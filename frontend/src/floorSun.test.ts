import test from "node:test";
import assert from "node:assert/strict";
import { floorSunlight, utcOffsetHours } from "./floorSun.ts";
import { blankSpace } from "./model.ts";

const room = (wall: "south" | "north") => ({
  ...blankSpace(), width: 8, depth: 6, height: 3,
  site: { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true },
  windowDimensions: { width: 2, height: 1.5, wall, offset: 3, sill: 0.9 },
  furniture: [],
});
const day = new Date("2026-11-06T03:00:00Z");

test("Seoul is UTC+9", () => assert.equal(utcOffsetHours("Asia/Seoul", day), 9));

test("a south window at noon lights the floor north of it, starting near the window", () => {
  const sun = floorSunlight(room("south"), 12, day)!;
  assert.ok(sun.patch.length >= 4);
  const ys = sun.patch.map((p) => p.y);
  // Plan y grows south; the patch lies inside the room and away from the wall (y < depth).
  assert.ok(Math.min(...ys) >= 0 && Math.max(...ys) < 6);
  assert.ok(Math.max(...ys) > 3, "near part of the patch is in the south half");
});

test("no patch through a north window in the northern-hemisphere sun, or at night", () => {
  assert.equal(floorSunlight(room("north"), 12, day), null);
  assert.equal(floorSunlight(room("south"), 22, day), null);
});

test("furniture throws a shadow away from the sun", () => {
  const space = { ...room("south"), furniture: [{ id: "f", name: "책장", zone: blankSpace().zone, type: "bookcase" as const, x: 3.5, y: 2, width: 1, depth: 0.4, height: 2 }] };
  const [shadow] = floorSunlight(space, 12, day)!.shadows;
  // Noon sun from the south: the shadow reaches further north (smaller plan y) than the furniture.
  assert.ok(Math.min(...shadow.map((p) => p.y)) < 2);
});
