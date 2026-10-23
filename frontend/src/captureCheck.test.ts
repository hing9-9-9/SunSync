import { test } from "node:test";
import assert from "node:assert/strict";
import { measurePixels, photoIssue, retakeMessages } from "./captureCheck.ts";

function image(width: number, height: number, pixel: (x: number, y: number) => number) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      const v = pixel(x, y), i = (y * width + x) * 4;
      data.set([v, v, v, 255], i);
    }
  return data;
}

test("a flat image is blurred and a checkerboard is sharp", () => {
  const flat = measurePixels(image(20, 20, () => 128), 20, 20);
  assert.equal(flat.sharpness, 0);
  assert.equal(Math.round(flat.brightness), 128);
  const sharp = measurePixels(image(20, 20, (x, y) => ((x + y) % 2 ? 230 : 30)), 20, 20);
  assert.ok(sharp.sharpness > 1000);
});

test("marker problems are reported before image quality", () => {
  assert.equal(photoIssue({ brightness: 10, sharpness: 0, marker: "partial" }), "partial");
  assert.equal(photoIssue({ brightness: 10, sharpness: 0, marker: "missing" }), "missing");
  assert.equal(photoIssue({ brightness: 120, sharpness: 5, marker: "visible" }), "blur");
  assert.equal(photoIssue({ brightness: 20, sharpness: 500, marker: "visible" }), "dark");
  assert.equal(photoIssue({ brightness: 250, sharpness: 500, marker: "visible" }), "bright");
  assert.equal(photoIssue({ brightness: 120, sharpness: 500, marker: "visible" }), null);
});

test("every issue has a retake instruction", () => {
  for (const issue of ["partial", "missing", "blur", "dark", "bright"] as const)
    assert.match(retakeMessages[issue], /다시 찍어 주세요/);
});
