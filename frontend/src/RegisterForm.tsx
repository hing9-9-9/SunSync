import type { SpaceApp } from "./useSpaceApp";
import NumberField from "./NumberField";
import { FilmLayout } from "./FilmModulePicker";
import { isWidthWall, margin, wallLength, walls } from "./model";
import type { Wall } from "./model";
import { clearFieldIssue } from "./formIssues";
import { Card } from "./ShellParts";

/**
 * The space information form shared by the web and the app layouts. The fields are the same in both;
 * only how they are grouped into cards differs: the web uses three cards in two columns (`split`),
 * the app one card for the names and one for everything else, stacked.
 */
export function RegisterForm({ app, split }: { app: SpaceApp; split: boolean }) {
  const { isApp, spaces, draft, draftWarning, saving, locationStatus, locating, locateBuilding, change, save, zoneTabs,
    registerSubmit, draftWindow, changeWindow, draftSite, continueManually } = app;
  if (!draft) return null;
  const info = (
    <>
                      <h3>공간 정보</h3>
                      {zoneTabs}
                      <label className="field">
                        <span>건물 이름</span>
                        <input
                          required
                          list="buildings"
                          value={draft.building}
                          placeholder="예: 햇살도서관"
                          onChange={(e) => change({ building: e.target.value })}
                        />
                      </label>
                      <datalist id="buildings">
                        {[...new Set(spaces.map((s) => s.building))].map(
                          (b) => (
                            <option key={b} value={b} />
                          ),
                        )}
                      </datalist>
                      <div className="two-fields">
                        <label className="field">
                          <span>층 / 위치</span>
                          <input
                            required
                            value={draft.floor}
                            placeholder="예: 1층"
                            onChange={(e) => change({ floor: e.target.value })}
                          />
                        </label>
                        <label className="field">
                          <span>공간 이름</span>
                          <input
                            required
                            value={draft.name}
                            placeholder="예: 열람실"
                            onChange={(e) => change({ name: e.target.value })}
                          />
                        </label>
                      </div>
    </>
  );
  const size = (
    <>
                      <h3>
                        공간 크기 <small>m</small>
                      </h3>
                      <div className="dimensions">
                        <NumberField
                          label="공간 가로"
                          value={draft.width}
                          min={0.1}
                          onChange={(width) => change({ width })}
                        />
                        <NumberField
                          label="공간 세로"
                          value={draft.depth}
                          min={0.1}
                          onChange={(depth) => change({ depth })}
                        />
                        <NumberField
                          label="공간 높이"
                          value={draft.height}
                          min={0.1}
                          onChange={(height) => change({ height })}
                        />
                      </div>
    </>
  );
  const windowFields = (
    <>
                      <h3>
                        창문 규격·위치 <small>m</small>
                      </h3>
                      <div className="two-fields">
                        <NumberField
                          label="창문 가로"
                          value={draft.windowDimensions?.width ?? NaN}
                          min={0.1}
                          onChange={(width) => changeWindow({ width })}
                        />
                        <NumberField
                          label="창문 높이"
                          value={draft.windowDimensions?.height ?? NaN}
                          min={0.1}
                          onChange={(height) => changeWindow({ height })}
                        />
                      </div>
                      <label className="field">
                        <span>창문이 있는 벽</span>
                        <select
                          value={draftWindow?.wall ?? "south"}
                          disabled={!draftWindow}
                          onChange={(e) =>
                            changeWindow({ wall: e.target.value as Wall, offset: undefined })
                          }
                        >
                          {(Object.keys(walls) as Wall[]).map((w) => (
                            <option key={w} value={w}>
                              {walls[w]}
                            </option>
                          ))}
                        </select>
                      </label>
                      {draftWindow && (
                        <>
                          {/* Right and top margins are derived: editing them rewrites offset/sill, the stored values. */}
                          <div className="two-fields">
                            <NumberField
                              label={
                                isWidthWall(draftWindow.wall)
                                  ? "왼쪽 여백 · 서쪽 끝부터"
                                  : "왼쪽 여백 · 북쪽 끝부터"
                              }
                              value={draftWindow.offset}
                              onChange={(offset) => changeWindow({ offset })}
                            />
                            <NumberField
                              label={
                                isWidthWall(draftWindow.wall)
                                  ? "오른쪽 여백 · 동쪽 끝까지"
                                  : "오른쪽 여백 · 남쪽 끝까지"
                              }
                              value={margin(wallLength(draft, draftWindow.wall) - draftWindow.offset - draftWindow.width)}
                              onChange={(right) =>
                                changeWindow({ offset: margin(wallLength(draft, draftWindow.wall) - draftWindow.width - right) })
                              }
                            />
                            <NumberField
                              label="위 여백 · 천장까지"
                              value={margin(draft.height - draftWindow.sill - draftWindow.height)}
                              onChange={(top) =>
                                changeWindow({ sill: margin(draft.height - draftWindow.height - top) })
                              }
                            />
                            <NumberField
                              label="아래 여백 · 바닥부터"
                              value={draftWindow.sill}
                              onChange={(sill) => changeWindow({ sill })}
                            />
                          </div>
                          <FilmLayout space={draft} onChange={(row, col) => changeWindow({ captureModule: { row, col, rowOrigin: "bottom" } })} />
                        </>
                      )}
    </>
  );
  const location = (
    <>
                      <div className="section-row location-heading">
                        <h3>건물 위치</h3>
                        <button
                          type="button"
                          className="secondary"
                          disabled={locating}
                          onClick={() =>
                            draft && locateBuilding(draft.id, true)
                          }
                        >
                          {locating ? "위치 확인 중…" : "현재 위치로 입력"}
                        </button>
                      </div>
                      {locationStatus && (
                        <p className="muted small" role="status">
                          {locationStatus}
                        </p>
                      )}
                      <div className="two-fields">
                        <NumberField
                          label="위도"
                          value={draftSite.latitude}
                          min={-90}
                          max={90}
                          step={0.000001}
                          precision={6}
                          onChange={(latitude) =>
                            change({ site: { ...draftSite, latitude } })
                          }
                        />
                        <NumberField
                          label="경도"
                          value={draftSite.longitude}
                          min={-180}
                          max={180}
                          step={0.000001}
                          precision={6}
                          onChange={(longitude) =>
                            change({ site: { ...draftSite, longitude } })
                          }
                        />
                      </div>
                      <label className="field">
                        <span>시간대</span>
                        <input
                          required
                          value={draftSite.timezone}
                          onChange={(e) =>
                            change({
                              site: { ...draftSite, timezone: e.target.value },
                            })
                          }
                        />
                      </label>
    </>
  );
  return (
                <form id="register-form" noValidate onSubmit={registerSubmit} onInput={(e) => clearFieldIssue(e.target)} className="registration">
                  <p className="step-label">1/3단계, 공간 정보</p>
                  <h2>건물과 공간 등록</h2>
      <div className="two-column">
        {split ? (
          <>
            <Card className="reg-info">{info}{size}</Card>
            <Card className="reg-window">{windowFields}</Card>
            <Card className="reg-location">{location}</Card>
          </>
        ) : (
          <>
            <Card>{info}</Card>
            <Card>
              {size}
              {windowFields}
              <div className="divider" />
              {location}
            </Card>
          </>
        )}
      </div>
                  <div className="actions">
                    {/* In the app the primary of these two lives in the bottom action bar. */}
                    {!isApp && spaces.some((s) => s.id === draft.id) && (
                      <button
                        type="button"
                        className="primary"
                        disabled={saving || !!draftWarning}
                        onClick={save}
                      >
                        {saving ? "저장 중…" : "변경사항 저장"}
                      </button>
                    )}
                    {(!isApp || spaces.some((s) => s.id === draft.id)) && (
                    <button
                      type="submit"
                      className={
                        spaces.some((s) => s.id === draft.id)
                          ? "secondary"
                          : "primary"
                      }
                    >
                      {isApp ? "QR 인식으로 계속" : "QR 촬영 안내로 계속"}
                    </button>
                    )}
                    {(
                      <button
                        type="button"
                        className="secondary"
                        onClick={(e) => continueManually(e.currentTarget.form)}
                      >
                        직접 가구 추가·수정
                      </button>
                    )}
                  </div>
    </form>
  );
}
