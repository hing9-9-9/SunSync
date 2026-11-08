import test from "node:test";
import assert from "node:assert/strict";
import {
  buildDemoNowcastComparison,
  computeX,
  computeY,
  buildQuantileBandPolygon,
  buildPolylinePoints,
  formatTimeLabel,
  DEFAULT_CHART_MARGIN,
} from "./spaceApi.ts";

test("buildDemoNowcastComparison produces valid structure and positive samples", () => {
  const demo = buildDemoNowcastComparison("2026-10-14T00:00:00Z");
  assert.equal(demo.rollout_mode, "active");
  assert.equal(demo.model_version, "fr01_kma_quantile_v1");
  assert.deepEqual(demo.points.map((point) => point.horizon_minutes), [10, 30, 60]);
});

test("quantile monotonicity: p10 <= p50 <= p90 across all demo points", () => {
  const demo = buildDemoNowcastComparison("2026-10-14T02:00:00Z");
  for (const pt of demo.points) {
    if (pt.p10_dni_W_m2 === null || pt.p50_dni_W_m2 === null || pt.p90_dni_W_m2 === null) continue;
    assert.ok(pt.p10_dni_W_m2 >= 0, `p10 must be non-negative: ${pt.p10_dni_W_m2}`);
    assert.ok(
      pt.p10_dni_W_m2 <= pt.p50_dni_W_m2,
      `p10 (${pt.p10_dni_W_m2}) must be <= p50 (${pt.p50_dni_W_m2})`,
    );
    assert.ok(
      pt.p50_dni_W_m2 <= pt.p90_dni_W_m2,
      `p50 (${pt.p50_dni_W_m2}) must be <= p90 (${pt.p90_dni_W_m2})`,
    );
  }
});

test("SVG coordinate helpers: computeX and computeY scale properly without NaN", () => {
  const margin = DEFAULT_CHART_MARGIN;
  const width = 500;
  const height = 200;

  const x0 = computeX(0, 10, width, margin);
  const x9 = computeX(9, 10, width, margin);
  assert.equal(x0, margin.left);
  assert.equal(x9, width - margin.right);
  assert.ok(x9 > x0);

  const yZero = computeY(0, 1000, height, margin);
  const yMax = computeY(1000, 1000, height, margin);
  assert.equal(yZero, height - margin.bottom);
  assert.equal(yMax, margin.top);
  assert.ok(yZero > yMax, "Higher irradiance values map to smaller Y in SVG");
});

test("buildQuantileBandPolygon generates closed non-empty coordinates without NaN", () => {
  const demo = buildDemoNowcastComparison("2026-10-14T02:00:00Z");
  const poly = buildQuantileBandPolygon(demo.points, 500, 200, 1000);
  assert.ok(poly.length > 0, "Polygon points string should not be empty");
  assert.ok(!poly.includes("NaN"), "Coordinates must not contain NaN");
  assert.ok(!poly.includes("undefined"), "Coordinates must not contain undefined");
  const pairs = poly.split(" ");
  assert.equal(pairs.length, 4, "Only the two AI horizons form the quantile polygon");
});

test("buildPolylinePoints generates sequential coordinates for valid series", () => {
  const demo = buildDemoNowcastComparison("2026-10-14T02:00:00Z");
  const line = buildPolylinePoints(
    demo.points.map((p) => p.p50_dni_W_m2),
    500,
    200,
    1000,
  );
  assert.ok(line.length > 0);
  assert.ok(!line.includes("NaN"));
  const pairs = line.split(" ");
  assert.equal(pairs.length, 2);
});

test("formatTimeLabel formats Korean time string with timezone", () => {
  const formatted = formatTimeLabel("2026-10-14T03:00:00Z", "Asia/Seoul");
  assert.match(formatted, /^\d{2}:\d{2}$/);
});
