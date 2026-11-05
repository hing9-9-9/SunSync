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
  assert.equal(demo.mode, "kma_ai_ensemble");
  assert.equal(demo.model_version, "kma_openmeteo_quantile_nowcast_v1");
  assert.ok(demo.points.length > 10, "Should generate a multi-step timeline");
  assert.ok(demo.metrics.sample_count === demo.points.length);
  assert.ok(demo.metrics.mean_uncertainty_spread_W_m2 > 0);
  assert.ok(demo.metrics.cloud_volatility_index >= 0 && demo.metrics.cloud_volatility_index <= 1);
});

test("quantile monotonicity: p10 <= p50 <= p90 across all demo points", () => {
  const demo = buildDemoNowcastComparison("2026-10-14T02:00:00Z");
  for (const pt of demo.points) {
    assert.ok(pt.dni_p10_W_m2 >= 0, `p10 must be non-negative: ${pt.dni_p10_W_m2}`);
    assert.ok(
      pt.dni_p10_W_m2 <= pt.dni_p50_W_m2,
      `p10 (${pt.dni_p10_W_m2}) must be <= p50 (${pt.dni_p50_W_m2})`,
    );
    assert.ok(
      pt.dni_p50_W_m2 <= pt.dni_p90_W_m2,
      `p50 (${pt.dni_p50_W_m2}) must be <= p90 (${pt.dni_p90_W_m2})`,
    );
    assert.ok(
      pt.uncertainty_spread_W_m2 === pt.dni_p90_W_m2 - pt.dni_p10_W_m2,
      "uncertainty spread must equal p90 - p10",
    );
    assert.ok(
      pt.dni_p90_W_m2 <= pt.clear_sky_dni_W_m2 * 1.05 + 1.0,
      `p90 must not exceed clear sky upper bound: ${pt.dni_p90_W_m2} vs ${pt.clear_sky_dni_W_m2}`,
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
  assert.equal(pairs.length, demo.points.length * 2, "Polygon should contain 2 * N coordinate pairs");
});

test("buildPolylinePoints generates sequential coordinates for valid series", () => {
  const demo = buildDemoNowcastComparison("2026-10-14T02:00:00Z");
  const line = buildPolylinePoints(
    demo.points.map((p) => p.dni_p50_W_m2),
    500,
    200,
    1000,
  );
  assert.ok(line.length > 0);
  assert.ok(!line.includes("NaN"));
  const pairs = line.split(" ");
  assert.equal(pairs.length, demo.points.length);
});

test("formatTimeLabel formats Korean time string with timezone", () => {
  const formatted = formatTimeLabel("2026-10-14T03:00:00Z", "Asia/Seoul");
  assert.match(formatted, /^\d{2}:\d{2}$/);
});
