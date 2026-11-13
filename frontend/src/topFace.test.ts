import test from "node:test";
import assert from "node:assert/strict";
import { regionOwners, topCompanions } from "./model.ts";
import { homeExampleSpace } from "./exampleSpace.ts";
import { scheduleFromPrediction } from "./sunlight.ts";
import { compactDay } from "./space3dServer.ts";
import type { ShadingPlan } from "./shading.ts";
import type { SunpathPrediction } from "./spaceApi.ts";

const space = homeExampleSpace(); // bookcase region is grade A on a furniture; the other two are grade D
const t = (h: number) => `2026-11-13T${String(h).padStart(2, "0")}:00:00+09:00`;
const entry = (h: number, exposed: boolean) => ({ timestamp: t(h), status: exposed ? ("exposed" as const) : ("not_exposed" as const), sample_count: 4, exposed_sample_count: exposed ? 4 : 0, max_direct_irradiance_W_m2: exposed ? 100 : null });
const region = (id: string, hours: number[]) => ({ region_id: id, timeline: [8, 9, 10, 11, 12].map((h) => entry(h, hours.includes(h))) });
const prediction = (regions: SunpathPrediction["regions"]): SunpathPrediction => ({ scene_revision: 1, start_time: t(8), regions });

test("only furniture regions of grade A to C that are not already the top get a top", () => {
  assert.deepEqual(topCompanions(space), [0]);
  const onTop = { ...space, regions: space.regions.map((r, i) => (i === 0 ? { ...r, face: "상단" as const } : r)) };
  assert.deepEqual(topCompanions(onTop), []);
  const free = { ...space, regions: space.regions.map((r, i) => (i === 0 ? { ...r, furnitureId: null } : r)) };
  assert.deepEqual(topCompanions(free), []);
  assert.deepEqual(topCompanions({ ...space, regions: space.regions.map((r) => ({ ...r, grade: "D" as const })) }), []);
});

test("server areas map back to their space region; a scene saved before the tops still maps", () => {
  assert.deepEqual(regionOwners(space, 4), [0, 1, 2, 0]);
  assert.deepEqual(regionOwners(space, 3), [0, 1, 2]);
  assert.equal(regionOwners(space, 5), null);
});

test("the light reaches the furniture when either its side or its top is exposed", () => {
  const side = region("side", [9]), other1 = region("o1", []), other2 = region("o2", []), top = region("top", [11, 12]);
  const periods = scheduleFromPrediction(space, prediction([side, other1, other2, top])).get(space.regions[0].id)!;
  const minutes = periods.map((p) => [p.arrival / 60, p.end / 60]);
  assert.equal(minutes.length, 2, "side at 9 and top at 11-12 are two separate periods");
  // the top alone, with the side shaded, still counts
  const topOnly = scheduleFromPrediction(space, prediction([region("side", []), other1, other2, region("top", [10])])).get(space.regions[0].id)!;
  assert.equal(topOnly.length, 1);
  // an old scene without tops keeps working
  assert.equal(scheduleFromPrediction(space, prediction([side, other1, other2].slice(0, 3))).get(space.regions[0].id)!.length, 1);
  assert.throws(() => scheduleFromPrediction(space, prediction([side, other1])), /do not match/);
});

test("the 3D day folds the top into its furniture: strongest exposure, and a cell asked for twice counts once", () => {
  const sunpath = { ...prediction([region("side", [9]), region("o1", []), region("o2", []), region("top", [9, 10])]), solar_positions: [] };
  const plan: ShadingPlan = {
    plan_version: "0.1.0", scene_id: "s", scene_revision: 1, start_time: t(8), end_time: t(13),
    windows: [{ window_id: "w", film_rows: 1, film_cols: 1, cells: [{ row: 0, col: 0, segments: [{ start_time: t(9), end_time: t(10), state: "opaque", reasons: [{ region_id: "side", grade: "A" }, { region_id: "top", grade: "A" }] }] }] }],
  };
  const day = compactDay(sunpath, [plan], 0, regionOwners(space, 4));
  assert.equal(day.exposure.length, 3);
  assert.deepEqual(day.exposure[0].share, [0, 1, 1, 0, 0]);
  assert.deepEqual(day.exposure[0].maxW, [null, 100, 100, null, null]);
  assert.deepEqual(day.segments[0].regions, [0]);
});
