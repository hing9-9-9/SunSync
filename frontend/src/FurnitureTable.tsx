import { byGradeThenName, furnitureTypes } from "./model";
import type { Space, Region } from "./model";
import { formatMinute } from "./sunlight";
import type { BlockingSchedule } from "./sunlight";

export default function FurnitureTable({ space, selectedFurniture, selectedRegion, schedule, onFurniture, onRegion, onAdd }: {
  space: Space;
  selectedFurniture: string | null;
  selectedRegion: string | null;
  schedule?: BlockingSchedule;
  onFurniture: (id: string) => void;
  onRegion: (id: string) => void;
  onAdd: () => void;
}) {
  const regions = space.regions.filter(r => r.zone === space.zone);
  const rows = byGradeThenName([
    ...space.furniture.filter(f => f.zone === space.zone).map(f => ({
      id: f.id, name: f.name, type: f.type ? furnitureTypes[f.type] : "기타",
      regions: regions.filter(r => r.furnitureId === f.id),
      selected: selectedFurniture === f.id, select: () => onFurniture(f.id),
    })),
    // Freely drawn areas still need to be selectable and keep their own sunlight times.
    ...regions.filter(r => !r.furnitureId).map(r => ({
      id: r.id, name: r.name, type: "직접 지정 영역", regions: [r],
      selected: selectedRegion === r.id, select: () => onRegion(r.id),
    })),
  ]);
  function sunlight(areas: Region[]) {
    if (!schedule) return [];
    const periods = areas.flatMap(r => schedule.get(r.id) ?? [])
      .map(p => ({ start: p.arrival, end: p.end })).sort((a, b) => a.start - b.start);
    const merged: typeof periods = [];
    for (const p of periods) {
      const last = merged.at(-1);
      if (last && p.start <= last.end) last.end = Math.max(last.end, p.end);
      else merged.push({ ...p });
    }
    return merged;
  }
  return <section className="editor-furniture-list" aria-label="가구 목록">
    <div className="section-row">
      <h3>가구 목록</h3>
      <button type="button" className="secondary" onClick={onAdd}>＋ 가구 추가</button>
    </div>
    <div className="furniture-table-scroll">
      <table className="furniture-table">
        <thead><tr><th scope="col">가구</th><th scope="col">보호 등급</th><th scope="col">햇빛 닿는 시간 <small>간이 계산</small></th></tr></thead>
        <tbody>{rows.map(row => {
          const periods = sunlight(row.regions);
          const label = periods.length ? periods.map(p => `${formatMinute(p.start)}–${formatMinute(p.end)}`).join(", ")
            : schedule && row.regions.some(r => r.grade !== "D") ? "닿지 않음" : "—";
          const gradeKeys = [...new Set(row.regions.map(r => r.grade))].sort();
          return <tr key={row.id} className={row.selected ? "is-selected" : ""} onClick={row.select}>
            <th scope="row"><button type="button" aria-pressed={row.selected} onClick={e => { e.stopPropagation(); row.select(); }}>
              <span className="furniture-row-name">{row.name}</span><small className="furniture-row-type">{row.type}</small>
            </button></th>
            <td><span className="furniture-row-grades">{(gradeKeys.length ? gradeKeys : ["D"]).map(g => <span key={g} className={`furniture-grade grade-${g}`}>{g}</span>) /* no protection area = D (default since 2026-11-06) */}</span></td>
            <td><div className="furniture-exposure">
              <span className={periods.length ? "exposure-time" : "exposure-time is-empty"}>{label}</span>
              <div className="furniture-day-track" aria-hidden="true">{periods.map((p, i) => <i key={i} style={{left: `${p.start / 14.4}%`, width: `${(p.end - p.start) / 14.4}%`}} />)}</div>
            </div></td>
          </tr>;
        })}</tbody>
      </table>
      {!rows.length && <p className="muted small">등록된 가구가 없습니다.</p>}
    </div>
  </section>;
}
