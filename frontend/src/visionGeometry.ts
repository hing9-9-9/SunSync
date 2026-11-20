import { localFrontFace } from "./model.ts";
import type { Face, Furniture, Wall } from "./model.ts";
import type { VisionEstimatedItem } from "./measureApi.ts";

export type PreviewBox = {
  x: number; y: number; width: number; depth: number; // unrotated box in plan metres (y grows south)
  yaw: number; // degrees counterclockwise from east, as the server stores it
  face: Face; // the side that faces the window
  protectedFace: boolean; // grade A–C on a side region: the window-facing side is protected
  protectedTop: boolean; // grade A–C: the top is protected (a side region also protects its top, decided 2026-11-13)
};

/**
 * Server scene frame (ENU, origin at the room's south-west corner, y north) to plan frame (y south):
 * plan y = room depth - north. Same mapping as ui_scene.py, so a box lands where it will be saved.
 */
export function previewBox(item: VisionEstimatedItem, depth: number, wall: Wall): PreviewBox {
  const [width, d] = item.occluder.dimensions_m;
  const [cx, cy] = item.occluder.base_center_m;
  const yaw = item.occluder.yaw_deg_ccw_from_east;
  // A region the server puts on top of the furniture (a desk surface) stays top-only once saved; any side region becomes the window-facing side plus the top.
  const guarded = !!item.protected_region && item.protected_region.grade !== "D";
  const topRegion = Math.abs(item.protected_region?.receiving_normal?.[2] ?? 0) > 0.7;
  const px = cx - width / 2, py = depth - cy - d / 2;
  const asFurniture: Furniture = { id: item.occluder.occluder_id, name: item.occluder.name, zone: "", x: px, y: py, width, depth: d, height: item.occluder.dimensions_m[2], yaw };
  return {
    x: px, y: py, width, depth: d, yaw,
    face: localFrontFace(asFurniture, wall),
    protectedFace: guarded && !topRegion,
    protectedTop: guarded,
  };
}

/** The protected side as a line on the unrotated box. */
export function faceSegment(b: Pick<PreviewBox, "x" | "y" | "width" | "depth" | "face">) {
  const { x, y, width: w, depth: d } = b;
  switch (b.face) {
    case "북쪽 후면": return { x1: x, y1: y, x2: x + w, y2: y };
    case "동쪽 측면": return { x1: x + w, y1: y, x2: x + w, y2: y + d };
    case "서쪽 측면": return { x1: x, y1: y, x2: x, y2: y + d };
    default: return { x1: x, y1: y + d, x2: x + w, y2: y + d };
  }
}

