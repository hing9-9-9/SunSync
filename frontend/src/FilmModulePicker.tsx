import { filmGrid, resolveWindow, validateWindow, walls } from "./model";
import type { Space } from "./model";

/**
 * The window seen from inside, as its 1 m film modules (row 0 = top, col 0 = left).
 * Interactive: tap the module that should show the 60 cm marker. With readOnly it only pictures the layout.
 */
export default function FilmModulePicker({ rows, cols, row, col, onChange, readOnly = false }: {
  rows: number; cols: number; row?: number; col?: number; onChange?: (row: number, col: number) => void; readOnly?: boolean;
}) {
  return (
    <div className={`module-picker${readOnly ? " readonly" : ""}`} role={readOnly ? "img" : "radiogroup"} aria-label={readOnly ? `1m 필름 ${rows}행 ${cols}열` : "마커를 표시할 필름"} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 72px))` }}>
      {Array.from({ length: rows * cols }, (_, i) => {
        const r = Math.floor(i / cols), c = i % cols, on = !readOnly && r === row && c === col;
        const label = `위에서 ${r + 1}번째, 왼쪽에서 ${c + 1}번째 필름`;
        const inner = on ? <span className="module-marker" aria-hidden="true" /> : null;
        return readOnly
          ? <span key={i} className="module" aria-hidden="true" />
          : <button key={i} type="button" role="radio" aria-checked={on} aria-label={label} className={`module${on ? " on" : ""}`} onClick={() => onChange?.(r, c)}>{inner}</button>;
      })}
    </div>
  );
}

/** Persist the user-selected module with the window before camera handoff. */
export function FilmLayout({ space, onChange }: { space: Space; onChange?: (row: number, col: number) => void }) {
  const win = resolveWindow(space);
  if (!win || validateWindow(space)) return null;
  const { cols, rows } = filmGrid(win);
  const whole = rows % 10 === 0 && cols % 10 === 0;
  return (
    <section className="film-layout" aria-label="필름 구성">
      <h3>필름 구성</h3>
      {whole ? <>
        <p className="muted small">1m 필름 {rows / 10}행 × {cols / 10}열이에요. 마커를 띄울 필름을 눌러 선택해 주세요.</p>
        <FilmModulePicker rows={rows / 10} cols={cols / 10} row={win.captureModule?.row} col={win.captureModule?.col} onChange={onChange} readOnly={!onChange} />
        {win.captureModule && <p role="status">선택한 필름: 위에서 {win.captureModule.row + 1}번째 · 왼쪽에서 {win.captureModule.col + 1}번째</p>}
        <p className="muted small">{walls[win.wall]} · {win.width} × {win.height} m · 안쪽에서 본 모습</p>
      </> : <p className="muted small">창문이 1m 단위(예: 3 × 2 m)여야 마커 촬영을 할 수 있어요. 지금은 {win.width} × {win.height} m예요.</p>}
    </section>
  );
}
