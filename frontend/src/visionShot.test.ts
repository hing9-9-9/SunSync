import test from "node:test";
import assert from "node:assert/strict";
import { pickBestFrame } from "./visionShot.ts";

const f = (markers: number, sharpness: number, brightness = 120) => ({ markers, sharpness, brightness });

test("no frames means no pick", () => {
  assert.equal(pickBestFrame([]), -1);
});

test("a frame with one marker beats a sharper frame without it", () => {
  assert.equal(pickBestFrame([f(0, 500), f(1, 100), f(0, 400)]), 1);
});

test("among usable frames the sharpest wins", () => {
  assert.equal(pickBestFrame([f(1, 150), f(1, 300), f(1, 200)]), 1);
});

test("a frame without photo issues beats a blurry one that still has the marker", () => {
  assert.equal(pickBestFrame([f(1, 30), f(1, 90)]), 1);
  assert.equal(pickBestFrame([f(1, 90), f(1, 400, 10)]), 0); // too dark counts as an issue too
});

test("ties go to the later frame", () => {
  assert.equal(pickBestFrame([f(1, 100), f(1, 100)]), 1);
});

test("with no marker in any frame it still returns the sharpest so the server can say why", () => {
  assert.equal(pickBestFrame([f(0, 80), f(2, 200), f(0, 120)]), 1);
});
