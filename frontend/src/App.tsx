import { lazy, Suspense } from "react";
import { WebLoginGoogleStep } from "./WebLoginStep";
import NumberField from "./NumberField";
import RotateField from "./RotateField";
import { FilmLayout } from "./FilmModulePicker";
import FurnitureTable from "./FurnitureTable";
import "./ReferencePreview.css";
import "./WorkspaceDesign.css";
import { DeviceHandoff } from "./DeviceHandoff";
import { CaptureHelp } from "./CaptureHelp";
import { ShadingTimeControl, VirtualFilmControl } from "./ShadingPlan";
import { SpaceMenu, SpaceMenuGlyph } from "./SpaceMenu";
import AdaptiveEffect from "./AdaptiveEffect.tsx";
import SpatialCapture from "./SpatialCapture";
import {
  defaultShadingTime,
  furnitureTypes,
  grades,
  isWidthWall,
  margin,
  wallLength,
  walls,
} from "./model";
import type {
  FurnitureType,
  Grade,
  Wall,
} from "./model";
import "./App.css";

import { AwardHeader, AwardLanding, AwardSpaces, AwardGuide, AwardFooter } from "./AwardSystem";
import { AccountOverview, AuthPanel } from "./AccountPage";
import { useSpaceApp, titles } from "./useSpaceApp";
import { clearFieldIssue } from "./formIssues";
import { Icon, Card } from "./ShellParts";
// The web detail plan (2D top view and 3D) is a three.js scene, loaded with the detail page only.
// Start fetching the 3D view at once, so the detail page rarely waits on it.
const load3D = () => import("./Space3DView.tsx");
void load3D();
const Space3DView = lazy(load3D);
// Its stylesheet loads with the page so the placeholder below has the stage's exact frame.
import "./Space3DView.css";

