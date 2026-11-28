import assert from "node:assert/strict";
import test from "node:test";
import { exampleWindow, homeExampleSpace, refreshExample } from "./exampleSpace.ts";
import { validateSpace } from "./model.ts";

test("the example room is valid, has a full-wall window and a lived-in amount of furniture", () => {
  const room = homeExampleSpace();
  assert.deepEqual(room.windowDimensions, exampleWindow);
  assert.equal(room.windowDimensions!.width, room.width);
  assert.equal(room.furniture.length, 7);
  assert.equal(room.regions.length, 7);
  assert.equal(validateSpace(room), null);
});

test("an older example room is replaced by the current one, keeping its identity and settings", () => {
  assert.equal(refreshExample(homeExampleSpace()), null, "the current room is left alone");
  const full = homeExampleSpace();
  // An older example the person had edited: moved furniture, changed grades, a different window.
  const old = {
    ...full,
    id: "mine", serverSceneId: "scene-1", serverRevision: 21,
    shadingSchedule: { enabled: true, start: "09:00", end: "17:00" },
    windowDimensions: { width: 8, height: 2.8, wall: "south" as const, offset: 0, sill: 0, marker: undefined },
    furniture: full.furniture.slice(0, 3).map((f, i) => ({ ...f, x: f.x + i * 0.1, y: 0.6 })),
    regions: [] as typeof full.regions,
  };
  const up = refreshExample(old)!;
  assert.equal(up.id, "mine");
  assert.equal(up.serverSceneId, "scene-1");
  assert.equal(up.serverRevision, 21);
  assert.deepEqual(up.shadingSchedule, old.shadingSchedule);
  assert.equal(up.furniture.length, 7);
  assert.equal(up.regions.length, 7);
  assert.deepEqual(up.windowDimensions, exampleWindow);
  assert.equal(validateSpace(up), null);
  assert.equal(refreshExample({ ...old, name: "내 열람실" }), null, "a room that is not the example is never touched");
});
