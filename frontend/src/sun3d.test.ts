import test from "node:test";
import assert from "node:assert/strict";
import { dayOfYear, sunPosition } from "./sun3d.ts";

const seoul = (month: number, day: number, hour: number) => sunPosition(dayOfYear(month, day), hour, 37.57, 126.98, 9);

test("Seoul solar noon altitude matches the season", () => {
  assert.ok(Math.abs(seoul(6, 21, 12.55).altitude - 75.9) < 1.5);
  assert.ok(Math.abs(seoul(12, 21, 12.55).altitude - 29) < 1.5);
});

test("sun rises in the east and sets in the west", () => {
  assert.ok(Math.abs(seoul(12, 21, 12.55).azimuth - 180) < 3);
  const morning = seoul(3, 20, 8), evening = seoul(3, 20, 17);
  assert.ok(morning.azimuth > 90 && morning.azimuth < 135);
  assert.ok(evening.azimuth > 225 && evening.azimuth < 270);
  assert.ok(seoul(12, 21, 6).altitude < 0);
});
