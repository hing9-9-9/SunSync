// The estimate shown while a space is being edited (nothing is saved to the server yet): when the sun,
// coming through the window, can reach each protection area. It follows the same geometry as the 3D view
// (the side facing the window plus the top, furniture shadows included) but is a plain sampled check,
// not the server's FR-01 calculation, so the list calls it "간이 계산".
import { isWidthWall, localFrontFace, resolveWindow, topCompanions } from "./model.ts";
import type { Furniture, Region, Space } from "./model.ts";
import { ADVANCE_MINUTES, DAY_MINUTES, blockingSchedule, protectedRegions } from "./sunlight.ts";
import type { BlockingSchedule } from "./sunlight.ts";
import { safeUtcOffsetHours } from "./floorSun.ts";
import { sunPosition } from "./sun3d.ts";

type V = [number, number, number]; // x east, y south (plan), z up
const STEP = 10; // minutes between checks
const add = (a: V, b: V, k = 1): V => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
const dot = (a: V, b: V) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/** Turns a vector around the furniture's vertical axis (yaw is counterclockwise seen from above, y points south). */
const turn = ([x, y, z]: V, yaw: number): V => {
  const a = (yaw * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  return [x * c + y * s, -x * s + y * c, z];
};
const centreOf = (f: Furniture): V => [f.x + f.width / 2, f.y + f.depth / 2, 0];

type Surface = { corner: V; u: V; v: V; normal: V };
function surfaceOf(r: Region, face: Region["face"], lo: number, hi: number, host?: Furniture): Surface {
  const { x, y, width: w, depth: d } = r;
  const flat = {
    "남쪽 전면": { corner: [x, y + d, lo], u: [w, 0, 0], v: [0, 0, hi - lo], normal: [0, 1, 0] },
    "북쪽 후면": { corner: [x, y, lo], u: [w, 0, 0], v: [0, 0, hi - lo], normal: [0, -1, 0] },
    "동쪽 측면": { corner: [x + w, y, lo], u: [0, d, 0], v: [0, 0, hi - lo], normal: [1, 0, 0] },
    "서쪽 측면": { corner: [x, y, lo], u: [0, d, 0], v: [0, 0, hi - lo], normal: [-1, 0, 0] },
    "상단": { corner: [x, y, hi], u: [w, 0, 0], v: [0, d, 0], normal: [0, 0, 1] },
  }[face] as Surface;
  if (!host) return flat;
  const yaw = host.yaw ?? 0, c = centreOf(host);
  const corner = add(turn(add(flat.corner, c, -1), yaw), c);
  corner[2] += host.baseHeight ?? 0;
  return { corner, u: turn(flat.u, yaw), v: turn(flat.v, yaw), normal: turn(flat.normal, yaw) };
}

/** Does the ray from `p` along `dir` hit the furniture box? Slab test in the furniture's own frame. */
function hits(p: V, dir: V, f: Furniture): boolean {
  const yaw = -(f.yaw ?? 0), c = centreOf(f);
  const o = turn(add(p, c, -1), yaw), d = turn(dir, yaw);
  const lo: V = [-f.width / 2, -f.depth / 2, (f.baseHeight ?? 0)], hi: V = [f.width / 2, f.depth / 2, (f.baseHeight ?? 0) + f.height];
  let near = 0, far = Infinity;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-9) { if (o[i] < lo[i] || o[i] > hi[i]) return false; continue; }
    const a = (lo[i] - o[i]) / d[i], b = (hi[i] - o[i]) / d[i];
    near = Math.max(near, Math.min(a, b));
    far = Math.min(far, Math.max(a, b));
  }
  return near <= far;
}

function surfacesOf(space: Space, r: Region, wall: "south" | "north" | "east" | "west"): Surface[] {
  const host = r.furnitureId ? space.furniture.find((f) => f.id === r.furnitureId) : undefined;
  const side = host ? localFrontFace(host, wall) : r.face;
  const out = [surfaceOf(r, side, r.bottom, r.top, host)];
  if (host && topCompanions(space).includes(space.regions.indexOf(r))) out.push(surfaceOf(r, "상단", 0, host.height, host));
  return out;
}

/** Minutes of today when sunlight can reach each protection area of the (unsaved) space, in the shape the lists use. */
export function estimatedSchedule(space: Space, now = new Date()): BlockingSchedule {
  const win = resolveWindow(space);
  if (!win || !space.site) return blockingSchedule(space);
  const regions = protectedRegions(space);
  const samples = regions.map((r) => surfacesOf(space, r, win.wall).flatMap((s) => {
    const points: V[] = [];
    for (let i = 0; i < 5; i++) for (let j = 0; j < 3; j++) points.push(add(add(s.corner, s.u, (i + 0.5) / 5), s.v, (j + 0.5) / 3));
    return points.map((p) => ({ p: add(p, s.normal, 0.01), n: s.normal }));
  }));
  const { width: W, depth: D } = space;
  const wide = isWidthWall(win.wall);
  const along = (p: V) => (wide ? p[0] : p[1]);
  const day = Math.round((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(now.getFullYear(), 0, 0)) / 86400000);
  const offset = safeUtcOffsetHours(space.site.timezone, now);
  const wallAt = { south: D, north: 0, east: W, west: 0 }[win.wall];
  const axis = wide ? 1 : 0; // the coordinate that is constant on the window's wall
  const states = regions.map(() => ({ arrival: null as number | null }));
  const schedule: BlockingSchedule = new Map(regions.map((r) => [r.id, []]));
  const others = (id: string | null) => space.furniture.filter((f) => f.zone === space.zone && f.id !== id);
  for (let minute = 0; minute <= DAY_MINUTES; minute += STEP) {
    let sun: V | null = null;
    if (minute < DAY_MINUTES) {
      const pos = sunPosition(day, minute / 60, space.site.latitude, space.site.longitude, offset);
      if (pos.altitude > 1) {
        const a = (pos.altitude * Math.PI) / 180, z = (pos.azimuth * Math.PI) / 180;
        sun = [Math.cos(a) * Math.sin(z), -Math.cos(a) * Math.cos(z), Math.sin(a)]; // towards the sun
      }
    }
    regions.forEach((r, i) => {
      const lit = !!sun && samples[i].some(({ p, n }) => {
        if (dot(n, sun!) <= 0.02 || Math.abs(sun![axis]) < 1e-6) return false;
        const t = (wallAt - p[axis]) / sun![axis];
        if (t <= 0) return false; // the window wall is behind this point
        const hit = add(p, sun!, t);
        const along0 = along(hit);
        if (along0 < win.offset || along0 > win.offset + win.width || hit[2] < win.sill || hit[2] > win.sill + win.height) return false;
        return !others(r.furnitureId).some((f) => hits(p, sun!, f));
      });
      const st = states[i];
      if (lit && st.arrival === null) st.arrival = minute;
      if (!lit && st.arrival !== null) {
        const periods = schedule.get(r.id)!, start = Math.max(0, st.arrival - ADVANCE_MINUTES), last = periods.at(-1);
        if (last && start <= last.end) last.end = minute;
        else periods.push({ arrival: st.arrival, start, end: minute });
        st.arrival = null;
      }
    });
  }
  return schedule;
}
