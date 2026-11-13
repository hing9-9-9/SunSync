import { filmGrid, isWidthWall, regionOwners, resolveWindow, timeToMinute, wallLength } from "./model.ts";
import type { Region, ShadingTime, Space } from "./model.ts";
import type { SunpathPrediction } from "./spaceApi.ts";
type Point = { x: number; y: number };

// Shared by the drawing and the preview schedule; replace both together with FR-01 output.
export function sunlightPolygon(space: Pick<Space, "width" | "depth">, hour: number): Point[] {
  if (hour < 6 || hour >= 18) return [];
  const x = space.width * (0.25 + (hour - 9) * 0.025);
  const y = space.depth * 0.35;
  const width = space.width * 0.5;
  const height = space.depth * 0.65;
  return [[1 / 3, 0], [1, 0.18], [329.28 / 399.84, 1], [0, 1]].map(([u, v]) => ({ x: x + u * width, y: y + v * height }));
}

// Separating-axis test includes boundary contact, so blocking starts at first contact.
export function polygonsTouch(a: Point[], b: Point[]): boolean {
  if (a.length < 3 || b.length < 3) return false;
  for (const polygon of [a, b]) {
    for (let i = 0; i < polygon.length; i++) {
      const p = polygon[i], q = polygon[(i + 1) % polygon.length];
      const axis = { x: -(q.y - p.y), y: q.x - p.x };
      const pa = a.map(v => v.x * axis.x + v.y * axis.y);
      const pb = b.map(v => v.x * axis.x + v.y * axis.y);
      if (Math.max(...pa) < Math.min(...pb) - 1e-9 || Math.max(...pb) < Math.min(...pa) - 1e-9) return false;
    }
  }
  return true;
}
const footprint = (r: Region): Point[] => [
  { x: r.x, y: r.y }, { x: r.x + r.width, y: r.y },
  { x: r.x + r.width, y: r.y + r.depth }, { x: r.x, y: r.y + r.depth },
];
export const protectedRegions = (space: Space) => space.regions.filter(r => r.zone === space.zone && r.grade !== "D");

// FR-02.3: cells switch before the light arrives (switching time, delay and safety margin).
export const ADVANCE_MINUTES = 5;
export type BlockingPeriod = { arrival: number; start: number; end: number };
export type BlockingSchedule = Map<string, BlockingPeriod[]>;
// Demo stand-in for FR-01/FR-02 output: per protected region, the minutes of the day when
// sunlight reaches it. One pass over the day shares each minute's sun polygon across regions.
export const DAY_MINUTES = 24 * 60;
export const isBlockingAt = (p: BlockingPeriod, minute: number) => minute >= p.start && minute < p.end;

export function blockingSchedule(space: Space): BlockingSchedule {
  const regions = protectedRegions(space).map(r => ({ id: r.id, area: footprint(r), arrival: null as number | null }));
  const schedule: BlockingSchedule = new Map(regions.map(r => [r.id, []]));
  for (let minute = 0; minute <= DAY_MINUTES && regions.length; minute++) {
    const sun = minute < DAY_MINUTES ? sunlightPolygon(space, minute / 60) : [];
    for (const r of regions) {
      const lit = polygonsTouch(sun, r.area);
      if (lit && r.arrival === null) r.arrival = minute;
      if (!lit && r.arrival !== null) {
        const periods = schedule.get(r.id)!, start = Math.max(0, r.arrival - ADVANCE_MINUTES), last = periods.at(-1);
        // A short gap shorter than the advance time continues the previous period instead of overlapping it.
        if (last && start <= last.end) last.end = minute;
        else periods.push({ arrival: r.arrival, start, end: minute });
        r.arrival = null;
      }
    }
  }
  return schedule;
}
// The user's chosen time decides when blocking may happen: each sunlit period is clipped to it,
// and with the setting off nothing is blocked.
export function applyShadingTime(schedule: BlockingSchedule, time: ShadingTime | undefined): BlockingSchedule {
  const start = time?.enabled ? timeToMinute(time.start) : null;
  const end = time?.enabled ? timeToMinute(time.end) : null;
  return new Map([...schedule].map(([id, periods]) => [
    id,
    start === null || end === null || start >= end
      ? []
      : periods
          .map((p) => ({ ...p, start: Math.max(p.start, start), end: Math.min(p.end, end) }))
          .filter((p) => p.start < p.end),
  ]));
}
// Same clip, but a region that has its own time uses it instead of the space's.
export function applyRegionShading(schedule: BlockingSchedule, space: Pick<Space, "regions">, base: ShadingTime | undefined): BlockingSchedule {
  const own = new Map(space.regions.map((r) => [r.id, r.shadingSchedule] as const));
  return new Map([...schedule].map(([id, periods]) => [id, applyShadingTime(new Map([[id, periods]]), own.get(id) ?? base).get(id) ?? []]));
}
// Stops at the first lit minute, so callers that only need the first contact skip the full-day scan.
export function firstProtectionContact(space: Space): number | null {
  const areas = protectedRegions(space).map(footprint);
  for (let minute = 0; minute < DAY_MINUTES && areas.length; minute++) {
    const sun = sunlightPolygon(space, minute / 60);
    if (areas.some(area => polygonsTouch(sun, area))) return minute;
  }
  return null;
}