function App() {
  const {
    isApp,
    handoffLink,
    deviceSession,
    measurementJobs,
    measurementReady,
    account,
    authStatus,
    authNotice,
    startLogin,
    signOut,
    spaces,
    page,
    draft,
    setDraft,
    selectedFurniture,
    selectedRegion,
    chooseRegion,
    setSelectedFurniture,
    setSelectedRegion,
    tool,
    setTool,
    modal,
    setModal,
    error,
    setEditedDraftId,
    draftWarning,
    savedOverlapWarning,
    setPage,
    toast,
    pendingDelete,
    confirmDelete,
    askDelete,
    guideIndex,
    setGuideIndex,
    addingByQr,
    setAddingByQr,
    rememberQrReturn,
    progress,
    setProgress,
    hour,
    setHour,
    storageError,
    saving,
    serverState,
    serverError,
    unavailable,
    conflict,
    locationStatus,
    locating,
    receiveMeasurement,
    joinMeasurement,
    locateBuilding,
    modalRef,
    errorRef,
    active,
    editing,
    space,
    sunSchedule,
    shadingEdit,
    setShadingEdit,
    detailShading,
    furniture,
    region,
    refreshServer,
    reloadServerDraft,
    transferSpace,
    unsaved,
    leaveUnsaved,
    tempDrafts,
    tempDeleteId,
    askDeleteTempDraft,
    confirmDeleteTempDraft,
    resumeTempDraft,
    leaveEditor,
    navigate,
    requireAccount,
    dismissModal,
    begin,
    selectSpace,
    saveShadingTime,
    editSpace,
    setActiveId,
    change,
    changeFurniture,
    chooseFurniture,
    patchRegion,
    protectFurniture,
    save,
    renameZone,
    zoneTabs,
    finishCapture,
    addFurniture,
    addManualFurniture,
    confirmReview,
    registerSubmit,
    plan,
    navItems,
    isActive,
    actionBar,
    draftWindow,
    changeWindow,
    draftSite,
    syncFailed,
    retryUpload,
    sunSource,
    filmPrediction,
    continueManually,
    deleteSpace,
    deleting,
    predictionDate,
    predictionKey,
    sunpathResult,
  } = useSpaceApp();
  // Home, guide and the example need no login; everything else waits for the session answer.
  const publicPage = page === "home" || page === "guide";
  const checking = authStatus === "checking" && !publicPage;
  return (
    <>
      <div
        inert={saving || undefined}
        aria-busy={saving}
        className={`app ap award-app ${isApp ? "mode-app" : "mode-web"} page-${page} ${page === "more" ? "more-page" : ""} ${!account || checking ? "auth-page" : ""} ${actionBar || (isApp && page === "capture" && draft) ? "has-action-bar" : ""}`}
      >
        {account && (
          <button
            type="button"
            className="skip-link"
            onClick={() => document.getElementById(page === "guide" ? "award-guide-main" : page === "home" ? "ap-main" : "main")?.focus()}
          >
            본문으로 건너뛰기
          </button>
        )}
        {<AwardHeader account={account} page={page} editing={editing} onNavigate={p=>{if(p==="spaces"||p==="account")requireAccount(()=>navigate(p),p);else navigate(p)}} onLogin={()=>requireAccount(()=>navigate("spaces"),"spaces")}/>}
        {page === "home" && <AwardLanding onStart={()=>requireAccount(()=>navigate("spaces"),"spaces")} onGuide={()=>navigate("guide")}/>}
        {page === "guide" && (<AwardGuide index={guideIndex} onIndex={setGuideIndex} onStart={()=>requireAccount(()=>navigate("spaces"),"spaces")}/>)}
        {checking ? (
          <main className="auth acct-auth" aria-busy="true" />
        ) : !account ? (
          <AuthPanel hidden={publicPage} brand={<Icon name="sun" />} notice={authNotice} onLogin={() => setModal("login")} />
        ) : (
          <>
            <main id="main" className="main" tabIndex={-1} onChangeCapture={() => setEditedDraftId(draft?.id ?? null)}>
              {draftWarning && <p className="live-validation" role="alert">{draftWarning}</p>}
              {editing && space && (
                <div className="space-context desktop-only">
                  <span>내 공간</span>
                  <span aria-hidden="true">/</span>
                  <strong>
                    {space.building || "새 건물"}, {space.name || "새 공간"}
                  </strong>
                  {/* Save state rides on the existing "편집 중" label, so nothing shifts when it appears. */}
                  <span className="context-mode" role="status">
                    {editing ? `편집 중${!unsaved ? "" : !leaveUnsaved ? " · 임시 저장됨" : " · 저장되지 않은 변경사항"}` : "공간 현황"}
                  </span>
                </div>
              )}
              {editing && (
                <ol
                  className="space-workflow desktop-only"
                  aria-label="공간 등록 단계"
                >
                  {[
                    "공간 정보",
                    "휴대폰 측정 (선택)",
                    "가구·보호 영역 편집",
                  ].map((label, i) => {
                    const current =
                      page === "register"
                        ? 0
                        : ["capture", "processing"].includes(page)
                          ? 1
                          : 2;
                    return (
                      <li
                        key={label}
                        aria-current={i === current ? "step" : undefined}
                        className={i < current && i !== 1 ? "complete" : ""}
                      >
                        <span>
                          {i < current && i !== 1 ? (
                            <svg viewBox="0 0 16 16" width="12" height="12" aria-label="완료" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="m3.5 8.5 3 3 6-7" />
                            </svg>
                          ) : (
                            i + 1
                          )}
                        </span>
                        {label}
                      </li>
                    );
                  })}
                </ol>
              )}
              {/* The web editor's command bar carries the screen's h1. */}
              {(isApp || (page !== "review" && page !== "protect")) && (
              <div className="page-heading">
                <div>
                  <h1>
                    {page === "register" &&
                    draft &&
                    spaces.some((s) => s.id === draft.id)
                      ? "공간 정보 수정"
                      : page === "detail" && active ? active.name : titles[page]}
                  </h1>
                  {page === "detail" && active && <p className="page-subtitle">{active.building} {active.floor}</p>}
                </div>
                {["home", "spaces"].includes(page) && (
                  <button
                    className="icon-button plus"
                    aria-label="공간 추가"
                    onClick={begin}
                  >
                    ＋
                  </button>
                )}
                {page === "detail" && active && (
                  <div className="heading-actions">
                    <button type="button" className="secondary" onClick={() => editSpace(active)}><SpaceMenuGlyph name="layout" />배치 편집</button>
                    <SpaceMenu
                      label="공간 더보기"
                      items={[
                        { label: "공간 정보 수정", icon: "info", onSelect: () => editSpace(active, "register") },
                        { label: "공간 삭제", icon: "trash", danger: true, onSelect: () => setModal("delete-space") },
                        ...(isApp ? [{ label: "웹 페이지 이동", icon: "external" as const, onSelect: () => { window.open(`${location.origin}/?mode=web#/detail/${active.id}`, "_blank", "noopener"); } }] : []),
                      ]}
                    />
                  </div>
                )}
              </div>
              )}
              {storageError && (
                <p className="error" role="alert">
                  저장 공간을 사용할 수 없습니다. 변경 내용이 새로고침 후 사라질
                  수 있습니다.
                </p>
              )}
              {error && (
                <p ref={errorRef} tabIndex={-1} className="error" role="alert">
                  {error}
                </p>
              )}
              {page === "detail" && active && savedOverlapWarning && (
                <div className="notice overlap-notice" role="status">
                  <span>{savedOverlapWarning}</span>
                  <button type="button" className="text-button" onClick={() => editSpace(active)}>
                    가구 위치 수정
                  </button>
                </div>
              )}
              {(page === "spaces") && (
                <div className="list-content">
                  {<AwardSpaces status={<>{/* Show connection feedback only while loading or when attention is needed. */}
                  {serverState !== "online" && (
                  <div className="server-sync-row">
                    <span>
                      {serverState === "loading"
                        ? "저장한 공간 불러오는 중…"
                        : "서버에 연결되지 않음"}
                    </span>
                    <button
                      className="text-button"
                      disabled={serverState === "loading"}
                      onClick={refreshServer}
                    >
                      목록 새로고침
                    </button>
                  </div>
                  )}
                  {serverError && (
                    <p className="notice" role="status">
                      {serverError} 기존 브라우저 자료는 유지됩니다.
                    </p>
                  )}</>} spaces={spaces} jobs={measurementJobs} syncFailed={syncFailed} onRetrySync={retryUpload} onSelect={selectSpace} onEdit={(s, at) => editSpace(s, at)} onDelete={(s) => { setActiveId(s.id); setModal("delete-space"); }} onStart={begin} onMeasurement={job=>{const s=spaces.find(s=>s.id===job.space_id);if(isApp && job.status!=="completed")joinMeasurement(job);else if(s){selectSpace(s);if(job.status!=="completed"){setDraft(structuredClone(s));setPage("capture");}}}}/>}
                  {page === "spaces" && tempDrafts.length > 0 && (
                    <section className="temp-drafts" aria-labelledby="temp-drafts-title">
                      <h2 id="temp-drafts-title">작성 중인 공간</h2>
                      {tempDrafts.map((t) => (
                        <div className="row" key={t.space.id}>
                          <button type="button" className="temp-draft-open" onClick={() => resumeTempDraft(t)}>
                            <span>
                              {t.space.name || "이름 없는 공간"}
                              <small>
                                {[t.space.building, t.space.floor].filter(Boolean).join(" ") || "건물 정보 없음"} · {new Date(t.savedAt).toLocaleDateString("ko-KR", { month: "long", day: "numeric" })} 임시 저장
                              </small>
                            </span>
                            <span className="status-pill todo">작성 중</span>
                          </button>
                          <button type="button" className="text-button destructive-text" onClick={() => askDeleteTempDraft(t.space.id)}>
                            삭제
                          </button>
                        </div>
                      ))}
                    </section>
                  )}
                  {unavailable.length > 0 && (
                    <section className="external-spaces">
                      <h2>다른 형식의 공간</h2>
                      {unavailable.map((s) => (
                        <div className="row" key={s.scene_id}>
                          <span>
                            {s.name ?? s.scene_id}
                            <small>
                              {s.reason} 기존 서버 데이터는 유지됩니다.
                            </small>
                          </span>
                        </div>
                      ))}
                    </section>
                  )}
                </div>
              )}
              {page === "register" && draft && (
                <form id="register-form" noValidate onSubmit={registerSubmit} onInput={(e) => clearFieldIssue(e.target)} className="registration">
                  <p className="step-label">1/3단계, 공간 정보</p>
                  <h2>건물과 공간 등록</h2>
                  <div className="two-column">
                    <Card>
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
                      <label className="field">
                        <span>측정 구역</span>
                        <input
                          required
                          value={draft.zone}
                          onChange={(e) => renameZone(e.target.value)}
                        />
                      </label>
                    </Card>
                    <Card>
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
                          <FilmLayout space={draft} onChange={(row, col) => changeWindow({ captureModule: { row, col } })} />
                        </>
                      )}
                      <div className="mini-room">
                        <span>
                          {draft.width || "—"} × {draft.depth || "—"} m
                        </span>
                        <span>창</span>
                      </div>
                      <p className="muted small">
                        공간 크기는 직접 입력 후 도면에서 확인
                      </p>
                      <div className="divider" />
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
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={draftSite.northConfirmed}
                          onChange={(e) =>
                            change({
                              site: {
                                ...draftSite,
                                northConfirmed: e.target.checked,
                              },
                            })
                          }
                        />
                        <span>도면의 위쪽이 북쪽임을 확인</span>
                      </label>
                    </Card>
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
              )}
              {page === "capture" && draft && !isApp && (
                <Card className="qr-web-guide">
                  <h2>앱에서 공간 인식</h2>
                  <ol className="instruction-list">
                    <li>창문 규격·위치 입력 후 필름에 QR 마커 표시</li>
                    <li>앱에서 가구 이름과 상판 형태 선택</li>
                    <li>형태별 촬영 지점에서 창문 QR 마커를 지점마다 3장 촬영</li>
                    <li>마커가 다 보이지 않거나 흔들리면 앱이 재촬영 안내</li>
                    <li>촬영이 끝나면 웹에서 도면 확인 후 가구·보호 영역 편집</li>
                  </ol>
                  <button
                    className="primary"
                    onClick={() => transferSpace("app")}
                  >
                    휴대폰에서 촬영
                  </button>
                  {deviceSession && (
                    <DeviceHandoff
                      session={deviceSession}
                      onComplete={receiveMeasurement}
                    />
                  )}
                  {measurementReady && (
                    <button
                      className="primary"
                      onClick={() => setPage("protect")}
                    >
                      측정 완료, 도면 확인
                    </button>
                  )}
                  {handoffLink && (
                    <>
                      <a className="secondary handoff-link" href={handoffLink}>
                        앱 촬영 화면 열기
                      </a>
                      <label className="field">
                        <span>휴대폰에서 열 링크</span>
                        <input
                          readOnly
                          value={handoffLink}
                          onFocus={(e) => e.target.select()}
                        />
                      </label>
                    </>
                  )}
                  <button
                    className="secondary"
                    onClick={() => setPage("protect")}
                  >
                    직접 가구 추가·도면 편집
                  </button>
                </Card>
              )}
              {page === "capture" && draft && isApp && (
                <div className="capture-layout">
                  <div>
                    <p className="step-label">2/3단계, 휴대폰으로 공간 촬영</p>
                    <h2>
                      {addingByQr
                        ? "추가 가구 모서리 촬영"
                        : "가구 모서리에서 QR 촬영"}
                    </h2>
                    {zoneTabs}
                    {draft.windowDimensions?.width &&
                    draft.windowDimensions?.height ? (
                      <SpatialCapture
                        sceneId={draft.serverSceneId}
                        key={`${draft.id}:${draft.zone}`}
                        count={
                          addingByQr
                            ? draft.furniture.filter((f) => f.zone === draft.zone).length
                            : 0
                        }
                        onFinish={finishCapture}
                      />
                    ) : (
                      <Card>
                        <p>촬영 전 창문 규격 확인 필요</p>
                        <button
                          className="primary"
                          onClick={() => setPage("register")}
                        >
                          창문 규격 입력
                        </button>
                      </Card>
                    )}
                  </div>
                  <Card className="capture-controls">
                    <h3>촬영 순서</h3>
                    <ol className="instruction-list">
                      <li>창문 규격 확인 및 필름의 QR 마커 표시</li>
                      <li>촬영할 가구의 이름과 상판 형태 선택</li>
                      <li>형태 도면의 P1부터 순서대로 이동하며 지점마다 3장 촬영</li>
                      <li>재촬영 안내가 나오면 안내대로 같은 지점에서 다시 촬영</li>
                      <li>모든 가구 촬영 후 도면과 가로·세로·높이 확인</li>
                    </ol>
                    <button
                      className="secondary"
                      onClick={() => setModal("capture-help")}
                    >
                      촬영 방법 보기
                    </button>
                  </Card>
                </div>
              )}
              {page === "processing" && (
                <Card className="processing">
                  <p className="step-label">2/3단계, 휴대폰 사진 분석</p>
                  <h2>측정 도면 생성 중</h2>
                  <strong className="progress-number">
                    {progress}
                    <small>%</small>
                  </strong>
                  <progress
                    aria-label="도면 생성 진행률"
                    value={progress}
                    max={100}
                  />
                  <p className="muted small">촬영한 가구를 도면에 배치하는 중</p>
                  <button
                    className="secondary"
                    onClick={() => {
                      setProgress(0);
                      setPage("capture");
                    }}
                  >
                    분석 취소
                  </button>
                </Card>
              )}
              {(page === "review" || page === "protect") && draft && (
                <>
                  {!isApp && <div className="editor-command-bar">
                    <div>
                      <h1>{draft.name || "새 공간"}</h1>
                    </div>
                    <div className="editor-command-actions">
                      <button
                        className="primary"
                        disabled={saving || !!draftWarning}
                        onClick={save}
                      >
                        {saving ? "저장 중…" : "저장"}
                      </button>
                      <button
                        className="secondary"
                        onClick={() => navigate(spaces.some((s) => s.id === draft.id) ? "detail" : "spaces")}
                      >
                        닫기
                      </button>
                    </div>
                  </div>}
                  <p className="step-label editor-step-label">
                    {page === "review"
                      ? "3/3단계, 가구 위치와 치수 확인"
                      : "3/3단계, 보호 영역과 높이 설정"}
                  </p>
                  <div className="workspace editor-workspace">
                    <div className="workspace-main">
                      {zoneTabs}
                      <div className="toolbar">
                        {page === "protect" && (
                          <>
                            <button
                              className={`chip ${tool === "furniture" ? "active" : ""}`}
                              aria-pressed={tool === "furniture"}
                              onClick={() => setTool("furniture")}
                            >
                              가구 선택
                            </button>
                            <button
                              className={`chip ${tool === "region" ? "active" : ""}`}
                              aria-pressed={tool === "region"}
                              onClick={() => setTool("region")}
                            >
                              영역 그리기
                            </button>
                          </>
                        )}
                      </div>
                      <Card className="plan-card">{plan}</Card>
                      {page === "protect" && (
                        <Card className="shading-time-card">
                          <ShadingTimeControl
                            compact
                            space={draft}
                            value={draft.shadingSchedule ?? defaultShadingTime}
                            onChange={(shadingSchedule) => change({ shadingSchedule })}
                            sunSchedule={sunSchedule}
                            hour={hour}
                          />
                        </Card>
                      )}
                      <FurnitureTable
                        space={draft}
                        selectedFurniture={selectedFurniture}
                        selectedRegion={selectedRegion}
                        schedule={page === "protect" ? sunSchedule : undefined}
                        onFurniture={chooseFurniture}
                        onRegion={chooseRegion}
                        onAdd={addFurniture}
                      />
                    </div>
                    <aside className="inspector">
                      <h2>
                        {region ? "선택한 보호 영역 설정" : "선택한 가구 설정"}
                      </h2>
                      {furniture ? (
                        <>
                          <label className="field">
                            <span>가구 이름</span>
                            <input
                              value={furniture.name}
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
                          <p className="muted small">
                            {draft.source === "sample"
                              ? "종류별 기본 치수예요. 실제 치수로 고쳐 주세요."
                              : "직접 입력한 치수예요."}{" "}
                            단위는 m예요.
                          </p>
                          <RotateField value={furniture.yaw ?? 0} onChange={(yaw) => changeFurniture({ ...furniture, yaw })} />
                          <div className="dimensions">
                            {(["width", "depth", "height"] as const).map(
                              (key, i) => (
                                <NumberField
                                  key={key}
                                  label={["가로", "세로", "높이"][i]}
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
                              label="가구 X 위치"
                              value={furniture.x}
                              onChange={(x) =>
                                changeFurniture({ ...furniture, x })
                              }
                            />
                            <NumberField
                              label="가구 Y 위치"
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
                          <div className="divider" />
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
                      <div className="actions">
                        {page === "review" ? (
                          <>
                            {!isApp && (
                            <button className="primary" onClick={confirmReview}>
                              확인하고 보호 영역 지정
                            </button>
                            )}
                            <button
                              className="secondary"
                              onClick={() => setPage("capture")}
                            >
                              다시 측정
                            </button>
                            {isApp && (
                              <button
                                className="text-button"
                                onClick={() => navigate(spaces.some((s) => s.id === draft.id) ? "detail" : "spaces")}
                              >
                                닫기
                              </button>
                            )}
                          </>
                        ) : (
                          <>
                            {!isApp && (
                            <button
                              className="primary save-protection"
                              onClick={save}
                              disabled={saving}
                            >
                              보호 영역 저장
                            </button>
                            )}
                            {conflict && (
                              <button
                                className="secondary"
                                onClick={() => setModal("reload-server")}
                              >
                                서버의 최신 내용 불러오기
                              </button>
                            )}
                            {isApp && (
                              <button
                                className="text-button"
                                onClick={() => navigate(spaces.some((s) => s.id === draft.id) ? "detail" : "spaces")}
                              >
                                닫기
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </aside>
                  </div>
                </>
              )}
              {page === "detail" && active && (
                <div className="detail-3d">
                  {active.zones.length > 1 && zoneTabs}
                  {/* While three.js loads, an empty stage of the same size holds the place: showing the
                      old simple plan here flashed an outdated drawing on reload. */}
                  <Suspense fallback={<div className="s3 s3-loading" role="status" aria-label="도면을 불러오는 중"><div className="s3-head" /><div className="s3-body"><div className="s3-stage" /></div></div>}>
                    <Space3DView
                      key={`${active.id}:${active.serverRevision ?? active.revision}:${active.zone}`}
                      space={active}
                      hour={hour}
                      onHour={setHour}
                      date={predictionDate}
                      selected={selectedFurniture}
                      onSelect={chooseFurniture}
                      shading={detailShading}
                      planned={!!predictionKey}
                      sunpath={sunpathResult}
                      film={filmPrediction}
                    />
                  </Suspense>
                  {/* Below the stage only what people set or do; what the stage already shows is not repeated. */}
                  <div className="detail-settings">
                    <Card className="detail-shading">
                      <ShadingTimeControl
                        space={active}
                        value={detailShading}
                        onChange={setShadingEdit}
                        sunSchedule={sunSchedule}
                        hour={hour}
                        onSave={saveShadingTime}
                        sunSource={sunSource}
                        dirty={
                          !!shadingEdit &&
                          JSON.stringify(shadingEdit) !==
                            JSON.stringify(active.shadingSchedule ?? defaultShadingTime)
                        }
                        saving={saving}
                      />
                    </Card>
                    <Card className="detail-film">
                      <VirtualFilmControl space={active} prediction={filmPrediction} hour={hour}>
                        {filmPrediction?.plan && <AdaptiveEffect space={active} start={filmPrediction.plan.start_time} />}
                      </VirtualFilmControl>
                    </Card>
                  </div>
                </div>
              )}
              {(page === "account" || page === "more") && (
                <AccountOverview
                  account={account}
                  spaces={spaces}
                  serverState={serverState}
                  onOpen={selectSpace}
                  onRegister={begin}
                  onGuide={() => navigate("guide")}
                  onRefresh={refreshServer}
                  onLogout={() => setModal("logout")}
                />
              )}
            </main>
            {actionBar && <div className="app-action-bar">{actionBar}</div>}
            <nav className="mobile-nav" aria-label="앱 메뉴">
              {navItems.map((item) => (
                <button
                  key={item.page}
                  aria-current={isActive(item.page) ? "page" : undefined}
                  className={isActive(item.page) ? "active" : ""}
                  onClick={() => navigate(item.page)}
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    {item.page === "home" && (
                      <path d="m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9" />
                    )}
                    {item.page === "spaces" && (
                      <path d="M4 4h16v16H4zM4 12h16M12 4v16" />
                    )}
                    {item.page === "guide" && (
                      <>
                        <path d="M12 6c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v15" />
                      </>
                    )}
                    {item.page === "more" && (
                      <path d="M4 6h16M4 12h16M4 18h16" />
                    )}
                  </svg>
                  {item.mobile}
                </button>
              ))}
            </nav>
          </>
        )}
        {<AwardFooter onHome={()=>navigate("home")}/>}
        <dialog
          ref={modalRef}
          className={modal === "login" ? "acct-login-dialog" : modal === "add-furniture" ? "furniture-add-dialog" : undefined}
          tabIndex={-1}
          onCancel={dismissModal}
          onClick={(e) => {
            if (e.target === e.currentTarget) dismissModal();
          }}
        >
          {modal === "login" && (
            <form onSubmit={(e) => e.preventDefault()}>
              <aside className="acct-login-visual" aria-hidden="true">
                <div className="acct-auth-glow" />
                <div className="acct-wordmark">SunSync</div>
                <p>
                  햇빛은 들이고,
                  <br />
                  <mark className="ap-mark">공간은 지킨다.</mark>
                </p>
                <span className="ap-state-chip on">보호 중</span>
              </aside>
              <WebLoginGoogleStep notice={authNotice} onGoogle={startLogin} onCancel={dismissModal} />
            </form>
          )}
          {modal === "delete-space" && active && (
            <>
              <h2>'{active.name}'을 삭제할까요?</h2>
              <p className="muted">
                이 공간의 가구·보호 영역 정보도 함께 사라지고 되돌릴 수 없습니다.
              </p>
              <button className="primary destructive" disabled={deleting} onClick={deleteSpace}>
                {deleting ? "삭제 중…" : "삭제"}
              </button>
              <button className="secondary" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          {modal === "delete-item" && pendingDelete && (
            <>
              <h2>{pendingDelete.title}</h2>
              <p className="muted">
                {pendingDelete.body ? `${pendingDelete.body} ` : ""}삭제한 내용은 저장할 때 반영됩니다.
              </p>
              <button className="primary destructive" onClick={confirmDelete}>
                삭제
              </button>
              <button className="secondary" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          {modal === "delete-temp" && (
            <>
              <h2>
                '{tempDrafts.find((t) => t.space.id === tempDeleteId)?.space.name || "이름 없는 공간"}'의 작성 중인 내용을 삭제할까요?
              </h2>
              <p className="muted">
                이 기기에 임시 저장한 내용이 사라지고 되돌릴 수 없습니다. 서버에 저장된 공간은 그대로 남습니다.
              </p>
              <button className="primary destructive" onClick={confirmDeleteTempDraft}>
                삭제
              </button>
              <button className="secondary" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          {modal === "reload-server" && (
            <>
              <h2>서버의 최신 내용으로 바꿀까요?</h2>
              <p className="muted">
                저장하지 않은 현재 수정 내용은 사라집니다.
              </p>
              <button className="primary" onClick={reloadServerDraft}>
                최신 내용 불러오기
              </button>
              <button className="secondary" onClick={dismissModal}>
                계속 편집
              </button>
            </>
          )}
          {modal === "logout" && (
            <>
              <h2>로그아웃하시겠어요?</h2>
              <button
                className="primary destructive"
                onClick={() => void signOut()}
              >
                로그아웃
              </button>
              <button className="secondary" onClick={() => setModal(null)}>
                취소
              </button>
            </>
          )}
          {modal === "add-furniture" && (
            <div className="furniture-add-content">
              <h2>가구 추가</h2>
              <button className="primary" onClick={addManualFurniture}>
                직접 추가
                <small>위치와 치수 입력</small>
              </button>
              <button
                className="secondary"
                onClick={() => {
                  rememberQrReturn(page);
                  setAddingByQr(true);
                  setModal(null);
                  setPage("capture");
                }}
              >
                QR 촬영으로 추가
              </button>

              <button className="text-button" onClick={dismissModal}>
                취소
              </button>
            </div>
          )}
          {modal === "capture-help" && <CaptureHelp onClose={() => setModal(null)} />}
          {modal === "discard" && (
            <>
              <button type="button" className="dialog-close" aria-label="계속 편집" onClick={() => setModal(null)}>
                <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
              <h2>저장하지 않은 변경 내용이 있습니다</h2>
              <p className="muted">
                임시 저장하면 입력한 데까지 이 기기에 남고, 내 공간의 작성 중인 공간에서 이어서 편집할 수 있습니다.
              </p>
              <button className="primary" onClick={() => leaveEditor(true)}>
                임시 저장하고 나가기
              </button>
              <button className="primary destructive" onClick={() => leaveEditor(false)}>
                저장하지 않고 나가기
              </button>
            </>
          )}
        </dialog>
        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
      </div>
      {saving && (
        <div className="toast" role="status">
          서버에 저장 중…
        </div>
      )}
    </>
  );
}
export default App;
