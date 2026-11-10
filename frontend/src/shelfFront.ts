import { localFrontFace } from "./model.ts";
import type { Furniture, Wall } from "./model.ts";

/**
 * The open front of a bookcase in the 3D scene. The server protects the side of furniture that
 * looks at the window (backend/app/ui_scene.py protected_face; the scene mirrors it as WINDOW_FACE),
 * so the open front looks the same way. Without a window the front stays south, as before.
 * Plan furniture has no rotation, so the window wall alone decides.
 */
export type Front = { dx: number; dz: number; yaw: number };

/** Three.js axes (x east, z south). `yaw` turns a front built toward +z (south) about the y axis. */
const FRONT: Record<Wall, Front> = {
  south: { dx: 0, dz: 1, yaw: 0 },
  north: { dx: 0, dz: -1, yaw: Math.PI },
  east: { dx: 1, dz: 0, yaw: Math.PI / 2 },
  west: { dx: -1, dz: 0, yaw: -Math.PI / 2 },
};

export function shelfFront(wall: Wall | null | undefined, furniture?: Furniture): Front {
  if (furniture) {
    const face=localFrontFace(furniture,wall ?? "south");
    return {"남쪽 전면":FRONT.south,"북쪽 후면":FRONT.north,"동쪽 측면":FRONT.east,"서쪽 측면":FRONT.west,"상단":FRONT.south}[face];
  }
  return FRONT[wall ?? "south"] ?? FRONT.south;
}
