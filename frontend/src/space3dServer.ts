// Server results for the 3D sample, reduced to the few numbers the view reads (the FR-01 reply is tens of MB).
import type { CellState, ShadingPlan } from "./shading.ts";
import type { SunpathPrediction } from "./spaceApi.ts";

export type ServerDay = {
  times: number[]; // FR-01 timestamps (ms)
  sun: [number, number, number][]; // sun direction, ENU (x east, y north, z up)
  exposure: { share: number[]; maxW: (number | null)[] }[]; // per region, in space.regions order
  rows: number;
  cols: number;
  // FR-04 stabilised segments; cell is the server index row * cols + col (row 0 top, seen from inside).
  segments: { cell: number; from: number; to: number; state: CellState; regions: number[] }[];
  flicker: number;
};
export const STATE_CODE: Record<CellState, number> = { transparent: 0, opaque: 1, infeasible: 2, unknown: 3 };

export function compactDay(sunpath: SunpathPrediction, plans: ShadingPlan[], flicker: number, owners?: number[] | null): ServerDay {
  const regionIds = sunpath.regions.map(r => r.region_id);
  // `owners[j]` is the space region that server area j belongs to (a furniture's top counts for its furniture's region).
  const own = (j: number) => owners?.[j] ?? j;
  const groups = Array.from({ length: Math.max(-1, ...sunpath.regions.map((_, j) => own(j))) + 1 }, (_, i) => sunpath.regions.filter((_, j) => own(j) === i));
  const win = plans[0]?.windows[0];
  return {
    times: (sunpath.solar_positions ?? []).map(p => Date.parse(p.timestamp)),
    sun: (sunpath.solar_positions ?? []).map(p => p.sun_direction_enu),
    exposure: groups.map(group => ({
      share: group[0].timeline.map((_, k) => Math.max(...group.map(r => { const t = r.timeline[k]; return t?.sample_count ? t.exposed_sample_count / t.sample_count : 0; }))),
      maxW: group[0].timeline.map((_, k) => {
        const values = group.map(r => r.timeline[k]?.max_direct_irradiance_W_m2).filter((v): v is number => typeof v === "number");
        return values.length ? Math.max(...values) : null;
      }),
    })),
    rows: win?.film_rows ?? 0,
    cols: win?.film_cols ?? 0,
    segments: plans.flatMap(p => (p.windows[0]?.cells ?? []).flatMap(c => c.segments
      .filter(s => s.state !== "transparent")
      .map(s => ({
        cell: c.row * p.windows[0].film_cols + c.col,
        from: Date.parse(s.start_time),
        to: Date.parse(s.end_time),
        state: s.state,
        // A cell asked for by both the side and the top of one furniture counts once for it.
        regions: [...new Set(s.reasons.map(r => regionIds.indexOf(r.region_id)).filter(i => i >= 0).map(own))],
      })))),
    flicker,
  };
}

/** Cell states at one moment (server indexing) and how many closed cells each region asked for. */
export function cellsAt(day: ServerDay, at: number, regionCount: number, gate?: (region: number) => boolean, fallback = true) {
  const states = new Uint8Array(day.rows * day.cols);
  const byRegion = new Array<number>(regionCount).fill(0);
  for (const s of day.segments) {
    if (at < s.from || at >= s.to) continue;
    // With a gate, only the regions inside their own time keep a cell closed; a segment without regions follows `fallback`.
    const regions = gate ? s.regions.filter(gate) : s.regions;
    if (gate && (s.regions.length ? !regions.length : !fallback)) continue;
    states[s.cell] = Math.max(states[s.cell], STATE_CODE[s.state]);
    for (const r of regions) byRegion[r]++;
  }
  return { states, byRegion };
}

/** The FR-01 sample in force at `at` (samples hold until the next one). */
export function sampleIndex(day: ServerDay, at: number) {
  let i = -1;
  while (i + 1 < day.times.length && day.times[i + 1] <= at) i++;
  return i;
}

/** Sun direction between FR-01 samples, linearly blended. */
export function sunAt(day: ServerDay, at: number): [number, number, number] | null {
  const i = sampleIndex(day, at);
  if (i < 0 || i >= day.times.length - 1) return i < 0 ? null : day.sun[i];
  const k = (at - day.times[i]) / (day.times[i + 1] - day.times[i]);
  return day.sun[i].map((v, j) => v + (day.sun[i + 1][j] - v) * k) as [number, number, number];
}
