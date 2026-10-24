import { filmGrid, resolveWindow, timeToMinute, validateShadingTime, validateWindow, walls } from "./model";
import type { ShadingTime, Space } from "./model";
import { DAY_MINUTES, formatMinute, protectedRegions, shadedCells } from "./sunlight";
import type { BlockingSchedule as Schedule } from "./sunlight";
import type { ReactNode } from "react";

// Surface gap between cells, in cell units: white space separates the cells instead of grid lines.
const GAP = 0.14;

// MAR5.3 / WAR4.3: the window as 10 cm film cells, seen from inside; row 0 is the top row.
export function FilmGrid({ space, schedule, hour, marker = false }: { space: Space; schedule?: Schedule; hour?: number; marker?: boolean }) {
  const win = resolveWindow(space);
  if (!win) return null;
  // Older saved windows skip validation on load; don't draw a grid from sizes the form would reject.
  const problem = validateWindow(space);
  if (problem) return <p className="muted small">{problem}</p>;
  const { cols, rows } = filmGrid(win);
  const showShading = hour !== undefined && !!schedule;
  const shaded = showShading ? shadedCells(space, schedule, hour) : new Set<number>();
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
        return <rect aria-hidden="true" key={i} className={on ? "cell shaded" : "cell"} x={(i % cols) + GAP / 2} y={Math.floor(i / cols) + GAP / 2} width={1 - GAP} height={1 - GAP} rx=".14">
          <title>{`${Math.floor(i / cols) + 1}행 ${(i % cols) + 1}열 · ${on ? "가림" : "열림"}`}</title>
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
      {showShading && <span className="example-note"> · 칸 배치는 예시값(FR-02 연결 전)</span>}
    </figcaption>
  </figure>;
}

// WAR4.2: the user chooses when light may be blocked. The 24-hour bar shows that range and the
// time picked on the slider; the simplified sun times stay as small reference text only.
export function ShadingTimeControl({ space, value, onChange, sunSchedule, hour, onSave, dirty = false, saving = false, sunSource }: {
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
    <p className="muted small">
      {value.enabled ? "설정한 시간 안에서 햇빛이 보호영역에 닿을 때만 필름을 가립니다." : "꺼져 있으면 필름을 가리지 않습니다. 켜고 차단할 시간을 정해 주세요."}
    </p>
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
      <span className="now" style={{ left: pct(minute) }} />
    </div>
    <div className="blocking-axis" aria-hidden="true">
      {[0, 6, 12, 18, 24].map((h) => <span key={h} style={{ left: pct(h * 60) }}>{String(h).padStart(2, "0")}</span>)}
    </div>
    <p className="blocking-status" role="status">
      {active ? `${value.start} ~ ${value.end} 차단 · 지금 선택한 ${formatMinute(minute)}은 ${minute >= start && minute < end ? "차단 시간" : "차단 시간 밖"}` : "차단 시간이 설정되지 않음"}
    </p>
    {regions.length > 0 ? sunSource?.note ?? <ul className="sun-reference muted small">
      {regions.map((r) => {
        const periods = sunSchedule.get(r.id) ?? [];
        return <li key={r.id}>
          {r.name} · {sunSource?.label ?? "햇빛 닿는 시간(간이 계산)"} {periods.length ? periods.map((p) => `${formatMinute(p.arrival)}~${formatMinute(p.end)}`).join(", ") : sunSource ? "직사광이 닿지 않음" : "없음"}
        </li>;
      })}
    </ul> : <p className="muted small">보호영역이 없어 가릴 곳이 없습니다. D 등급은 채광을 우선해 가리지 않습니다.</p>}
    {onSave && <button type="button" className="secondary" disabled={!dirty || saving || (value.enabled && !!error)} onClick={onSave}>
      {saving ? "저장 중…" : "차단 시간 저장"}
    </button>}
  </section>;
}
