import test from "node:test";
import assert from "node:assert/strict";
import { blankSpace } from "./model.ts";
import type { Region } from "./model.ts";
import { ADVANCE_MINUTES, predictionDay, scheduleFromPrediction } from "./sunlight.ts";
import { predictSunpath } from "./spaceApi.ts";

const region = (id: string): Region => ({ id, name: id, zone: "구역 01", furnitureId: null, x: 1, y: 1, width: 1, depth: 1, bottom: 0, top: 1, face: "상단", grade: "A" });
const start = "2026-10-24T00:00:00+09:00";
const at = (minute: number, exposed: boolean) => ({
  timestamp: new Date(Date.parse(start) + minute * 60000).toISOString(),
  status: exposed ? ("exposed" as const) : ("not_exposed" as const),
});

test("prediction day is local midnight with the zone offset", () => {
  const day = predictionDay("Asia/Seoul", new Date("2026-10-24T05:00:00Z"));
  assert.equal(day.start, "2026-10-24T00:00:00+09:00");
  assert.equal(day.label, "10월 24일 기준");
});

test("exposed samples become blocking periods per region, in saved order", () => {
  const space = { ...blankSpace(), regions: [region("a"), region("b")] };
  const schedule = scheduleFromPrediction(space, {
    scene_revision: 3,
    start_time: start,
    regions: [
      { region_id: "x", timeline: [at(600, false), at(605, true), at(610, true), at(615, false)] },
      { region_id: "y", timeline: [at(600, false), at(605, false)] },
    ],
  });
  assert.deepEqual(schedule.get("a"), [{ arrival: 605, start: 605 - ADVANCE_MINUTES, end: 615 }]);
  assert.deepEqual(schedule.get("b"), []);
});

test("a region still lit at the end of the day closes at midnight", () => {
  const space = { ...blankSpace(), regions: [region("a")] };
  const schedule = scheduleFromPrediction(space, { scene_revision: 1, start_time: start, regions: [{ region_id: "x", timeline: [at(1435, true)] }] });
  assert.equal(schedule.get("a")?.[0].end, 1440);
});

test("mismatched regions are rejected instead of shown on the wrong area", () => {
  const space = { ...blankSpace(), regions: [region("a")] };
  assert.throws(() => scheduleFromPrediction(space, { scene_revision: 1, start_time: start, regions: [] }));
});

test("prediction request pins the saved revision", async (t) => {
  let body: Record<string, unknown> = {};
  let url = "";
  t.mock.method(globalThis, "fetch", async (u: string, init?: RequestInit) => {
    url = u;
    body = JSON.parse(init?.body as string);
    return new Response(JSON.stringify({ scene_revision: 4, start_time: start, regions: [] }), { status: 200 });
  });
  await predictSunpath("scene-1", 4, start);
  assert.match(url, /\/api\/scenes\/scene-1\/sunpath-predictions$/);
  assert.equal(body.expected_scene_revision, 4);
  assert.equal(body.duration_minutes, 1440);
});
