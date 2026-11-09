import { useState } from "react";
import { filmStatesAt } from "./shading";
import type { ShadingResult } from "./shading";
import { filmGrid, resolveWindow, timeToMinute, validateShadingTime, validateWindow, walls } from "./model";
import { defaultShadingTime } from "./model";
import type { ShadingTime, Space } from "./model";
import { DAY_MINUTES, formatMinute, protectedRegions, shadedCells } from "./sunlight";
import type { BlockingSchedule as Schedule } from "./sunlight";
import {
  applyFilmSchedule,
  fetchFilmLogs,
  fetchFilmState,
  formatTimeLabel,
  userMessage,
} from "./spaceApi";
import type {
  AppliedCellState,
  FilmApplicationLogEntry,
  ScheduleApplyResponse,
  WindowFilmStateResponse,
} from "./spaceApi";
import type { ReactNode } from "react";

// Surface gap between cells, in cell units: white space separates the cells instead of grid lines.
const GAP = 0.14;

// MAR5.3 / WAR4.3: the window as 10 cm film cells, seen from inside; row 0 is the top row.
export function FilmGrid({ space, schedule, hour, marker = false, prediction, setting = defaultShadingTime }: { space: Space; schedule?: Schedule; hour?: number; marker?: boolean; prediction?: ShadingResult; setting?: ShadingTime }) {
  const win = resolveWindow(space);
  if (!win) return null;
  // Older saved windows skip validation on load; don't draw a grid from sizes the form would reject.
  const problem = validateWindow(space);
  if (problem) return <p className="muted small">{problem}</p>;
  const { cols, rows } = filmGrid(win);
  if (prediction && !prediction.plan) return <figure className="film-grid"><p className={prediction.error ? "error" : "muted small"} role={prediction.error ? "alert" : "status"}>{prediction.error ?? (prediction.empty ? "아직 이 공간의 차광 계획이 없습니다." : "차광 계획을 계산하는 중…")}</p></figure>;
  const serverWindow = prediction?.plan?.windows[0];
  if (serverWindow && (serverWindow.film_cols !== cols || serverWindow.film_rows !== rows))
    return <p className="error" role="alert">저장된 창문 규격과 차광 계획이 다릅니다. 최신 공간 정보를 불러와 주세요.</p>;
  let states: ReturnType<typeof filmStatesAt> | undefined;
  try {
    if (prediction?.plan && hour !== undefined) states = filmStatesAt(prediction.plan, hour, setting);
  } catch {
    return <p className="error" role="alert">선택한 시각의 차광 계획을 확인할 수 없습니다. 다시 계산해 주세요.</p>;
  }
  const counts = { infeasible: 0, unknown: 0 };
  for (const cell of states?.values() ?? []) {
    if (cell.state === "infeasible" || cell.state === "unknown") counts[cell.state]++;
  }
  const showShading = hour !== undefined && !!schedule;
  const shaded = states
    ? new Set([...states].filter(([, c]) => c.state === "opaque").map(([i]) => i))
    : showShading ? shadedCells(space, schedule, hour) : new Set<number>();
  const m = win.marker;
  // Cells are rounded to whole 10 cm units, so scale real metres onto the drawn grid.
  const sx = cols / win.width, sy = rows / win.height;
  const markerBox = marker && {
    x: (m.x - m.size / 2) * sx,
    y: (win.height - m.y - m.size / 2) * sy,
    width: m.size * sx,
    height: m.size * sy,
  };
  return <figure className="film-grid">
    <svg viewBox={`${-GAP} ${-GAP} ${cols + GAP * 2} ${rows + GAP * 2}`} role="img" aria-label={`${walls[win.wall]} 창문 필름 ${cols}×${rows}칸${showShading ? `, 가림 ${shaded.size}칸` : ""}`}>
      {Array.from({ length: cols * rows }, (_, i) => {
        const on = shaded.has(i);
        const state = states?.get(i);
        const warning = state?.state === "infeasible" || state?.state === "unknown";
        const label = state?.state === "infeasible" ? "보호 조건 충족 불가" : state?.state === "unknown" ? "판단 불가" : on ? "가림" : "열림";
        return <rect aria-hidden="true" key={i} className={on ? "cell shaded" : "cell"} style={warning ? { fill: state?.state === "infeasible" ? "var(--c-f8d7da)" : "var(--c-e2e5e9)", stroke: state?.state === "infeasible" ? "var(--c-a32b38)" : "var(--c-616b78)", strokeWidth: .06, strokeDasharray: state?.state === "unknown" ? ".12 .08" : undefined } : undefined} x={(i % cols) + GAP / 2} y={Math.floor(i / cols) + GAP / 2} width={1 - GAP} height={1 - GAP} rx=".14">
          <title>{`${Math.floor(i / cols) + 1}행 ${(i % cols) + 1}열 · ${label}${state?.reasons.length ? ` · ${state.reasons.join(", ")}` : ""}`}</title>
        </rect>;
      })}
      {markerBox && <rect aria-hidden="true" className="marker" {...markerBox} rx=".1"><title>QR 마커</title></rect>}
    </svg>
    <figcaption className="muted small">
      <span className="plan-legend film-legend" aria-hidden="true">
        {showShading && <span><i className="shaded" />가림</span>}
        <span><i className="open" />열림</span>
        {markerBox && <span><i className="marker" />QR 마커</span>}
      </span>
      {walls[win.wall]} · {win.width} × {win.height} m · {cols}×{rows}칸
      {showShading && ` · 가림 ${shaded.size}칸`}
      {showShading && <span className="example-note">{prediction ? " · 계산된 차단 계획" : " · 예시 칸 배치"}</span>}
      {counts.infeasible > 0 && <p role="status">보호 조건 충족 불가 {counts.infeasible}칸: 필름을 가장 어둡게 해도 보호 조건을 만족하지 못합니다.</p>}
      {counts.unknown > 0 && <p role="status">판단 불가 {counts.unknown}칸: 일사량 정보가 부족합니다.</p>}
    </figcaption>
  </figure>;
}

