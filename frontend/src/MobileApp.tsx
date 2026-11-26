import { useEffect, useRef, useState } from "react";
import MobileEditorSheet from "./MobileEditorSheet";
import MobileFurnitureList from "./MobileFurnitureList";
import { WorkspaceNavigation, WorkspaceHome, WorkspaceCards } from "./WorkspaceDesign";
import { FilmGrid, ShadingTimeControl, VirtualFilmControl } from "./ShadingPlan";
import { SpaceMenu, SpaceMenuGlyph } from "./SpaceMenu";
import { BUILD_LABEL } from "./buildLabel";
import AdaptiveEffect from "./AdaptiveEffect.tsx";
import { applyRegionShading, formatMinute } from "./sunlight";
import SpatialCapture from "./SpatialCapture";
import TestMarkerPreview, { TestMarkerSize } from "./TestMarkerPreview";
import type { CaptureMarker } from "./measureApi";
import { hasStoredCapture } from "./measureApi";

import {
  defaultShadingTime,
  grades,
  m2,
  upsertSpace,
  resolveWindow,
  timeToMinute,
} from "./model";
import type {
  Grade,
} from "./model";
import "./App.css";
// Award design language for the phone screens; imported last so it settles ties with the base app styles.
import "./AwardMobile.css";
import {
  getServerSpace,
  userMessage,
} from "./spaceApi";
import { guideExtras, guideSteps } from "./guideContent";
import { GradeList, GuideExtraIcon } from "./AwardSystem";
import { GuideMethods } from "./GuideMethods";
import { GuideIllustration } from "./MoodIcons";
import {
  WebsiteHeader,
  WebsiteHome,
  WebsiteGuide,
  WebsiteFooter,
} from "./Website";
import type { WebsiteDestination } from "./Website";

