import { byGradeThenName, furnitureTypes, grades } from "./model";
import type { Space } from "./model";
import { formatMinute } from "./sunlight";
import type { BlockingSchedule } from "./sunlight";

export default function MobileFurnitureList({
  space,
  selected,
  selectedRegion,
  schedule,
  onFurniture,
  onRegion,
  onAdd,
  opensSettings = false,
}: {
  space: Space;
  selected: string | null;
  selectedRegion: string | null;
  schedule?: BlockingSchedule;
  onFurniture: (id: string) => void;
  onRegion: (id: string) => void;
  onAdd?: () => void;
  opensSettings?: boolean;
}) {
  const regions = space.regions.filter((r) => r.zone === space.zone);
  const rows = byGradeThenName([
    ...space.furniture
      .filter((f) => f.zone === space.zone)
      .map((f) => ({
        id: f.id,
        name: f.name,
        type: f.type ? furnitureTypes[f.type] : "기타",
        regions: regions.filter((r) => r.furnitureId === f.id),
        active: selected === f.id,
        pick: () => onFurniture(f.id),
      })),
    ...regions
      .filter((r) => !r.furnitureId)
      .map((r) => ({
        id: r.id,
        name: r.name,
        type: "직접 지정 영역",
        regions: [r],
        active: selectedRegion === r.id,
        pick: () => onRegion(r.id),
      })),
  ]);
  return (
    <section className={`mobile-furniture-list${opensSettings ? "" : " furniture-selector"}`} aria-label="가구와 보호 영역">
      <div className="section-row">
        <h3>가구 목록</h3>
        {!opensSettings && <span className="furniture-count">{rows.length}개</span>}
        {onAdd && (
          <button type="button" className="secondary" onClick={onAdd}>
            ＋ 가구 추가
          </button>
        )}
      </div>
      <div className="mobile-furniture-rows">
        {rows.map((row) => {
          const times = [
            ...new Set(
              row.regions.flatMap((r) =>
                (schedule?.get(r.id) ?? []).map(
                  (p) => `${formatMinute(p.arrival)}–${formatMinute(p.end)}`,
                ),
              ),
            ),
          ];
          // Furniture without a protection area is grade D (the user's default since 2026-11-06).
          const shownGrades = row.regions.length ? [...new Set(row.regions.map(r => r.grade))].sort() : ["D" as const];
          return (
            <button
              key={row.id}
              type="button"
              className={`mobile-furniture-row${row.active ? " selected" : ""}`}
              aria-pressed={row.active}
              onClick={row.pick}
            >
              <span>
                <strong className="furniture-row-title">
                  {row.name}
                </strong>
                {opensSettings && row.name !== row.type && <small>{row.type}</small>}
                {!opensSettings && (
                  <small className="furniture-grade-caption">
                    {/* The type repeats the name ("책장 01 · 책장"), so it shows only when the name doesn't say it. */}
                    {[
                      ...(row.name.includes(row.type) ? [] : [row.type]),
                      ...shownGrades.map(g => grades[g]), /* the same wording as the web panel: "차단 100% · 암막 커튼을 친 정도" */
                    ].join(" · ")}
                  </small>
                )}
                {times.length > 0 && (
                  <small className="furniture-sun-time">
                    햇빛 {times.join(", ")}
                  </small>
                )}
              </span>
              <span className="mobile-furniture-grades">
                {shownGrades.map(g => (
                  <span key={g} className={`furniture-grade grade-${g}`}>{g}</span>
                ))}
                {opensSettings && <svg
                  viewBox="0 0 20 20"
                  width="18"
                  height="18"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden="true"
                >
                  <path d="m8 5 5 5-5 5" />
                </svg>}
              </span>
            </button>
          );
        })}
      </div>
      {!rows.length && <p className="muted small">등록된 가구가 없습니다.</p>}
      {schedule && rows.some((r) => r.regions.length) && (
        <p className="list-footnote">햇빛 시간은 간이 계산 기준</p>
      )}
    </section>
  );
}