// WAR4.2: the user chooses when light may be blocked. The 24-hour bar shows that range and the
// time picked on the slider; the simplified sun times stay as small reference text only.
export function ShadingTimeControl({ space, value, onChange, sunSchedule, hour, onSave, dirty = false, saving = false, sunSource, compact = false }: {
  space: Space;
  value: ShadingTime;
  onChange: (time: ShadingTime) => void;
  sunSchedule: Schedule;
  hour: number;
  onSave?: () => void;
  dirty?: boolean;
  saving?: boolean;
  // Where the sun times come from; without it they are the simplified preview calculation.
  sunSource?: { label: string; note?: ReactNode };
  compact?: boolean;
}) {
  const minute = Math.round(hour * 60);
  const regions = protectedRegions(space);
  const error = validateShadingTime(value);
  const start = timeToMinute(value.start), end = timeToMinute(value.end);
  const pct = (m: number) => `${(m / DAY_MINUTES) * 100}%`;
  const active = value.enabled && !error && start !== null && end !== null;
  return <section className="blocking-schedule shading-time">
    <div className="section-row">
      <h3>차단 시간 설정</h3>
      <label className="switch" aria-label="차단 시간 설정">
        <input type="checkbox" role="switch" checked={value.enabled} onChange={(e) => onChange({ ...value, enabled: e.target.checked })} />
        <span className="switch-track" aria-hidden="true" />
        <span className="switch-label">{value.enabled ? "켜짐" : "꺼짐"}</span>
      </label>
    </div>
    {!compact && <p className="muted small">
      {value.enabled ? "설정한 시간 안에서 햇빛이 보호 영역에 닿을 때만 필름을 가립니다." : "꺼져 있으면 필름을 가리지 않습니다. 켜고 차단할 시간을 정해 주세요."}
    </p>}
    <div className="two-fields">
      <label className="field">
        <span>시작</span>
        <input type="time" step={600} value={value.start} disabled={!value.enabled} onChange={(e) => onChange({ ...value, start: e.target.value })} />
      </label>
      <label className="field">
        <span>종료</span>
        <input type="time" step={600} value={value.end} disabled={!value.enabled} onChange={(e) => onChange({ ...value, end: e.target.value })} />
      </label>
    </div>
    {value.enabled && error && <p className="error" role="alert">{error}</p>}
    <div className="blocking-track" aria-hidden="true">
      {active && <span className="period" style={{ left: pct(start), width: pct(end - start) }} />}
      {!compact && <span className="now" style={{ left: pct(minute) }} />}
    </div>
    <div className="blocking-axis" aria-hidden="true">
      {[0, 6, 12, 18, 24].map((h) => <span key={h} style={{ left: pct(h * 60) }}>{String(h).padStart(2, "0")}</span>)}
    </div>
    {!compact && <p className="blocking-status" role="status">
      {active ? `${value.start} ~ ${value.end} 차단 · 지금 선택한 ${formatMinute(minute)}은 ${minute >= start && minute < end ? "차단 시간" : "차단 시간 밖"}` : "차단 시간이 설정되지 않음"}
    </p>}
    {!compact && (regions.length > 0 ? sunSource?.note ?? <ul className="sun-reference muted small">
      {regions.map((r) => {
        const periods = sunSchedule.get(r.id) ?? [];
        return <li key={r.id}>
          {r.name} · {sunSource?.label ?? "햇빛 닿는 시간(간이 계산)"} {periods.length ? periods.map((p) => `${formatMinute(p.arrival)}~${formatMinute(p.end)}`).join(", ") : sunSource ? "직사광이 닿지 않음" : "없음"}
        </li>;
      })}
    </ul> : <p className="muted small">보호 영역이 없어 가릴 곳이 없습니다. D 등급은 채광을 우선해 가리지 않습니다.</p>)}
    {onSave && <button type="button" className="secondary" disabled={!dirty || saving || (value.enabled && !!error)} onClick={onSave}>
      {saving ? "저장 중…" : "차단 시간 저장"}
    </button>}
  </section>;
}

