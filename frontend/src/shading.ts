import { timeToMinute, withinShading } from "./model.ts";
import type { ShadingTime } from "./model.ts";

export type CellState = "opaque" | "transparent" | "infeasible" | "unknown";
export type ShadingPlan = {
  plan_version: "0.1.0";
  scene_id: string;
  scene_revision: number;
  start_time: string;
  end_time: string;
  windows: {
    window_id: string; film_rows: number; film_cols: number;
    cells: { row: number; col: number; segments: {
      start_time: string; end_time: string; state: CellState;
      reasons: { region_id: string; grade: "A" | "B" | "C" | "D" }[];
    }[] }[];
  }[];
};
// `empty`: the server has no plan for this space yet, which is a calm state, not an error.
export type ShadingResult = { plan?: ShadingPlan; error?: string; empty?: boolean };
/** One UI space has one window. Never interpret an unrelated/malformed response as open film. */
export function validateShadingPlan(plan: ShadingPlan, sceneId: string, revision: number, start: string) {
  if (plan.plan_version !== "0.1.0" || plan.scene_id !== sceneId || plan.scene_revision !== revision ||
      Date.parse(plan.start_time) !== Date.parse(start) || !Number.isFinite(Date.parse(plan.end_time)) ||
      Date.parse(plan.end_time) <= Date.parse(start) || !Array.isArray(plan.windows) || plan.windows.length > 1)
    throw new Error("차광 계획과 저장된 공간 정보가 맞지 않습니다.");
  for (const w of plan.windows) {
    if (!Number.isInteger(w.film_rows) || !Number.isInteger(w.film_cols) || w.film_rows < 1 || w.film_cols < 1 || !Array.isArray(w.cells))
      throw new Error("차광 계획의 필름 규격을 확인해 주세요.");
    const seen = new Set<number>();
    for (const c of w.cells) {
      const index = c.row * w.film_cols + c.col;
      if (!Number.isInteger(c.row) || !Number.isInteger(c.col) || c.row < 0 || c.row >= w.film_rows || c.col < 0 || c.col >= w.film_cols || seen.has(index) || !Array.isArray(c.segments))
        throw new Error("차광 계획의 필름 칸을 확인해 주세요.");
      seen.add(index);
      let previous = Date.parse(plan.start_time);
      for (const s of c.segments) {
        const from = Date.parse(s.start_time), to = Date.parse(s.end_time);
        if (!Number.isFinite(from) || !Number.isFinite(to) || from < previous || to <= from || to > Date.parse(plan.end_time) || !["opaque", "transparent", "infeasible", "unknown"].includes(s.state))
          throw new Error("차광 계획의 시간 구간을 확인해 주세요.");
        previous = to;
      }
    }
  }
  return plan;
}
/** Keep the user's existing on/off and time gate. FR02 segments are [start, end).
 *  A cell stays closed while at least one region that asked for it is inside its own time: the
 *  region's own setting when `regionTime` returns one, the space's `setting` otherwise. */
export function filmStatesAt(plan: ShadingPlan, hour: number, setting: ShadingTime, regionTime?: (regionId: string) => ShadingTime | undefined) {
  const minute = hour * 60;
  const time = Date.parse(plan.start_time) + minute * 60000;
  if (!Number.isFinite(time) || time < Date.parse(plan.start_time) || time >= Date.parse(plan.end_time))
    throw new Error("선택 시각이 차광 계획 범위 밖입니다.");
  const states = new Map<number, { state: CellState; reasons: string[] }>();
  const w = plan.windows[0];
  for (const c of w?.cells ?? []) {
    const s = c.segments.find(s => time >= Date.parse(s.start_time) && time < Date.parse(s.end_time));
    if (!s) continue;
    const active = s.reasons.filter(r => withinShading(regionTime?.(r.region_id) ?? setting, minute));
    if (s.reasons.length ? !active.length : !withinShading(setting, minute)) continue;
    states.set(c.row * w.film_cols + c.col, { state: s.state, reasons: active.map(r => `${r.grade} 등급`) });
  }
  return states;
}

/** The plan as the film should follow it: only the stretches inside the chosen block times stay.
 *  Uses the same rule as `filmStatesAt` (a cell stays while a region that asked for it is inside its own time),
 *  but cuts the segments at the start and end of each time so a minute-level time is honoured too. */
export function gatePlan(plan: ShadingPlan, setting: ShadingTime, regionTime?: (regionId: string) => ShadingTime | undefined): ShadingPlan {
  const t0 = Date.parse(plan.start_time), t1 = Date.parse(plan.end_time);
  const DAY = 1440;
  const iso = (ms: number) => new Date(ms).toISOString();
  const windows = plan.windows.map((w) => ({
    ...w,
    cells: w.cells.flatMap((c) => {
      const segments: typeof c.segments = [];
      for (const s of c.segments) {
        const from = Date.parse(s.start_time), to = Date.parse(s.end_time);
        const times = s.reasons.length ? s.reasons.map((r) => regionTime?.(r.region_id) ?? setting) : [setting];
        const cuts = new Set<number>([from, to]);
        for (const t of times) {
          if (!t.enabled) continue;
          const a = timeToMinute(t.start), b = timeToMinute(t.end);
          if (a === null || b === null) continue;
          for (let day = 0; t0 + day * DAY * 60000 < t1; day++)
            for (const m of [a, b]) {
              const at = t0 + (day * DAY + m) * 60000;
              if (at > from && at < to) cuts.add(at);
            }
        }
        const edges = [...cuts].sort((x, y) => x - y);
        for (let i = 0; i + 1 < edges.length; i++) {
          const minute = (((edges[i] - t0) / 60000) % DAY + DAY) % DAY;
          const active = s.reasons.filter((r) => withinShading(regionTime?.(r.region_id) ?? setting, minute));
          if (s.reasons.length ? !active.length : !withinShading(setting, minute)) continue;
          const piece = {
            ...s,
            start_time: edges[i] === from ? s.start_time : iso(edges[i]),
            end_time: edges[i + 1] === to ? s.end_time : iso(edges[i + 1]),
            reasons: active,
          };
          const last = segments[segments.length - 1];
          if (last && Date.parse(last.end_time) === edges[i] && last.state === piece.state &&
              last.reasons.map((r) => r.region_id).join() === piece.reasons.map((r) => r.region_id).join())
            last.end_time = piece.end_time;
          else segments.push(piece);
        }
      }
      return segments.length ? [{ ...c, segments }] : [];
    }),
  }));
  return { ...plan, windows };
}