// Demo cell mapping until FR-02 exists: a blocked region darkens the window columns matching its
// place along the wall (plus one edge cell, FR-04.4); higher sun uses the upper rows. Returns row-major indices.
export function shadedCells(space: Space, schedule: BlockingSchedule, hour: number): Set<number> {
  const win = resolveWindow(space);
  const cells = new Set<number>();
  if (!win) return cells;
  const { cols, rows } = filmGrid(win);
  const minute = Math.round(hour * 60);
  const altitude = Math.max(0, 1 - Math.abs(hour - 12) / 6);
  // Light toward a low region crosses the window higher as the sun rises: a half-height band slides up.
  const band = Math.ceil(rows / 2);
  const top = Math.round((1 - altitude) * (rows - band));
  const length = wallLength(space, win.wall);
  for (const region of protectedRegions(space)) {
    if (!schedule.get(region.id)?.some(p => isBlockingAt(p, minute))) continue;
    // The demo sun patch is not traced through the window, so positions along the wall are
    // mapped proportionally onto the window's columns.
    const [from, size] = isWidthWall(win.wall) ? [region.x, region.width] : [region.y, region.depth];
    // Columns are seen from inside: facing a south or west wall, plan coordinates run right to left.
    const start = win.wall === "south" || win.wall === "west" ? length - from - size : from;
    const first = Math.max(0, Math.floor((start / length) * cols) - 1);
    const last = Math.min(cols - 1, Math.ceil(((start + size) / length) * cols));
    for (let row = top; row < top + band; row++)
      for (let col = first; col <= last; col++) cells.add(row * cols + col);
  }
  return cells;
}
/** Midnight today in the space's time zone, as the ISO start FR-01 expects, and its label. */
export function predictionDay(timeZone: string, now = new Date()) {
  const date = new Intl.DateTimeFormat("en-CA", { timeZone }).format(now);
  const offset = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "longOffset" })
    .formatToParts(now)
    .find((p) => p.type === "timeZoneName")!
    .value.replace("GMT", "") || "+00:00";
  const [, month, day] = date.split("-").map(Number);
  return { start: `${date}T00:00:00${offset}`, label: `${month}월 ${day}일 기준` };
}

// FR-01 output in the shape the preview already uses. Regions come back in the saved space's
// order; each run of exposed samples becomes a period that starts ADVANCE_MINUTES early.
export function scheduleFromPrediction(space: Space, prediction: SunpathPrediction): BlockingSchedule {
  if (space.serverRevision && prediction.scene_revision !== space.serverRevision)
    throw new Error("prediction revision does not match the saved space");
  const owners = regionOwners(space, prediction.regions.length);
  if (!owners) throw new Error("prediction regions do not match the saved space");
  const start = Date.parse(prediction.start_time);
  const minuteOf = (t: string) => Math.round((Date.parse(t) - start) / 60000);
  return new Map(space.regions.map((region, i) => {
    const periods: BlockingPeriod[] = [];
    let arrival: number | null = null;
    // The side facing the window and the top come back as separate areas; the light reaches the furniture when either is exposed.
    const timelines = prediction.regions.filter((_, j) => owners[j] === i).map((r) => r.timeline);
    const timeline = timelines[0].map((t, k) => ({ timestamp: t.timestamp, status: timelines.some((tl) => tl[k]?.status === "exposed") ? "exposed" : t.status }));
    for (const t of [...timeline, null]) {
      const minute = t ? minuteOf(t.timestamp) : DAY_MINUTES;
      if (t?.status === "exposed") {
        arrival ??= minute;
        continue;
      }
      if (arrival === null) continue;
      const begin = Math.max(0, arrival - ADVANCE_MINUTES), last = periods.at(-1);
      if (last && begin <= last.end) last.end = minute;
      else periods.push({ arrival, start: begin, end: Math.min(minute, DAY_MINUTES) });
      arrival = null;
    }
    return [region.id, periods];
  }));
}
export function formatMinute(minute: number): string {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}