const stateLabel: Record<AppliedCellState, string> = {
  transparent: "열림",
  opaque: "가림",
  dimmed: "디밍",
};

const statusLabel: Record<FilmApplicationLogEntry["status"], string> = {
  applied: "적용됨",
  target_unmet: "목표 미달",
  unknown_intensity: "조도 미상",
};

/** FR-05: apply the computed ShadingPlan to the virtual film and inspect cell states & transition logs. */
export function VirtualFilmControl({
  space,
  prediction,
  hour,
}: {
  space: Space;
  prediction?: ShadingResult;
  hour: number;
}) {
  const sceneId = space.serverSceneId;
  const win = resolveWindow(space);
  const [applying, setApplying] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [applied, setApplied] = useState<ScheduleApplyResponse | null>(null);
  const [windowState, setWindowState] = useState<WindowFilmStateResponse | null>(null);
  const [logs, setLogs] = useState<FilmApplicationLogEntry[] | null>(null);
  const [error, setError] = useState("");

  if (!sceneId || !win) return null;

  const plan = prediction?.plan;
  const simulatedAt = plan
    ? new Date(Date.parse(plan.start_time) + Math.round(hour * 60) * 60_000).toISOString()
    : undefined;

  async function handleApplySchedule() {
    if (!sceneId || !plan || applying) return;
    setApplying(true);
    setError("");
    try {
      const result = await applyFilmSchedule(sceneId, plan, simulatedAt);
      setApplied(result);
      setWindowState(result.windows[0] ?? null);
      const logResult = await fetchFilmLogs(sceneId, { limit: 20 });
      setLogs(logResult.logs);
    } catch (err) {
      setError(userMessage(err, "가상 필름에 일정을 적용하지 못했습니다."));
    } finally {
      setApplying(false);
    }
  }

  async function handleInspectStateAndLogs() {
    if (!sceneId || loadingLogs) return;
    setLoadingLogs(true);
    setError("");
    try {
      const [stateRes, logRes] = await Promise.all([
        fetchFilmState(sceneId, simulatedAt),
        fetchFilmLogs(sceneId, { limit: 20 }),
      ]);
      setWindowState(stateRes.windows[0] ?? null);
      setLogs(logRes.logs);
    } catch (err) {
      setError(userMessage(err, "가상 필름 상태와 적용 기록을 불러오지 못했습니다."));
    } finally {
      setLoadingLogs(false);
    }
  }

  const opaqueCells = windowState
    ? windowState.states.reduce(
        (sum, row) => sum + row.filter((cell) => cell === "opaque").length,
        0,
      )
    : 0;
  const totalCells = windowState ? windowState.film_rows * windowState.film_cols : 0;

  return (
    <section className="virtual-film-control" aria-label="가상 필름 제어 및 적용 기록">
      <div className="divider" />
      <div className="section-row">
        <h4>가상 필름 제어 (FR-05)</h4>
        <span className="muted small">{formatMinute(Math.round(hour * 60))} 기준</span>
      </div>
      <p className="muted small">
        계산된 차광 일정을 가상 필름 장치에 적용하고 시각별 칸 상태와 전환 기록을 확인합니다.
      </p>
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={!plan || applying}
          onClick={handleApplySchedule}
        >
          {applying ? "일정 적용 중…" : "가상 필름에 일정 적용"}
        </button>
        <button
          type="button"
          className="text-button"
          disabled={loadingLogs}
          onClick={handleInspectStateAndLogs}
        >
          {loadingLogs ? "불러오는 중…" : "필름 상태·기록 조회"}
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {(applied || windowState) && (
        <p className="muted small" role="status">
          {windowState &&
            `현재 시각(${formatTimeLabel(windowState.evaluated_at, space.site?.timezone)}) 가림 ${opaqueCells}/${totalCells}칸`}
          {applied &&
            ` · 전환 기록 ${applied.applied_records_count}건${
              applied.target_unmet_count > 0 ? ` · 목표 미달 ${applied.target_unmet_count}건` : ""
            }`}
        </p>
      )}
      {logs !== null && (
        <details className="film-log-details" open={logs.length > 0}>
          <summary className="muted small">
            필름 적용 기록 ({logs.length}건)
          </summary>
          {logs.length === 0 ? (
            <p className="muted small">아직 기록된 필름 상태 전환이 없습니다.</p>
          ) : (
            <ul className="sun-reference muted small">
              {logs.slice(0, 10).map((entry, idx) => (
                <li key={entry.log_id ?? `${entry.timestamp}-${entry.row}-${entry.col}-${idx}`}>
                  {formatTimeLabel(entry.timestamp, space.site?.timezone)} · {entry.row + 1}행{" "}
                  {entry.col + 1}열 · {stateLabel[entry.previous_state]} →{" "}
                  {stateLabel[entry.new_state]} · <strong>{statusLabel[entry.status]}</strong>
                  {entry.note ? ` (${entry.note})` : ""}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
    </section>
  );
}
