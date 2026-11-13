import test from "node:test";
import assert from "node:assert/strict";
import { homeExampleSpace } from "./exampleSpace.ts";
import { estimatedSchedule } from "./sunEstimate.ts";
import { blockingSchedule, formatMinute } from "./sunlight.ts";
import type { Space } from "./model.ts";

const now = new Date("2026-11-13T03:00:00Z");
const graded = (): Space => {
  const s = homeExampleSpace();
  return { ...s, regions: s.regions.map((r) => ({ ...r, grade: "B" as const })) };
};
const show = (s: Space) => Object.fromEntries(s.regions.map((r) => [s.furniture.find((f) => f.id === r.furnitureId)!.name, (estimatedSchedule(s, now).get(r.id) ?? []).map((p) => `${formatMinute(p.arrival)}~${formatMinute(p.end)}`)]));

test("every graded furniture gets a sun time from the window geometry, not the demo shape", () => {
  const s = graded();
  const times = show(s);
  assert.ok(times["수납장 03"].length > 0, "the cabinet near the west wall is reached in the morning");
  assert.ok(times["열람 테이블 02"].length > 0);
  assert.notDeepEqual(estimatedSchedule(s, now), blockingSchedule(s));
});

test("without a window the old demo schedule is used", () => {
  const s = { ...graded(), windowDimensions: undefined };
  assert.deepEqual(estimatedSchedule(s, now), blockingSchedule(s));
});

test("furniture in front of the window shades what is behind it", () => {
  const s = graded();
  const open = estimatedSchedule(s, now).get(s.regions[2].id)!;
  const blocker = { ...s, furniture: [...s.furniture, { id: "wall", name: "가림", zone: s.zone, x: 0, y: 4.4, width: 8, depth: 0.3, height: 3 }] };
  const shaded = estimatedSchedule(blocker, now).get(s.regions[2].id)!;
  assert.ok(open.length > 0 && shaded.length === 0, `open=${open.length} shaded=${shaded.length}`);
});
