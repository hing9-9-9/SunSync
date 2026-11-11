import test from "node:test";
import assert from "node:assert/strict";
import { shelfFront } from "./shelfFront.ts";
import { safeUtcOffsetHours, dateOfDayOfYear, createOffsetCache } from "./floorSun.ts";

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

test("the offset is looked up once per zone and day, not once per minute", () => {
  let calls = 0;
  const offsetFor = createOffsetCache((tz, date) => (calls++, safeUtcOffsetHours(tz, date)));
  for (let m = 0; m < 24 * 60; m++) assert.equal(offsetFor("Australia/Sydney", 2026, 15), 11);
  assert.equal(calls, 1);
  assert.equal(offsetFor("Australia/Sydney", 2026, 196), 10); // another day is its own entry (daylight saving)
  assert.equal(offsetFor("Asia/Seoul", 2026, 15), 9); // another zone too
  assert.equal(calls, 3);
});


test("rotated shelves choose the window-facing long side in world coordinates", () => {
  for(const width of [1,2]) for(const yaw of [15,30,90,180,215,270]) {
    const furniture={id:"s",name:"s",zone:"z",x:1,y:1,width,depth:1.5,height:2,yaw};
    const angle=yaw*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle);
    for(const wall of ["south","north","east","west"] as const) {
      const front=shelfFront(wall,furniture);
      const world=[c*front.dx+s*front.dz,-s*front.dx+c*front.dz];
      const target=toThree(OUTWARD[wall]);
      assert.ok(world[0]*target[0]+world[1]*target[1]>=-1e-10);
      assert.equal(width>=furniture.depth ? front.dx : front.dz,0);
    }
  }
});
