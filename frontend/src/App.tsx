import { lazy, Suspense } from "react";
import { WebLoginGoogleStep } from "./WebLoginStep";
import FurnitureTable from "./FurnitureTable";
import "./ReferencePreview.css";
import "./WorkspaceDesign.css";
import { ShadingTimeControl, VirtualFilmControl } from "./ShadingPlan";
import { SpaceMenu, SpaceMenuGlyph } from "./SpaceMenu";
import AdaptiveEffect from "./AdaptiveEffect.tsx";
import SpatialCapture from "./SpatialCapture";
import {
  defaultShadingTime,
} from "./model";
import type {
} from "./model";
import "./App.css";

import { AwardHeader, AwardLanding, AwardSpaces, AwardGuide, AwardFooter } from "./AwardSystem";
import { AccountOverview, AuthPanel } from "./AccountPage";
import { useSpaceApp, titles } from "./useSpaceApp";
import { RegisterForm } from "./RegisterForm";
import { InspectorFields } from "./InspectorFields";
import { DialogBodies } from "./DialogBodies";
import { ProcessingCard, QrWebGuide, TempDraftsSection } from "./SharedSections";
import { Icon, Card } from "./ShellParts";
// The web detail plan (2D top view and 3D) is a three.js scene, loaded with the detail page only.
// Start fetching the 3D view at once, so the detail page rarely waits on it.
const load3D = () => import("./Space3DView.tsx");
void load3D();
const Space3DView = lazy(load3D);
// Its stylesheet loads with the page so the placeholder below has the stage's exact frame.
import "./Space3DView.css";
import { HeadingSkeleton, SubtitleSkeleton, DetailPlanSkeleton } from "./SpaceSkeleton";

function App() {
  const app = useSpaceApp();
  const {
    isApp,
    measurementJobs,
    account,
    authStatus,
    authNotice,
    startLogin,
    spaces,
    spacesPending,
    page,
    draft,
    setDraft,
    selectedFurniture,
    selectedRegion,
    chooseRegion,
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
    guideIndex,
    setGuideIndex,
    addingByQr,
    setAddingByQr,
    rememberQrReturn,
    hour,
    setHour,
    storageError,
    saving,
    serverState,
    serverError,
    unavailable,
    conflict,
    joinMeasurement,
    modalRef,
    errorRef,
    active,
    editing,
    space,
    sunSchedule,
    shadingEdit,
    setShadingEdit,
    detailShading,
    region,
    refreshServer,
    unsaved,
    leaveUnsaved,
    tempDrafts,
    navigate,
    requireAccount,
    dismissModal,
    begin,
    selectSpace,
    saveShadingTime,
    editSpace,
    setActiveId,
    change,
    chooseFurniture,
    save,
    zoneTabs,
    finishCapture,
    completeVisionRegistration,
    addFurniture,
    addManualFurniture,
    confirmReview,
    plan,
    navItems,
    isActive,
    actionBar,
    syncFailed,
    retryUpload,
    sunSource,
    filmPrediction,
    predictionDate,
    predictionKey,
    sunpathResult,
  } = app;
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
                      : page === "detail" && spacesPending ? <HeadingSkeleton /> : page === "detail" && active ? active.name : titles[page]}
                  </h1>
                  {page === "detail" && spacesPending && <SubtitleSkeleton />}
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
                  {serverState !== "online" && !(serverState === "loading" && spacesPending) && (
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
                  )}</>} loading={spacesPending} spaces={spaces} jobs={measurementJobs} syncFailed={syncFailed} onRetrySync={retryUpload} onSelect={selectSpace} onEdit={(s, at) => editSpace(s, at)} onDelete={(s) => { setActiveId(s.id); setModal("delete-space"); }} onStart={begin} onMeasurement={job=>{const s=spaces.find(s=>s.id===job.space_id);if(isApp && job.status!=="completed")joinMeasurement(job);else if(s){selectSpace(s);if(job.status!=="completed"){setDraft(structuredClone(s));setPage("capture");}}}}/>}
                  {page === "spaces" && tempDrafts.length > 0 && <TempDraftsSection app={app} />}
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
              {page === "register" && draft && <RegisterForm app={app} split />}
              {page === "capture" && draft && !isApp && <QrWebGuide app={app} />}
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
                        onVisionRegistered={completeVisionRegistration}
                        space={draft}
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
                      <li>형태 도면의 P1부터 순서대로 이동하며 지점마다 촬영 버튼을 한 번 누르기</li>
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
              {page === "processing" && <ProcessingCard app={app} />}
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
                      <InspectorFields app={app} compact={false} />
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
              {page === "detail" && spacesPending && <DetailPlanSkeleton />}
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
                      <VirtualFilmControl key={active.id} space={active} prediction={filmPrediction} hour={hour} setting={detailShading}>
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
          <DialogBodies app={app} addFurniture={(
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
          )} />
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
