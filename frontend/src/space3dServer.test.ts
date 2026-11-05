import test from "node:test";
import assert from "node:assert/strict";
import { cellsAt, compactDay, sampleIndex, sunAt } from "./space3dServer.ts";
import type { ShadingPlan } from "./shading.ts";
import type { SunpathPrediction } from "./spaceApi.ts";

const t = (h: number) => `2026-11-05T0${h}:00:00Z`;
const sunpath: SunpathPrediction = {
  scene_revision: 1,
  start_time: t(0),
  solar_positions: [{ timestamp: t(0), sun_direction_enu: [0, -1, 0] }, { timestamp: t(1), sun_direction_enu: [0, -1, 1] }],
  regions: [
    { region_id: "ra", timeline: [{ timestamp: t(0), status: "exposed", exposed_sample_count: 1, samples: [{ direct_irradiance_W_m2: 40 }, { direct_irradiance_W_m2: 0 }] }, { timestamp: t(1), status: "not_exposed", exposed_sample_count: 0, samples: [{ direct_irradiance_W_m2: null }, { direct_irradiance_W_m2: 0 }] }] },
    { region_id: "rb", timeline: [] },
  ],
};
const plan: ShadingPlan = {
  plan_version: "0.1.0", scene_id: "s", scene_revision: 1, start_time: t(0), end_time: t(2),
  windows: [{ window_id: "w", film_rows: 2, film_cols: 3, cells: [
    { row: 1, col: 2, segments: [
      { start_time: t(0), end_time: t(1), state: "opaque", reasons: [{ region_id: "ra", grade: "A" }] },
      { start_time: t(1), end_time: t(2), state: "unknown", reasons: [{ region_id: "rb", grade: "B" }] },
    ] },
  ] }],
};

test("server results shrink to exposure share, peak irradiance and closed segments", () => {
  const day = compactDay(sunpath, [plan], 12);
  assert.deepEqual(day.exposure[0], { share: [0.5, 0], maxW: [40, null] });
  assert.equal(day.segments.length, 2);
  assert.equal(day.segments[0].cell, 5);
});

test("cell states follow [start, end) segments and count the asking regions", () => {
  const day = compactDay(sunpath, [plan], 0);
  const first = cellsAt(day, Date.parse(t(0)), 2), edge = cellsAt(day, Date.parse(t(1)), 2);
  assert.equal(first.states[5], 1);
  assert.deepEqual(first.byRegion, [1, 0]);
  assert.equal(edge.states[5], 3);
  assert.deepEqual(edge.byRegion, [0, 1]);
  assert.equal(cellsAt(day, Date.parse(t(2)), 2).states[5], 0);
});

test("sun direction blends between samples and holds after the last one", () => {
  const day = compactDay(sunpath, [plan], 0);
  assert.equal(sampleIndex(day, Date.parse(t(0)) - 1), -1);
  assert.deepEqual(sunAt(day, Date.parse("2026-11-05T00:30:00Z")), [0, -1, 0.5]);
  assert.deepEqual(sunAt(day, Date.parse(t(3))), [0, -1, 1]);
});
