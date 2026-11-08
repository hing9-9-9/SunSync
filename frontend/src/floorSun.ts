// Sunlight on the floor of the 2D plan: the window cast along the sun's direction, minus the
// shadows furniture throws. A plain geometric drawing for the plan (no reflections or film).
import { resolveWindow } from "./model.ts";
import type { Furniture, Space } from "./model.ts";
import { sunPosition } from "./sun3d.ts";

type Point = { x: number; y: number };
type V3 = [number, number, number];

/** Hours from UTC for a zone on a date, e.g. Asia/Seoul → 9. */
export function utcOffsetHours(timeZone: string, date: Date): number {
  const text = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "longOffset" })
    .formatToParts(date).find((p) => p.type === "timeZoneName")?.value ?? "GMT";
  const m = text.match(/GMT([+-])(\d{2}):?(\d{2})?/);
  return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) + Number(m[3] ?? 0) / 60) : 0;
}
const dayOfYearOf = (d: Date) => Math.round((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(d.getFullYear(), 0, 0)) / 86400000);

// Convex hull (monotone chain); shadows are hulls of a box's foot and its projected top.
function hull(points: Point[]): Point[] {
  const p = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o: Point, a: Point, b: Point) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: Point[] = [], upper: Point[] = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower.at(-2)!, lower.at(-1)!, q) <= 0) lower.pop(); lower.push(q); }
  for (const q of [...p].reverse()) { while (upper.length >= 2 && cross(upper.at(-2)!, upper.at(-1)!, q) <= 0) upper.pop(); upper.push(q); }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}
// Sutherland–Hodgman against the room rectangle.
function clip(poly: Point[], w: number, h: number): Point[] {
  const edges: [(p: Point) => boolean, (a: Point, b: Point) => Point][] = [
    [(p) => p.x >= 0, (a, b) => ({ x: 0, y: a.y + ((b.y - a.y) * (0 - a.x)) / (b.x - a.x) })],
    [(p) => p.x <= w, (a, b) => ({ x: w, y: a.y + ((b.y - a.y) * (w - a.x)) / (b.x - a.x) })],
    [(p) => p.y >= 0, (a, b) => ({ x: a.x + ((b.x - a.x) * (0 - a.y)) / (b.y - a.y), y: 0 })],
    [(p) => p.y <= h, (a, b) => ({ x: a.x + ((b.x - a.x) * (h - a.y)) / (b.y - a.y), y: h })],
  ];
  let out = poly;
  for (const [inside, cut] of edges) {
    const input = out; out = [];
    input.forEach((p, i) => {
      const q = input[(i + 1) % input.length];
      if (inside(p)) { out.push(p); if (!inside(q)) out.push(cut(p, q)); } else if (inside(q)) out.push(cut(p, q));
    });
    if (!out.length) break;
  }
  return out;
}

/**
 * Floor patch and furniture shadows in plan coordinates (x east, y down = south), or null when
 * the sun is down or behind the window's wall. ENU room frame as backend/app/ui_scene.py: south wall at y = 0.
 */
export function floorSunlight(space: Space, hour: number, date: Date = new Date()) {
  const w = resolveWindow(space);
  if (!w || !space.site) return null;
  const sun = sunPosition(dayOfYearOf(date), hour, space.site.latitude, space.site.longitude, utcOffsetHours(space.site.timezone, date));
  if (sun.altitude <= 1) return null;
  const alt = (sun.altitude * Math.PI) / 180, az = (sun.azimuth * Math.PI) / 180;
  const d: V3 = [-Math.sin(az) * Math.cos(alt), -Math.cos(az) * Math.cos(alt), -Math.sin(alt)]; // light travels away from the sun
  const { width: W, depth: D } = space;
  const o = w.offset, z0 = w.sill, z1 = w.sill + w.height, L = w.width;
  // Window bottom/top edge end points and the inward normal of its wall.
  const frames: Record<string, { a: [number, number]; b: [number, number]; n: [number, number] }> = {
    south: { a: [o, 0], b: [o + L, 0], n: [0, 1] },
    north: { a: [o, D], b: [o + L, D], n: [0, -1] },
    east: { a: [W, D - o - L], b: [W, D - o], n: [-1, 0] },
    west: { a: [0, D - o - L], b: [0, D - o], n: [1, 0] },
  };
  const f = frames[w.wall];
  if (d[0] * f.n[0] + d[1] * f.n[1] <= 0.01) return null;
  const toFloor = ([x, y, z]: V3): Point => { const t = -z / d[2]; return { x: x + t * d[0], y: y + t * d[1] }; };
  const enu = [toFloor([...f.a, z0]), toFloor([...f.b, z0]), toFloor([...f.b, z1]), toFloor([...f.a, z1])];
  const toPlan = (p: Point): Point => ({ x: p.x, y: D - p.y });
  const patch = clip(enu, W, D).map(toPlan);
  if (patch.length < 3) return null;
  const shadow = (fu: Furniture): Point[] => {
    const x0 = fu.x, x1 = fu.x + fu.width, n0 = D - (fu.y + fu.depth), n1 = D - fu.y;
    const foot: V3[] = [[x0, n0, 0], [x1, n0, 0], [x1, n1, 0], [x0, n1, 0]];
    const top = foot.map(([x, y]) => toFloor([x, y, fu.height] as V3));
    return hull([...foot.map(([x, y]) => ({ x, y })), ...top]).map(toPlan);
  };
  return { patch, shadows: space.furniture.filter((fu) => fu.zone === space.zone).map(shadow), altitude: sun.altitude };
}
