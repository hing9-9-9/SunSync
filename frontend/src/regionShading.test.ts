import test from "node:test";
import assert from "node:assert/strict";
import { filmStatesAt } from "./shading.ts";
import type { ShadingPlan } from "./shading.ts";
import { cellsAt } from "./space3dServer.ts";
import type { ServerDay } from "./space3dServer.ts";
import { regionShading, validateSpace, withinShading } from "./model.ts";
import { homeExampleSpace } from "./exampleSpace.ts";

const day = "2026-11-13";
const seg = (regions: string[]) => [{ start_time: `${day}T00:00:00+09:00`, end_time: `2026-11-14T00:00:00+09:00`, state: "opaque" as const, reasons: regions.map((id) => ({ region_id: id, grade: "A" as const })) }];
const plan: ShadingPlan = {
  plan_version: "0.1.0", scene_id: "s", scene_revision: 1, start_time: `${day}T00:00:00+09:00`, end_time: `2026-11-14T00:00:00+09:00`,
  windows: [{ window_id: "w", film_rows: 1, film_cols: 3, cells: [
    { row: 0, col: 0, segments: seg(["a"]) }, { row: 0, col: 1, segments: seg(["b"]) }, { row: 0, col: 2, segments: seg(["a", "b"]) },
  ] }],
};
const space = { enabled: true, start: "12:00", end: "15:00" };
const own = (id: string) => (id === "a" ? { enabled: true, start: "11:00", end: "16:00" } : undefined);
const closed = (hour: number, regionTime?: typeof own) => [...filmStatesAt(plan, hour, space, regionTime).keys()].sort();

test("space time 12-15 and one furniture 11-16: each cell follows the furniture that asked for it", () => {
  assert.deepEqual(closed(11.5, own), [0, 2], "only the furniture with its own time, and the cell it shares");
  assert.deepEqual(closed(13, own), [0, 1, 2], "inside both times");
  assert.deepEqual(closed(15.5, own), [0, 2], "only the furniture with its own time");
  assert.deepEqual(closed(17, own), [], "outside both");
});

test("without a furniture's own time the space's time decides, as before", () => {
  assert.deepEqual(closed(11.5), []);
  assert.deepEqual(closed(13), [0, 1, 2]);
  assert.deepEqual(filmStatesAt(plan, 13, { enabled: false, start: "12:00", end: "15:00" }).size, 0);
});

test("3D cell gating follows the same rule", () => {
  const at = Date.parse(`${day}T11:30:00+09:00`);
  const d = { rows: 1, cols: 3, segments: [{ cell: 0, from: 0, to: Infinity, state: "opaque", regions: [0] }, { cell: 1, from: 0, to: Infinity, state: "opaque", regions: [1] }] } as unknown as ServerDay;
  const gate = (i: number) => withinShading(i === 0 ? own("a") : space, 11.5 * 60);
  const c = cellsAt(d, at, 2, gate, false);
  assert.ok(c.states[0] > 0 && c.states[1] === 0);
  assert.deepEqual(c.byRegion, [1, 0]);
});

test("a furniture's own time is validated and falls back to the space's", () => {
  const s = homeExampleSpace();
  assert.equal(regionShading({ shadingSchedule: space }, {}).start, "12:00");
  assert.equal(regionShading({ shadingSchedule: space }, { shadingSchedule: own("a")! }).start, "11:00");
  const region = s.regions[0];
  assert.equal(validateSpace({ ...s, regions: [{ ...region, shadingSchedule: own("a")! }] }), null);
  assert.match(validateSpace({ ...s, regions: [{ ...region, shadingSchedule: { enabled: true, start: "16:00", end: "11:00" } }] }) ?? "", new RegExp(region.name));
});
