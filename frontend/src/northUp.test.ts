import test from "node:test";
import assert from "node:assert/strict";
import { floorSunlight } from "./floorSun.ts";
import { blankSpace, validateSpace } from "./model.ts";
import { homeExampleSpace } from "./exampleSpace.ts";

// The plan's top is north whichever wall the window is on: the sun side decides where light enters.
const room = (wall: "south" | "north" | "east" | "west") => ({
  ...blankSpace(), width: 8, depth: 6, height: 3,
  site: { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true },
  windowDimensions: { width: 2, height: 1.5, wall, offset: 2, sill: 0.9 },
  furniture: [],
});
const day = new Date("2026-11-06T03:00:00Z");

test("an east window is lit in the morning and a west window in the afternoon, not the other way round", () => {
  const east = floorSunlight(room("east"), 9, day)!;
  assert.ok(east && east.patch.length >= 3, "east window, 9:00");
  assert.ok(Math.max(...east.patch.map((p) => p.x)) <= 8 + 1e-6 && Math.min(...east.patch.map((p) => p.x)) >= 0, "patch stays in the room");
  assert.equal(floorSunlight(room("east"), 16, day), null);
  const west = floorSunlight(room("west"), 16, day)!;
  assert.ok(west && west.patch.length >= 3, "west window, 16:00");
  assert.equal(floorSunlight(room("west"), 9, day), null);
});

test("the example room given to a new account is a valid space", () => {
  const space = homeExampleSpace();
  assert.equal(validateSpace(space), null);
  assert.equal(space.site?.northConfirmed, true);
  assert.equal(space.furniture.length, 3);
});
