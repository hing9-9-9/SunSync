import { resolveWindow, validateWindow } from "./model";
import type { Space } from "./model";

export default function FilmModulePicker({
  rows, cols, row, col, topRemainder = 0, rightRemainder = 0, onChange,
}: {
  rows: number; cols: number; row?: number; col?: number;
  topRemainder?: number; rightRemainder?: number;
  onChange?: (row: number, col: number) => void;
}) {
  const readOnly = !onChange;
  const widths = [...Array<number>(cols).fill(1), ...(rightRemainder > 0 ? [rightRemainder] : [])];
  const heights = [...(topRemainder > 0 ? [topRemainder] : []), ...Array<number>(rows).fill(1)];
  return (
    <div
      className={`module-picker${readOnly ? " readonly" : ""}`}
      role={readOnly ? "img" : "radiogroup"}
      aria-label={readOnly ? `1m 필름 ${rows}행 ${cols}열` : "마커를 표시할 필름"}
      style={{ gridTemplateColumns: widths.map(width => `minmax(0, ${width * 72}px)`).join(" ") }}
    >
      {heights.flatMap((height, topIndex) => widths.map((width, c) => {
        const r = heights.length - 1 - topIndex;
        const key = `${r}:${c}`;
        const partial = r >= rows || c >= cols;
        const selected = !partial && r === row && c === col;
        const style = { aspectRatio: width / height };
        const label = `아래에서 ${r + 1}번째, 왼쪽에서 ${c + 1}번째 필름`;
        const marker = selected && <span className="module-marker" aria-hidden="true" />;
        if (partial) return <span key={key} className="module partial" style={style} aria-hidden="true" />;
        if (readOnly) return <span key={key} className="module" style={style} aria-hidden="true">{marker}</span>;
        return (
          <button key={key} type="button" role="radio" aria-checked={selected}
            aria-label={label} className={`module${selected ? " on" : ""}`} style={style}
            onClick={() => onChange(r, c)}>
            {marker}
          </button>
        );
      }))}
    </div>
  );
}

export function FilmLayout({ space, onChange }: { space: Space; onChange?: (row: number, col: number) => void }) {
  const win = resolveWindow(space);
  if (!win || validateWindow(space)) return null;
  const rows = Math.floor(win.height + 1e-6);
  const cols = Math.floor(win.width + 1e-6);
  const topRemainder = Math.max(0, win.height - rows);
  const rightRemainder = Math.max(0, win.width - cols);
  const canSelect = rows > 0 && cols > 0;
  const selected = win.captureModule;
  const row = selected && (selected.rowOrigin === "bottom" ? selected.row : rows - 1 - selected.row);
  return (
    <section className="film-layout" aria-label="필름 구성">
      <h3>필름 구성</h3>
      <FilmModulePicker rows={rows} cols={cols} topRemainder={topRemainder > 1e-6 ? topRemainder : 0}
        rightRemainder={rightRemainder > 1e-6 ? rightRemainder : 0} row={row ?? undefined}
        col={selected?.col} onChange={canSelect ? onChange : undefined} />
      {canSelect
        ? <p className="muted small">왼쪽 아래부터 1m 필름 {rows}행 × {cols}열을 배치해요. 마커를 띄울 필름을 선택해 주세요.</p>
        : <p className="muted small">마커를 표시하려면 가로·높이가 1m 이상인 창문이 필요해요.</p>}
      {(topRemainder > 1e-6 || rightRemainder > 1e-6) && <p className="muted small">회색 자투리 필름에는 마커를 표시할 수 없어요.</p>}
      {row != null && selected && <p role="status">선택한 필름: 아래에서 {row + 1}번째 · 왼쪽에서 {selected.col + 1}번째</p>}
    </section>
  );
}
