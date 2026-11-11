import test from "node:test";
import assert from "node:assert/strict";
import { liveHint, pointFailure } from "./captureGuide.ts";

const box = (x0: number, y0: number, x1: number, y1: number): [number, number][] => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const seen = (corners: [number, number][][], extra = {}) => ({ detections: corners.map((c) => ({ corners_px: c })), width: 1920, height: 1080, ...extra });

test("live hint says what is wrong with the marker, most important first", () => {
  assert.deepEqual([liveHint(seen([])).tone, liveHint(seen([])).text], ["stop", "마커가 인식되고 있지 않아요. 창문 필름의 마커 전체가 화면에 들어오게 해 주세요."]);
  assert.equal(liveHint(seen([box(100, 100, 400, 400), box(600, 100, 900, 400)])).tone, "stop");
  assert.match(liveHint(seen([box(0, 100, 400, 500)])).text, /잘려요/);
  assert.match(liveHint(seen([box(900, 400, 1000, 500)])).text, /너무 작게/);
  assert.match(liveHint(seen([box(800, 300, 1200, 700)]), { brightness: 20, sharpness: 200 }).text, /어두워요/);
  assert.match(liveHint(seen([box(800, 300, 1200, 700)], { pose_status: "unstable" })).text, /불안정/);
  assert.equal(liveHint(seen([box(800, 300, 1200, 700)], { pose_status: "ok" }), { brightness: 120, sharpness: 200 }).tone, "ok");
});

test("a rejected point names itself and the reason", () => {
  assert.match(pointFailure("P3", { quality: { reason: "inconsistent_samples" } }), /^P3 · .*어긋나요/);
  assert.match(pointFailure("P2", { quality: { reason: "insufficient_samples" }, diagnostics: [{ status: "pose_uncertainty_too_large" }, { status: "pose_uncertainty_too_large" }, { status: "ok" }] }), /^P2 · .*비스듬/);
  assert.match(pointFailure("P1", { quality: { reason: "insufficient_samples" }, diagnostics: [{ status: "ok" }] }), /^P1 · .*부족해요/);
});