import { useSpaceApp, titles } from "./useSpaceApp";
import { RegisterForm } from "./RegisterForm";
import { InspectorFields } from "./InspectorFields";
import { DialogBodies } from "./DialogBodies";
import { ProcessingCard, QrWebGuide, TempDraftsSection } from "./SharedSections";
import { Icon, Card, SyncBadge, SyncRetry } from "./ShellParts";
import { AuthAction, AuthLead } from "./AuthNotice";
import { GoogleButton } from "./GoogleMark";
import { HeadingSkeleton, SubtitleSkeleton, AppListSkeleton, AppDetailSkeleton, AppHomeSkeleton } from "./SpaceSkeleton";
function MobileApp() {
  const [captureMarker, setCaptureMarker] = useState<CaptureMarker | null>(null);
  const [markerSizeOverride, setMarkerSizeOverride] = useState<{ sessionId: string; sideM: number }>();
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
    setSpaces,
    page,
    draft,
    selectedFurniture,
    selectedRegion,
    tool,
    setTool,
    modal,
    setModal,
    error,
    setError,
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
    draftPage,
    takePendingWebAction,
    active,
    editing,
    space,
    sunSchedule,
    shadingEdit,
    setShadingEdit,
    detailShading,
    activeRegionCount,
    furniture,
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
    chooseRegion,
    save,
    zoneTabs,
    finishCapture,
    cancelCapture,
    completeVisionRegistration,
    changeCaptureFilm,
    namePrompt,
    addFurniture,
    addManualFurniture,
    plan,
    navItems,
    isActive,
    actionBar,
    syncFailed,
    retryUpload,
    sunSource,
    predictionPill,
    filmPrediction,
  } = app;
  const [settings, setSettings] = useState({ page, open: false });
  if (settings.page !== page) setSettings({ page, open: false });
  const settingsOpen = settings.page === page && settings.open;
  const openSettings = () => setSettings({ page, open: true });
  const closeSettings = () => setSettings({ page, open: false });
  useEffect(() => { if (namePrompt) setSettings({ page, open: true }); }, [namePrompt]);
  const editorPointer = useRef<{x:number;y:number;furnitureId:string|null;regionId:string|null} | null>(null);
  // WEB APP TEST ONLY: camera setup state for 더보기 › 카메라 준비.
  // Home shows whether blocking is on right now, so the clock ticks once a minute.
  // 더보기 reference pages: 0 등급 기준, 1 차광 필름, 2 촬영 방법; null = the list.
  const [moreView, setMoreView] = useState<number | null>(null);
  useEffect(() => {
    if (page === "more") return;
    setMoreView(null);
    // Entries pushed by other pages copy the old state; drop the detail marker so it cannot reappear on back.
    if (typeof window.history.state?.moreView === "number") {
      window.history.replaceState({ ...window.history.state, moreView: undefined }, "", window.location.href);
    }
  }, [page]);
  // A detail page gets its own history entry so the phone's back gesture returns to the list instead of leaving 더보기.
  const openMoreView = (i: number) => {
    window.history.pushState({ ...window.history.state, moreView: i }, "", window.location.href);
    setMoreView(i);
  };
  const closeMoreView = () => {
    if (typeof window.history.state?.moreView === "number") window.history.back();
    else setMoreView(null);
  };
  useEffect(() => {
    const sync = () => {
      const v = window.history.state?.moreView;
      setMoreView(typeof v === "number" ? v : null);
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  function websiteNavigate(next: WebsiteDestination) {
    if (next === "home" || next === "guide") {
      navigate(next);
      return;
    }
    requireAccount(() => navigate("spaces"), "spaces");
  }

  function openWebsiteGuide(index: number | null) {
    setGuideIndex(index);
    navigate("guide");
  }
  // App home shows today's blocking window first, then whether the spaces are set up, then a short list.
  // 공간 추가 lives in the 공간 tab; the guide keeps a quiet shortcut under the list.
  const protectedSpaces = spaces.filter((s) => s.regions.some((r) => r.grade !== "D"));
  // Reference pages under 더보기 share their wording with the guide (one source: guideContent.ts).
  const moreHelp = [
    { title: guideExtras[0].title, hint: "가구별 차단 정도", art: guideExtras[0].art, text: guideExtras[0].text, detail: undefined, grades: true, methods: undefined },
    { title: guideExtras[1].title, hint: "창문 필름이 햇빛을 막는 방식", art: guideExtras[1].art, text: guideExtras[1].text, detail: guideExtras[1].detail, grades: false, methods: undefined },
    { title: "촬영 방법", hint: "창문과 가구를 정확히 찍는 법", art: 2, text: guideSteps[2].text, detail: guideSteps[2].detail, grades: false, methods: guideSteps[2].methods },
  ];
  const needsSetup = spaces.find((s) => !s.regions.some((r) => r.grade !== "D"));
  const protectedRegionCount = spaces.reduce((n, s) => n + s.regions.filter((r) => r.grade !== "D").length, 0);
  // Today's blocking times lead the home screen: one big window when every space shares it, else one row per space.
  const scheduled = spaces.flatMap((s) => (s.shadingSchedule?.enabled ? [{ id: s.id, name: s.name, ...s.shadingSchedule }] : []));
  const sharedWindow = scheduled.length > 0 && scheduled.every((s) => s.start === scheduled[0].start && s.end === scheduled[0].end);
  const nowDate = new Date(clock);
  const nowMinute = nowDate.getHours() * 60 + nowDate.getMinutes();
  const blockingNow = scheduled.some((s) => {
    const a = timeToMinute(s.start);
    const b = timeToMinute(s.end);
    if (a === null || b === null) return false;
    return a <= b ? nowMinute >= a && nowMinute < b : nowMinute >= a || nowMinute < b;
  });
  const homeDashboard = !spaces.length ? null : (
    <div className="home-dashboard">
      <section className="home-today" aria-label="오늘 차단 시각">
        <h2 className="home-today-label">오늘 차단 시각</h2>
        {scheduled.length === 0 ? (
          <>
            <p className="home-today-empty">아직 차단 시간을 정한 공간이 없어요</p>
            <button className="primary" onClick={() => selectSpace(spaces[0])}>
              차단 시간 정하기
            </button>
          </>
        ) : (
          <>
            {sharedWindow ? (
              <p className="home-today-time">
                {scheduled[0].start} – {scheduled[0].end}
              </p>
            ) : (
              <ul className="home-today-list">
                {scheduled.slice(0, 3).map((s) => (
                  <li key={s.id}>
                    <span>{s.name}</span>
                    <strong>
                      {s.start} – {s.end}
                    </strong>
                  </li>
                ))}
              </ul>
            )}
            <span className={`home-today-chip${blockingNow ? " on" : ""}`} role="status">
              {blockingNow ? "지금 차단 중" : "지금은 차단 시간이 아니에요"}
            </span>
          </>
        )}
      </section>
      <div className="home-preview-head">
        <h2 className="section-label">내 공간</h2>
        <button className="text-button" onClick={() => navigate("spaces")}>
          전체 보기
        </button>
      </div>
      <section className={`home-status${needsSetup ? " todo" : ""}`} aria-label="보호 설정 현황">
        <span className="home-status-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z" />
            {!needsSetup && <path d="M9 12l2 2 4-4" />}
          </svg>
        </span>
        <span className="home-status-text">
          <strong>
            {!needsSetup
              ? `공간 ${spaces.length}곳 모두 보호 설정 완료`
              : `${spaces.length}곳 중 ${protectedSpaces.length}곳 보호 설정 완료`}
          </strong>
          <small>{needsSetup ? `${needsSetup.name} 설정 전` : `보호 영역 ${protectedRegionCount}개 설정됨`}</small>
        </span>
        {needsSetup && (
          <button className="primary" aria-label="보호 영역 설정하기" onClick={() => selectSpace(needsSetup)}>
            설정하기
          </button>
        )}
      </section>
      <div className="home-preview">
        {spaces.slice(0, 3).map((s) => (
          <button key={s.id} onClick={() => selectSpace(s)}>
            <span>
              <strong>{s.name}</strong>
              <small>
                {s.building} {s.floor}
              </small>
            </span>
            {!s.regions.some((r) => r.grade !== "D") && <span className="status-pill todo">설정 전</span>}
            <span aria-hidden="true">›</span>
          </button>
        ))}
      </div>
      <button className="home-guide" onClick={() => navigate("guide")}>
        <span aria-hidden="true">?</span>사용 가이드
        <small>처음이라면 순서대로 따라 해 보세요</small>
        <span aria-hidden="true">›</span>
      </button>
    </div>
  );
  const spaceList = (
    <>
      {spacesPending ? (
        <AppListSkeleton />
      ) : !spaces.length ? (
        <Card className="empty">
          <h2>등록된 공간 없음</h2>
          <button className="primary" onClick={begin}>
            공간 등록
          </button>
        </Card>
      ) : (
        <div className="building-list">
          {[...new Set(spaces.map((s) => s.building))].map((building) => (
            <section key={building}>
              <h2 className="building-title">
                {building}
              </h2>
              <div className="room-grid">
                {spaces
                  .filter((s) => s.building === building)
                  .map((s) => (
                    <div className="room-entry" key={s.id}>
                      <SpaceMenu label={`${s.name} 더보기`} items={[
                        { label: "가구 편집", icon: "layout", onSelect: () => editSpace(s) },
                        { label: "공간 정보 수정", icon: "info", onSelect: () => editSpace(s, "register") },
                        { label: "공간 삭제", icon: "trash", danger: true, onSelect: () => { setActiveId(s.id); setModal("delete-space"); } },
                      ]} />
                      <button
                        className="room-card"
                        onClick={() => selectSpace(s)}
                      >
                        <span>
                          <small className="room-floor">{s.floor}</small>
                          <strong>{s.name}</strong>
                          <span className="room-tags">
                            {s.regions.some((r) => r.grade !== "D") ? (
                              <span className="status-pill done">보호 설정 완료</span>
                            ) : (
                              <span className="status-pill todo">보호 설정 전</span>
                            )}
                            <small>보호 영역 {s.regions.filter((r) => r.grade !== "D").length}개</small>
                            {!isApp && <SyncBadge space={s} />}
                          </span>
                        </span>
                        <span aria-hidden="true">›</span>
                      </button>
                      <SyncRetry space={s} failed={syncFailed} onRetry={retryUpload} />
                      {(() => {
                        const job = measurementJobs.find(j => j.space_id === s.id);
                        if (!job) return null;
                        // "Connected" stays on the server after a cancelled shoot; only a live capture session is resumable.
                        const resumable = job.status === "connected"
                          && (hasStoredCapture(s.serverSceneId ?? s.id) || (draft?.id === s.id && draftPage.current === "capture"));
                        return <div className="space-measurement-status" role="status">
                          <span>{job.status === "completed" ? "측정 완료" : resumable ? "측정 진행 중" : "측정 대기"}</span>
                          {isApp && job.status !== "completed" && <button type="button" className="primary" aria-label={`${s.name} ${resumable ? "촬영 이어하기" : "촬영 시작"}`} onClick={()=>{if(draft?.id === s.id && draftPage.current === "capture") setPage("capture"); else joinMeasurement(job);}}>{resumable ? "촬영 이어하기" : "촬영 시작"}</button>}
                          {!isApp && job.status === "completed" && <button className="secondary" onClick={async()=>{ try { const saved=await getServerSpace(s.id);selectSpace(saved);setSpaces(current=>upsertSpace(current,saved)); }catch(e){setError(userMessage(e, "공간을 불러오지 못했습니다. 목록을 새로고침해 주세요."));} }}>도면 확인</button>}
                        </div>;
                      })()}
                    </div>
                  ))}
              </div>
            </section>
          ))}
        </div>
      )}
      {!isApp && spaces.length > 0 && (
        <button className="secondary add-space" onClick={begin}>
          ＋ 공간 추가
        </button>
      )}
    </>
  );
  // The app has no public pages; the web keeps home and guide open while the session is checked.
  const checking = authStatus === "checking" && (isApp || !(page === "home" || page === "guide"));
  return (
    <>
      <div
        inert={saving || undefined}
        aria-busy={saving}
        className={`app ${account && !isApp ? "rp-shell actual-workspace" : ""} ${isApp ? "mode-app award-mobile" : "mode-web"} page-${page} ${page === "more" ? "more-page" : ""} ${!account || checking ? "auth-page" : ""} ${actionBar || (isApp && page === "capture" && draft) ? "has-action-bar" : ""}`}
      >
        {account && (
          <button
            type="button"
            className="skip-link"
            onClick={() => document.getElementById("main")?.focus()}
          >
            본문으로 건너뛰기
          </button>
        )}
        {account && !isApp && <WorkspaceNavigation page={page} account={account} count={spaces.length} online={serverState === "online"} navigate={navigate} onStart={begin} />}
        {(!isApp || (account && !checking)) && (
        <WebsiteHeader
          account={account}
          active={page}
          onNavigate={websiteNavigate}
          onAccount={() => navigate("account")}
          onLogin={() => {
            takePendingWebAction();
            setModal("login");
          }}
          onStart={() => requireAccount(begin, "spaces")}
        />
        )}
        {page === "home" && !isApp && !account && (
          <WebsiteHome
            onGuide={openWebsiteGuide}
            onStart={() => requireAccount(begin, "spaces")}
          />
        )}
        {page === "guide" && (isApp ? !!account && !checking : true) && (
          <WebsiteGuide index={guideIndex} onIndex={setGuideIndex} />
        )}
        {checking ? (
          <main className="auth" aria-busy="true" />
        ) : !account ? (
          <main className={`auth ${!isApp && (page === "home" || page === "guide") ? "landing-auth-hidden" : ""}`}>
            <div className="auth-brand">
              <Icon name="sun" />
              <span>SunSync</span>
            </div>
            <h1>로그인 또는 회원가입</h1>
            <AuthAction message={authNotice}>
              <GoogleButton onClick={startLogin} />
            </AuthAction>
          </main>
        ) : (
          <>
            <header className="app-header">
              <button
                className="brand"
                onClick={() => navigate("home")}
                aria-label="SunSync 홈"
              >
                <Icon name="sun" />
                <span translate="no">SunSync</span>
              </button>
            </header>
            <nav className="desktop-nav" aria-label="웹 메뉴">
              {navItems.map((item) => (
                <button
                  key={item.page}
                  aria-current={isActive(item.page) ? "page" : undefined}
                  className={isActive(item.page) ? "active" : ""}
                  onClick={() => navigate(item.page)}
                >
                  {item.desktop}
                </button>
              ))}
              {active && (
                <button
                  onClick={() => {
                    if (draft && editing) navigate("protect");
                    else editSpace(active);
                  }}
                  className={
                    page === "protect" || page === "review" ? "active" : ""
                  }
                >
                  도면·보호 영역
                </button>
              )}
              <button onClick={begin}>공간 등록</button>
            </nav>
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
                  {isApp && page === "more" && moreView !== null && (
                    <button className="back-button" onClick={closeMoreView}>
                      <svg viewBox="0 0 12 20" width="12" height="20" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 2 2 10l8 8" /></svg>
                      더보기
                    </button>
                  )}
                  {isApp && (page === "detail" || (draft && ["register", "review", "protect"].includes(page))) && (
                    <button
                      className="back-button"
                      onClick={() =>
                        navigate(page !== "detail" && draft && spaces.some((s) => s.id === draft.id) ? "detail" : "spaces")
                      }
                    >
                      <svg viewBox="0 0 12 20" width="12" height="20" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 2 2 10l8 8" /></svg>
                      {page !== "detail" && draft && spaces.some((s) => s.id === draft.id) ? "공간 현황" : "내 공간"}
                    </button>
                  )}
                  <h1>
                    {page === "register" &&
                    draft &&
                    spaces.some((s) => s.id === draft.id)
                      ? "공간 정보 수정"
                      : page === "detail" && spacesPending ? <HeadingSkeleton /> : page === "detail" && active ? active.name : page === "more" && moreView !== null ? moreHelp[moreView].title : titles[page]}
                  </h1>
                  {page === "detail" && spacesPending && <SubtitleSkeleton />}
                  {page === "detail" && active && <p className="page-subtitle">{active.building} {active.floor} </p>}
                </div>
                {(isApp ? page === "spaces" : ["home", "spaces"].includes(page)) && (
                  <button
                    className="icon-button plus"
                    aria-label="공간 추가"
                    onClick={begin}
                  >
                    ＋
                  </button>
                )}
                {isApp && page === "capture" && (
                  <button
                    type="button"
                    className="capture-help-button"
                    aria-label="촬영 방법 보기"
                    onClick={() => setModal("capture-help")}
                  >
                    ?
                  </button>
                )}
                {isApp && draft && (page === "review" || page === "protect") && (
                  <div className="heading-actions">
                    <button type="button" className="secondary" disabled={saving || !!draftWarning} onClick={save}>{saving ? "저장 중…" : "저장"}</button>
                    <SpaceMenu
                      label="공간 더보기"
                      items={[
                        { label: "공간 정보 수정", icon: "info", onSelect: () => setPage("register") },
                      ]}
                    />
                  </div>
                )}
                {page === "detail" && active && (
                  <div className="heading-actions">
                    <button type="button" className="secondary" onClick={() => editSpace(active)}><SpaceMenuGlyph name="layout" />가구 편집</button>
                    <SpaceMenu
                      label="공간 더보기"
                      items={[
                        { label: "공간 정보 수정", icon: "info", onSelect: () => editSpace(active, "register") },
                        { label: "공간 삭제", icon: "trash", danger: true, onSelect: () => setModal("delete-space") },
                        ...(isApp ? [{ label: "웹에서 크게 보기", icon: "external" as const, onSelect: () => { window.open(`${location.origin}/?mode=web#/detail/${active.id}`, "_blank", "noopener"); } }] : []),
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
              {page === "home" && !isApp && account && <WorkspaceHome loading={spacesPending} spaces={spaces} selectSpace={selectSpace} navigate={navigate} onStart={begin} />}
              {(page === "spaces" || (isApp && page === "home")) && (
                <div className="list-content">
                  {page === "home" && isApp && (spacesPending ? <AppHomeSkeleton /> : (homeDashboard ?? spaceList))}
                  {page === "home" && !isApp && (
                    <>
                      {/* Summary strip (one card, three figures), then the list it summarises. */}
                      <section className="app-summary" aria-label="내 공간 요약">
                        <div>
                          <strong>{spaces.length}</strong>
                          <span>등록한 공간</span>
                        </div>
                        <div>
                          <strong>
                            {spaces.reduce((n, s) => n + s.regions.filter((r) => r.grade !== "D").length, 0)}
                          </strong>
                          <span>보호 영역</span>
                        </div>
                        <div>
                          <strong>
                            {spaces.filter((s) => !s.regions.some((r) => r.grade !== "D")).length}
                          </strong>
                          <span>설정 전 공간</span>
                        </div>
                      </section>
                      <h2 className="section-label">내 공간</h2>
                    </>
                  )}

                  {/* App home stays quiet while the server is fine; the status shows when it needs attention. */}
                  {(!isApp || serverState !== "online") && !(serverState === "loading" && spacesPending) && (
                  <div className="server-sync-row" data-state={serverState}>
                    <span>
                      {serverState === "loading"
                        ? "저장한 공간 불러오는 중…"
                        : serverState === "online"
                          ? "서버 연결됨"
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
                  )}
                  {!isApp ? <WorkspaceCards loading={spacesPending} spaces={spaces} selectSpace={selectSpace} onStart={begin} onEdit={(s, at) => editSpace(s, at)} onDelete={(s) => { setActiveId(s.id); setModal("delete-space"); }} /> : page === "home" ? null : spaceList}
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
              {page === "register" && draft && <RegisterForm app={app} split={false} />}
              {page === "capture" && draft && !isApp && <QrWebGuide app={app} />}
              {page === "capture" && draft && isApp && (
                <div className="capture-layout">
                  <div>
                    <div className="capture-intro">
                      <div className="capture-title-with-marker">
                        <TestMarkerPreview key={`preview:${captureMarker?.session_id}`} session={captureMarker} />
                        {captureMarker && <TestMarkerSize
                          key={`size:${captureMarker.session_id}`}
                          sideM={markerSizeOverride?.sessionId === captureMarker.session_id ? markerSizeOverride?.sideM : undefined}
                          onApply={sideM => setMarkerSizeOverride(sideM === undefined
                            ? undefined : { sessionId: captureMarker.session_id, sideM })}
                        />}
                      </div>
                      {!addingByQr && <p className="step-label">2/3단계</p>}
                    </div>
                    {zoneTabs}
                    {draft.windowDimensions?.width &&
                    draft.windowDimensions?.height ? (
                      <SpatialCapture
                        onMarkerChange={setCaptureMarker}
                        markerSizeOverride={markerSizeOverride}
                        sceneId={draft.serverSceneId}
                        key={`${draft.id}:${draft.zone}`}
                        count={
                          addingByQr
                            ? draft.furniture.filter((f) => f.zone === draft.zone).length
                            : 0
                        }
                        onFinish={finishCapture}
                        onCancel={cancelCapture}
                        onVisionRegistered={completeVisionRegistration}
                        onFilmChange={changeCaptureFilm}
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
                  {!draft.serverRevision && <p className="step-label editor-step-label">
                    {page === "review"
                      ? "3/3단계, 가구 위치와 치수 확인"
                      : "3/3단계, 보호 영역과 높이 설정"}
                  </p>}
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
                      <div className="mobile-plan-interaction"
                        onPointerDownCapture={e => {
                          const g=(e.target as Element).closest('[role="button"]');
                          editorPointer.current = {x:e.clientX,y:e.clientY,
                            furnitureId:g?.querySelector('[data-furniture]')?.getAttribute('data-furniture') ?? null,
                            regionId:g?.querySelector('[data-region]')?.getAttribute('data-region') ?? null};
                        }}
                        onPointerUpCapture={e => {
                          const p=editorPointer.current;
                          if (!p) return;
                          const moved=Math.hypot(e.clientX-p.x,e.clientY-p.y)>=6;
                          if (!moved && (p.furnitureId || p.regionId)) {
                            if (p.regionId) chooseRegion(p.regionId); else chooseFurniture(p.furnitureId!);
                            openSettings();
                          } else if (tool === "region" && moved && !p.furnitureId && !p.regionId) openSettings();
                        }}
                        onKeyUpCapture={e => { if ((e.key === "Enter" || e.key === " ") && (e.target as Element).closest('[role="button"]')) openSettings(); }}>
                        <Card className="plan-card">{plan}</Card>

                      </div>
                      {page === "protect" && <Card className="shading-time-card">
                        <ShadingTimeControl compact space={draft} value={draft.shadingSchedule ?? defaultShadingTime}
                          onChange={shadingSchedule=>change({shadingSchedule})} sunSchedule={sunSchedule} hour={hour} />
                      </Card>}
                      <MobileFurnitureList opensSettings space={draft} selected={selectedFurniture} selectedRegion={selectedRegion}
                        schedule={page === "protect" ? sunSchedule : undefined}
                        onFurniture={id=>{chooseFurniture(id);openSettings();}}
                        onRegion={id=>{chooseRegion(id);openSettings();}} onAdd={addFurniture} />
                    </div>
                    <MobileEditorSheet open={settingsOpen && !!(furniture || region)} title={furniture?.name ?? region?.name ?? "가구 설정"} onClose={closeSettings}>
                      {draftWarning && <p className="error" role="alert">{draftWarning}</p>}
                      <InspectorFields app={app} compact />
                      {conflict && <button className="secondary" onClick={()=>setModal("reload-server")}>최신 내용 불러오기</button>}
                      {page === "review" && <button className="secondary" onClick={()=>{closeSettings();setPage("capture");}}>다시 측정</button>}
                    </MobileEditorSheet>
                  </div>
                </>
              )}
              {page === "detail" && spacesPending && <AppDetailSkeleton />}
              {page === "detail" && active && (
                <div className="workspace">
                  <div className="workspace-main">
                    {active.zones.length > 1 && zoneTabs}
                    <Card className="status-card">
                      <span
                        className={`status-dot ${activeRegionCount ? "" : "pending"}`}
                      />
                      <div>
                        <strong>
                          {activeRegionCount
                            ? "보호 설정 완료"
                            : "보호 설정 전"}
                        </strong>
                        <p className="muted small">
                          {active.floor}, 보호 영역{" "}
                          {activeRegionCount}개
                        </p>
                      </div>
                      {predictionPill !== "햇빛 예측" && <span className="pill">{predictionPill}</span>}
                    </Card>
                    <Card className="plan-card detail-plan-card">
                      {plan}
                      {(furniture || region) && <div className="plan-selection-summary" aria-live="polite">
                        <strong>{furniture?.name ?? region?.name}</strong>
                        {furniture && <p className="selection-dimensions">{m2(furniture.width)} × {m2(furniture.depth)} × {m2(furniture.height)} m</p>}
                        {region && <div className="selection-protection">
                          <p>{region.name} · {m2(region.bottom)}–{m2(region.top)} m</p>
                          <p>{region.grade} : {grades[region.grade]}</p>
                        </div>}
                      </div>}
                    </Card>
                    {/* Right under the plan: the slider moves the sunlight on the plan and the film below. */}
                    <Card className="detail-preview-card">
                      <div className="section-row">
                        <h3>햇빛·필름 미리보기</h3>
                        <strong className="brand-text">{formatMinute(Math.round(hour * 60))}</strong>
                      </div>
                      <input
                        aria-label="햇빛 확인 시각"
                        type="range"
                        min="0"
                        max={24 - 1 / 12}
                        step={1 / 12}
                        value={hour}
                        onChange={(e) => setHour(Number(e.target.value))}
                      />
                      <div className="range-labels">
                        <span>00</span>
                        <span>06</span>
                        <span>12</span>
                        <span>18</span>
                        <span>24</span>
                      </div>

                      <div className="detail-film-preview">
                        <h4>필름 차광 모습</h4>
                      {resolveWindow(active) ? (
                        <FilmGrid space={active} schedule={applyRegionShading(sunSchedule, active, detailShading)} hour={hour} prediction={filmPrediction} setting={detailShading} />
                      ) : (
                        <p className="muted small">
                          창문 규격을 입력하면 시각별로 가려질 필름 칸을 볼 수 있습니다.
                        </p>
                      )}
                      </div>
                    </Card>
                    <MobileFurnitureList space={active} selected={selectedFurniture} selectedRegion={selectedRegion}
                      onFurniture={chooseFurniture} onRegion={chooseRegion} />
                    <Card className="detail-schedule-card">
                    <ShadingTimeControl
                      compact
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
                    {sunSource?.note}
                    </Card>
                    <Card className="detail-film">
                      <VirtualFilmControl key={active.id} space={active} prediction={filmPrediction} hour={hour} setting={detailShading}>
                        {filmPrediction?.plan && <AdaptiveEffect space={active} start={filmPrediction.plan.start_time} />}
                      </VirtualFilmControl>
                    </Card>
                  </div>
                </div>
              )}
              {page === "guide" && (
                <div className="guide-content">
                  {guideIndex === null ? (
                    <div className="guide-grid">
                      {guideSteps.map((step, i) => (
                        <button
                          className="guide-card"
                          key={step.title}
                          onClick={() => setGuideIndex(i)}
                        >
                          <span className="guide-number">
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <h2>{step.title}</h2>
                          <p>{step.text}</p>
                          <span className="brand-text">방법 보기</span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <Card className="guide-detail">
                      <p className="step-label">
                        사용 가이드 {guideIndex + 1}/{guideSteps.length}
                      </p>
                      <h2>{guideSteps[guideIndex].title}</h2>
                      <div className="mobile-guide-visual" aria-hidden="true">
                        <span className="mobile-guide-number">
                          {String(guideIndex + 1).padStart(2, "0")}
                        </span>
                        <GuideIllustration step={guideIndex} />
                      </div>
                      {!guideSteps[guideIndex].methods && <p>{guideSteps[guideIndex].text}</p>}
                      <GuideMethods methods={guideSteps[guideIndex].methods} />
                      <p className="muted">{guideSteps[guideIndex].detail}</p>
                      <div className="guide-pagination">
                        <button
                          className="secondary"
                          disabled={guideIndex === 0}
                          onClick={() => setGuideIndex((i) => (i ?? 1) - 1)}
                        >
                          이전 단계
                        </button>
                        <button
                          className="secondary"
                          disabled={guideIndex === guideSteps.length - 1}
                          onClick={() => setGuideIndex((i) => (i ?? 0) + 1)}
                        >
                          다음 단계
                        </button>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => setGuideIndex(null)}
                      >
                        가이드 목록
                      </button>
                    </Card>
                  )}
                </div>
              )}
              {page === "more" && moreView === null && (
                <div className="more-content">
                  <section className="card more-profile" aria-label="계정">
                    <button className="row account-row" onClick={() => setPage("account")}>
                      <span className="account-avatar" aria-hidden="true">{account.name.slice(0, 1)}</span>
                      <span>
                        <strong>{account.name}</strong>
                        <small>{account.email}</small>
                      </span>
                      <svg className="more-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
                    </button>
                    <dl className="more-stats">
                      <div><dt>등록한 공간</dt><dd>{spaces.length}곳</dd></div>
                      <div><dt>보호 설정 완료</dt><dd>{protectedSpaces.length}곳</dd></div>
                    </dl>
                  </section>
                  <Card className="more-help">
                    {moreHelp.map((item, i) => (
                      <button className="row help-row" key={item.title} onClick={() => openMoreView(i)}>
                        <span className="help-icon"><GuideExtraIcon art={item.art} size={24} /></span>
                        <span>
                          <strong>{item.title}</strong>
                          <small>{item.hint}</small>
                        </span>
                        {i === 0 && (
                          <span className="help-grades" aria-hidden="true">
                            {(["A", "B", "C", "D"] as Grade[]).map((g) => <span key={g} className={`furniture-grade grade-${g}`}>{g}</span>)}
                          </span>
                        )}
                        <svg className="more-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
                      </button>
                    ))}
                  </Card>
                  <button className="home-guide" onClick={() => navigate("guide")}>
                    <span aria-hidden="true">?</span>사용 가이드
                    <small>처음이라면 순서대로 따라 해 보세요</small>
                    <span aria-hidden="true"><svg className="more-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m9 5 7 7-7 7" /></svg></span>
                  </button>
                </div>
              )}
              {page === "more" && moreView !== null && (
                <div className="more-content more-detail">
                  <Card className="guide-detail">
                    <div className="mobile-guide-visual" aria-hidden="true">
                      <GuideIllustration step={moreHelp[moreView].art} />
                    </div>
                    {!moreHelp[moreView].methods && <p>{moreHelp[moreView].text}</p>}
                    <GuideMethods methods={moreHelp[moreView].methods} />
                    {moreHelp[moreView].grades && <GradeList />}
                    {moreHelp[moreView].detail && <p className="muted">{moreHelp[moreView].detail}</p>}
                  </Card>
                </div>
              )}
              {page === "account" && (
                <div className="account-content account-refresh">
                  <section className="account-profile" aria-label="계정 정보">
                    <span className="account-profile-avatar" aria-hidden="true">{account.name.slice(0, 1)}</span>
                    <h2>{account.name}</h2>
                    <p>{account.email}</p>
                  </section>
                  <section className="account-info" aria-labelledby="account-connection-title">
                    <h2 id="account-connection-title">로그인 정보</h2>
                    <dl>
                      <div><dt>로그인 방식</dt><dd>Google 계정</dd></div>
                    </dl>
                  <button type="button" className="account-logout" onClick={() => setModal("logout")}>로그아웃</button>
                  </section>
                  <p className="account-version">{BUILD_LABEL}</p>
                </div>
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
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    {item.page === "home" && (
                      <>
                        {/* One closed silhouette carries the active tint so the roof and walls do not leave a seam. */}
                        <path d="M5 9.4 12 4l7 5.4V21h-5v-7h-4v7H5z" stroke="none" />
                        <path d="m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9" fill="none" />
                      </>
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
        {(!isApp || (account && !checking)) && <WebsiteFooter />}
        <dialog
          ref={modalRef}
          tabIndex={-1}
          onCancel={dismissModal}
          onClick={(e) => {
            if (e.target === e.currentTarget) dismissModal();
          }}
        >
          {modal === "login" && (
            <>
              <h2>SunSync 시작하기</h2>
              <AuthLead className="muted" description="Google 계정으로 로그인하거나 새로 가입하세요." message={authNotice} />
              <GoogleButton autoFocus onClick={startLogin} />
              <button className="secondary" type="button" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          <DialogBodies app={app} captureHelpStep={captureMarker ? 2 : 1} addFurniture={(
            <>
              <h2>가구 추가</h2>
              <button className="secondary" onClick={()=>{addManualFurniture();openSettings();}}>
                직접 추가하고 위치·치수 입력
              </button>
              <button
                className="primary"
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
            </>
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
export default MobileApp;
