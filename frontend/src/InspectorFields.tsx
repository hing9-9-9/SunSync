import type { SpaceApp } from "./useSpaceApp";
import NumberField from "./NumberField";
import RotateField from "./RotateField";
import { RegionTimeField } from "./ShadingPlan";
import { defaultShadingTime, furnitureTypes, grades } from "./model";
import type { FurnitureType, Grade } from "./model";

/**
 * The furniture / protection-area settings shared by the web side panel and the app sheet.
 * `compact` is the app wording: units in the labels and no helper line.
 */
export function InspectorFields({ app, compact }: { app: SpaceApp; compact: boolean }) {
  const { page, draft, setSelectedFurniture, setSelectedRegion, askDelete, furniture, region, changeFurniture, patchRegion, protectFurniture } = app;
  if (!draft) return null;
  return (
    <>
                      {furniture ? (
                        <>
                          <label className="field">
                            <span>가구 이름</span>
                            <input
                              value={furniture.name}
                              maxLength={30}
                              onChange={(e) =>
                                changeFurniture({
                                  ...furniture,
                                  name: e.target.value,
                                })
                              }
                            />
                          </label>
                          <label className="field">
                            <span>가구 종류</span>
                            <select
                              value={furniture.type ?? "other"}
                              onChange={(e) =>
                                changeFurniture({
                                  ...furniture,
                                  type: e.target.value as FurnitureType,
                                })
                              }
                            >
                              {(Object.keys(furnitureTypes) as FurnitureType[]).map((t) => (
                                <option key={t} value={t}>
                                  {furnitureTypes[t]}
                                </option>
                              ))}
                            </select>
                          </label>
                          {!compact && (
                            <p className="muted small">
                              {draft.source === "sample"
                                ? "종류별 기본 치수예요. 실제 치수로 고쳐 주세요."
                                : "직접 입력한 치수예요."}{" "}
                              단위는 m예요.
                            </p>
                          )}
                          <RotateField value={furniture.yaw ?? 0} onChange={(yaw) => changeFurniture({ ...furniture, yaw })} />
                          <div className="dimensions">
                            {(["width", "depth", "height"] as const).map(
                              (key, i) => (
                                <NumberField
                                  key={key}
                                  label={compact ? ["가로 (m)", "세로 (m)", "높이 (m)"][i] : ["가로", "세로", "높이"][i]}
                                  value={furniture[key]}
                                  min={0.01}
                                  onChange={(n) =>
                                    changeFurniture({ ...furniture, [key]: n })
                                  }
                                />
                              ),
                            )}
                          </div>
                          <div className="two-fields">
                            <NumberField
                              label={compact ? "서쪽 벽에서 (m)" : "가구 X 위치"}
                              value={furniture.x}
                              onChange={(x) =>
                                changeFurniture({ ...furniture, x })
                              }
                            />
                            <NumberField
                              label={compact ? "북쪽 벽에서 (m)" : "가구 Y 위치"}
                              value={furniture.y}
                              onChange={(y) =>
                                changeFurniture({ ...furniture, y })
                              }
                            />
                          </div>
                          <div className="furniture-actions">
                            {page === "protect" && !region && (
                              <button
                                className="secondary"
                                onClick={protectFurniture}
                              >
                                보호 영역 지정
                              </button>
                            )}
                            <button
                              className="text-button furniture-delete"
                              onClick={() => {
                                const linked = draft.regions.filter((r) => r.furnitureId === furniture.id).length;
                                askDelete(
                                  {
                                    ...draft,
                                    revision: draft.revision + 1,
                                    furniture: draft.furniture.filter(
                                      (f) => f.id !== furniture.id,
                                    ),
                                    regions: draft.regions.filter(
                                      (r) => r.furnitureId !== furniture.id,
                                    ),
                                  },
                                  `'${furniture.name}'을 삭제할까요?`,
                                  linked ? `이 가구에 지정한 보호 영역 ${linked}개도 함께 삭제됩니다.` : undefined,
                                  () => {
                                    setSelectedFurniture(null);
                                    setSelectedRegion(null);
                                  },
                                );
                              }}
                            >
                              가구 삭제
                            </button>
                          </div>
                        </>
                      ) : (
                        !region && (
                          <p className="muted">
                            도면에서 가구 선택 또는 가구 추가
                          </p>
                        )
                      )}
                      {page === "protect" && region && (
                        <>
                          {(!compact || furniture) && <div className="divider" />}
                          <label className="field">
                            <span>보호 영역 이름</span>
                            <input
                              value={region.name}
                              onChange={(e) =>
                                patchRegion({ name: e.target.value })
                              }
                            />
                          </label>
                          <div className="two-fields">
                            <NumberField
                              label="시작 높이 (m)"
                              value={region.bottom}
                              onChange={(bottom) => patchRegion({ bottom })}
                            />
                            <NumberField
                              label="끝 높이 (m)"
                              disabled={region.face === "상단" && !!furniture}
                              value={region.top}
                              min={0.01}
                              max={furniture?.height ?? draft.height}
                              onChange={(top) => patchRegion({ top })}
                            />
                          </div>
                          {!region.furnitureId && (
                            <>
                              <h3>
                                영역 위치·크기 <small>m</small>
                              </h3>
                              <div className="two-fields">
                                <NumberField
                                  label="영역 X"
                                  value={region.x}
                                  onChange={(x) => patchRegion({ x })}
                                />
                                <NumberField
                                  label="영역 Y"
                                  value={region.y}
                                  onChange={(y) => patchRegion({ y })}
                                />
                                <NumberField
                                  label="영역 가로"
                                  value={region.width}
                                  min={0.1}
                                  onChange={(width) => patchRegion({ width })}
                                />
                                <NumberField
                                  label="영역 세로"
                                  value={region.depth}
                                  min={0.1}
                                  onChange={(depth) => patchRegion({ depth })}
                                />
                              </div>
                            </>
                          )}
                          <fieldset className="grade-picker">
                            <legend>보호 등급</legend>
                            {(Object.keys(grades) as Grade[]).map((g) => (
                              <button
                                key={g}
                                aria-pressed={region.grade === g}
                                className={`chip ${region.grade === g ? "active" : ""}`}
                                onClick={() => patchRegion({ grade: g })}
                              >
                                {g}
                              </button>
                            ))}
                          </fieldset>
                          <p className="grade-description" aria-live="polite">
                            {grades[region.grade]}
                          </p>
                          {region.grade !== "D" && (
                            <RegionTimeField
                              base={draft.shadingSchedule ?? defaultShadingTime}
                              value={region.shadingSchedule}
                              onChange={(shadingSchedule) => patchRegion({ shadingSchedule })}
                            />
                          )}
                          <div className="furniture-actions">
                            <button
                              className="text-button furniture-delete region-delete"
                              onClick={() => {
                                askDelete(
                                  {
                                    ...draft,
                                    revision: draft.revision + 1,
                                    regions: draft.regions.filter(
                                      (r) => r.id !== region.id,
                                    ),
                                  },
                                  `'${region.name}'을 삭제할까요?`,
                                  undefined,
                                  () => setSelectedRegion(null),
                                );
                              }}
                            >
                              보호 영역 삭제
                            </button>
                          </div>
                        </>
                      )}
    </>
  );
}
