import test from "node:test";
import assert from "node:assert/strict";
import { shelfFront } from "./shelfFront.ts";
import { safeUtcOffsetHours, dateOfDayOfYear } from "./floorSun.ts";

// Unit vector of the window wall's outward direction in server ENU (x east, y north).
const OUTWARD = { south: [0, -1], north: [0, 1], east: [1, 0], west: [-1, 0] } as const;
// ENU (x, y) to three.js (x, z = -y).
const toThree = ([e, n]: readonly [number, number]) => [e, -n + 0];

test("shelf front looks at the window on every wall (ENU to three.js)", () => {
  for (const wall of ["south", "north", "east", "west"] as const) {
    const f = shelfFront(wall);
    const [x, z] = toThree(OUTWARD[wall]);
    assert.deepEqual([f.dx, f.dz], [x, z], wall);
    // yaw turns the +z front onto (dx, dz): (sin yaw, cos yaw)
    assert.ok(Math.abs(Math.sin(f.yaw) - f.dx) < 1e-9 && Math.abs(Math.cos(f.yaw) - f.dz) < 1e-9, `${wall} yaw`);
  }
});

test("a Seoul south-window shelf is unchanged: front +z, no turn", () => {
  assert.deepEqual(shelfFront("south"), { dx: 0, dz: 1, yaw: 0 });
});

test("without a window the front stays south", () => {
  assert.deepEqual(shelfFront(undefined), shelfFront("south"));
  assert.deepEqual(shelfFront(null), shelfFront("south"));
});

test("preview offset follows site.timezone for the date shown", () => {
  const at = (month: number, dom: number) => new Date(Date.UTC(2026, month - 1, dom, 12));
  assert.equal(safeUtcOffsetHours("Asia/Seoul", at(11, 8)), 9);
  assert.equal(safeUtcOffsetHours("Australia/Sydney", at(1, 15)), 11);
  assert.equal(safeUtcOffsetHours("Australia/Sydney", at(7, 15)), 10);
  assert.equal(safeUtcOffsetHours("Not/AZone", at(1, 15)), 9);
  assert.equal(safeUtcOffsetHours("", at(1, 15)), 9);
  assert.equal(safeUtcOffsetHours(undefined, at(1, 15)), 9);
});

test("day of year maps to the right date for DST", () => {
  assert.equal(safeUtcOffsetHours("Australia/Sydney", dateOfDayOfYear(2026, 15)), 11);
  assert.equal(safeUtcOffsetHours("Australia/Sydney", dateOfDayOfYear(2026, 196)), 10);
});
