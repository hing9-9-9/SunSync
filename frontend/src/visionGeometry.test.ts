import test from "node:test";
import assert from "node:assert/strict";
import { faceSegment, previewBox } from "./visionGeometry.ts";
import type { VisionEstimatedItem } from "./measureApi.ts";

const item = (over: Partial<VisionEstimatedItem> = {}, occ: Partial<VisionEstimatedItem["occluder"]> = {}): VisionEstimatedItem => ({
  occluder: { occluder_id: "o1", room_id: "r", name: "책장", type: "bookshelf", base_center_m: [2, 1, 0], dimensions_m: [1.2, 0.4, 1.8], yaw_deg_ccw_from_east: 0, ...occ },
  protected_region: { region_id: "p1", grade: "B" },
  confidence: 0.8, calibrated_distance_m: 2, clamped_to_room: false, ...over,
});

test("previewBox flips north to plan y, like the saved space", () => {
  const b = previewBox(item(), 6, "south");
  assert.ok(Math.abs(b.x - 1.4) < 1e-9);
  assert.ok(Math.abs(b.y - (6 - 1 - 0.2)) < 1e-9);
});

test("previewBox protects the side that faces the window", () => {
  assert.equal(previewBox(item(), 6, "south").face, "남쪽 전면");
  assert.equal(previewBox(item(), 6, "north").face, "북쪽 후면");
});

test("grade D or a missing region has no protected face", () => {
  assert.equal(previewBox(item({ protected_region: { region_id: "p", grade: "D" } }), 6, "south").protectedFace, false);
  assert.equal(previewBox(item({ protected_region: null }), 6, "south").protectedFace, false);
  assert.equal(previewBox(item(), 6, "south").protectedFace, true);
  assert.equal(previewBox(item({ protected_region: { region_id: "p", grade: "D" } }), 6, "south").protectedTop, false);
});

test("a side region protects the window-facing side and the top; a top region (desk surface) protects only the top", () => {
  const side = previewBox(item({ protected_region: { region_id: "p", grade: "B", receiving_normal: [0, -1, 0] } }), 6, "north");
  assert.equal(side.protectedFace, true);
  assert.equal(side.protectedTop, true);
  const top = previewBox(item({ protected_region: { region_id: "p", grade: "C", receiving_normal: [0, 0, 1] } }), 6, "north");
  assert.equal(top.protectedFace, false);
  assert.equal(top.protectedTop, true);
});

test("faceSegment runs along the chosen side of the unrotated box", () => {
  const base = { x: 1, y: 2, width: 3, depth: 1 };
  assert.deepEqual(faceSegment({ ...base, face: "남쪽 전면" }), { x1: 1, y1: 3, x2: 4, y2: 3 });
  assert.deepEqual(faceSegment({ ...base, face: "북쪽 후면" }), { x1: 1, y1: 2, x2: 4, y2: 2 });
  assert.deepEqual(faceSegment({ ...base, face: "동쪽 측면" }), { x1: 4, y1: 2, x2: 4, y2: 3 });
  assert.deepEqual(faceSegment({ ...base, face: "서쪽 측면" }), { x1: 1, y1: 2, x2: 1, y2: 3 });
});
