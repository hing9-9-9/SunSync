import { validateShadingPlan } from "./shading";
import { browserCaptureEnabled } from "./captureEnvironment";
import { MeasureError, measureAndRegister, savedCamera, endCaptureMarker } from "./measureApi";
import type { ShadingResult } from "./shading";
import { createHandoff, connectHandoff, completeHandoff, listMeasurements, predictSunpath, predictShadingPlan } from "./spaceApi";
import type { HandoffSession, SunpathPrediction } from "./spaceApi";
import { blockingSchedule, predictionDay, scheduleFromPrediction } from "./sunlight";
import { estimatedSchedule } from "./sunEstimate";
import type { BlockingSchedule } from "./sunlight";
import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { editorExample, exampleState, homeExampleSpace } from "./exampleSpace";
import { markInvalidFields } from "./formIssues";
import { saveErrorMessage } from "./spaceSync";
import { EXPIRED_MESSAGE, LOGIN_FAILED_MESSAGE, currentPath, fetchSession, googleLoginUrl, logout, onUnauthorized } from "./auth";
import type { Session } from "./auth";
import { mergeStash, saveStash, splitForStash, takeStash } from "./userStash";
import Plan from "./Plan";
import PredictionSummary from "./PredictionSummary";
import {
  blankSpace,
  defaultShadingTime,
  findOpenFurnitureSpot,
  placeCapturedFurniture,
  readState,
  resolveWindow,
  regionForFurniture,
  isUnfilledInput,
  repairRegions,
  regionOwners,
  withDefaultRegions,
  removeZone,
  STORAGE_KEY,
  uid,
  hasUnsavedChanges,
  updateFurniture,
  upsertSpace,
  validateFurnitureOverlap,
  validateSpace,
  validateShadingTime,
  } from "./model";
import type {
  Account,
  CapturedFurniture,
  Furniture,
  Region,
  ShadingTime,
  Space,
  } from "./model";
import {
  deleteServerSpace,
  getServerSpace,
  listServerSpaces,
  mergeServerSpaces,
  saveServerSpace,
  SpaceApiError,
  userMessage,
} from "./spaceApi";
import type { UnavailableScene } from "./spaceApi";

import {
  isEditingPage,
  shouldConfirmNavigation,
  locationKey,
  locationUrl,
  readLocation,
  resolveLocation,
  readTempDrafts,
  TEMP_DRAFTS_KEY,
} from "./navigation";
import type { BrowserLocation, Page, TempDraft } from "./navigation";

export const titles: Record<Page, string> = {
  home: "홈",
  spaces: "내 공간",
  register: "새 공간 등록",
  capture: "QR 측정",
  processing: "공간 구성",
  review: "도면 확인",
  protect: "도면과 보호 영역",
  detail: "공간 현황",
  guide: "사용 가이드",
  more: "더보기",
  account: "내 계정",
};


const LAST_EMAIL_KEY = "sunsync-ui-last-email";

// Off until real building data is connected: the status screen shows the simplified simulation.
// Turning it on shows FR-01 sun times with loading, error and retry states.
const USE_FR01 = true;

// Spaces saved before the window position was stored get it filled from the form defaults;
// the server only calculates sunlight through a window whose wall, offset and sill are explicit.
const withWindowPosition = (s: Space): Space =>
  s.windowDimensions ? { ...s, windowDimensions: resolveWindow(s) ?? s.windowDimensions } : s;

// State, persistence and server sync shared by the web (App) and mobile (MobileApp) shells.
export function useSpaceApp() {
  const [initial] = useState(() => {
    // ?example=editor: a local sample space that never reads or writes server data.
    if (editorExample) return exampleState();
    try {
      return readState(localStorage);
    } catch {
      return { version: 1 as const, account: null, spaces: [] };
    }
  });
  const [initialNavigation] = useState(() =>
    editorExample
      ? {
          page: "protect" as const,
          guideIndex: null,
          activeId: initial.spaces[0].id,
          draft: structuredClone(initial.spaces[0]),
        }
      : resolveLocation(
          readLocation(window.history.state, window.location.hash),
          true, // login is decided by the server session; personal pages show the login screen themselves
          null,
          initial.spaces,
        ),
  );
  // The server session decides who is signed in. The stored account is only what to show until it answers.
  const [authStatus, setAuthStatus] = useState<"checking" | "in" | "out">(editorExample ? "in" : "checking");
  const [authNotice, setAuthNotice] = useState(() => {
    try {
      return !editorExample && new URLSearchParams(location.search).get("login") === "failed" ? LOGIN_FAILED_MESSAGE : "";
    } catch {
      return "";
    }
  });
  const [isApp, setIsApp] = useState(
    () =>
      new URLSearchParams(location.search).get("mode") === "app" ||
      (new URLSearchParams(location.search).get("mode") !== "web" &&
        matchMedia("(max-width: 760px)").matches),
  );
  const [handoffLink, setHandoffLink] = useState("");
  const [deviceSession, setDeviceSession] = useState<HandoffSession | null>(
    null,
  );
  const [measurementJobs, setMeasurementJobs] = useState<HandoffSession[]>([]);
  const [mobileToken, setMobileToken] = useState<string | null>(null);
  const [measurementReady, setMeasurementReady] = useState(false);
  const [storedAccount, setAccount] = useState<Account | null>(initial.account);
  const account = authStatus === "out" ? null : storedAccount;
  const [spaces, setSpaces] = useState<Space[]>(initial.spaces);
  const [page, setPageState] = useState<Page>(initialNavigation.page);
  const [activeId, setActiveId] = useState<string | null>(
    initialNavigation.activeId ?? initial.spaces[0]?.id ?? null,
  );
  const [draft, setDraft] = useState<Space | null>(initialNavigation.draft);
  const [tempDrafts, setTempDrafts] = useState<TempDraft[]>(() => {
    try {
      return editorExample ? [] : readTempDrafts(localStorage);
    } catch {
      return [];
    }
  });
  const unsaved = useMemo(() => hasUnsavedChanges(draft, spaces), [draft, spaces]);
  // Leaving only asks when something changed since the last full or temporary save.
  const tempDraft = draft ? tempDrafts.find((t) => t.space.id === draft.id) : undefined;
  const leaveUnsaved = tempDraft ? hasUnsavedChanges(draft, [tempDraft.space]) : unsaved;
  const [selectedFurniture, setSelectedFurniture] = useState<string | null>(
    editorExample ? initial.spaces[0].furniture[0].id : null,
  );
  const [selectedRegion, setSelectedRegion] = useState<string | null>(null);
  const [tool, setTool] = useState<"furniture" | "region">("furniture");
  const [modal, setModal] = useState<
    | "login"
    | "logout"
    | "discard"
    | "add-furniture"
    | "reload-server"
    | "delete-space"
    | "delete-temp"
    | "delete-item"
    | "capture-help"
    | null
  >(null);
  const [error, setError] = useState("");
  // Inline warnings start once the user edits this draft; opening an already-saved space with overlapping furniture shows its overlap notice right away.
  const [editedDraftId, setEditedDraftId] = useState<string | null>(null);
  const inputEdited = !!draft && editedDraftId === draft.id;
  const draftOverlapWarning = draft ? validateFurnitureOverlap(draft) : null;
  const liveMessage = inputEdited && draft ? validateSpace(draft) : null;
  // Empty or half-typed boxes are reported when the person moves on, not while typing.
  const draftWarning = (isUnfilledInput(liveMessage) ? null : liveMessage) ?? draftOverlapWarning;
  const setPage = (next: Page) => {
    setError("");
    setPageState(next);
  };
  const [toast, setToast] = useState("");
  // Editor deletions ask first (same dialog as space delete), then apply and report.
  const [pendingDelete, setPendingDelete] = useState<{ next: Space; title: string; body?: string; after?: () => void } | null>(null);
  function askDelete(next: Space, title: string, body?: string, after?: () => void) {
    if (!draft) return;
    setPendingDelete({ next, title, body, after });
    setModal("delete-item");
  }
  function confirmDelete() {
    if (!pendingDelete) return;
    setDraft(pendingDelete.next);
    pendingDelete.after?.();
    setPendingDelete(null);
    setModal(null);
    setToast("삭제했습니다.");
  }
  const [guideIndex, setGuideIndex] = useState<number | null>(
    initialNavigation.guideIndex,
  );
  const [pendingPage, setPendingPage] = useState<Page>("home");
  const [addingByQr, setAddingByQr] = useState(false);
  const qrReturnPage = useRef<Page>("review");
  const [progress, setProgress] = useState(0);
  const [hour, setHour] = useState(14);
  const [storageError, setStorageError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverState, setServerState] = useState<
    "loading" | "online" | "offline"
  >("loading");
  const [serverError, setServerError] = useState("");
  const [unavailable, setUnavailable] = useState<UnavailableScene[]>([]);
  const [conflict, setConflict] = useState(false);
  const [locationStatus, setLocationStatus] = useState("");
  const [locating, setLocating] = useState(false);
  const locationAttempts = useRef(new Set<string>());
  const locationRequest = useRef(0);
  useEffect(() => {
    if (page !== "spaces" || authStatus !== "in") return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function refreshJobs() {
      try { const [jobs, remote] = await Promise.all([listMeasurements(controller.signal), listServerSpaces(controller.signal)]); if (!stopped) { setMeasurementJobs(jobs);setSpaces(current=>mergeServerSpaces(current,remote.spaces,remote.unavailable,remote.deleted));setServerState("online"); } }
      catch (error) { if(!stopped) {setServerState("offline");setServerError(userMessage(error, "측정 상태를 불러오지 못했습니다. 잠시 후 자동으로 다시 확인합니다."));} }
      if (!stopped) timer = setTimeout(refreshJobs, 3000);
    }
    void refreshJobs();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [page, authStatus]);
  const receiveMeasurement = useCallback((saved: Space) => {
    setDraft(saved);
    setActiveId(saved.id);
    setSpaces((current) => upsertSpace(current, saved));
    setMeasurementReady(true);
    setToast("휴대폰 측정 결과 도착 · 도면 확인 가능");
  }, []);
  function joinMeasurement(session: HandoffSession) {
    setMobileToken(session.token);
    connectHandoff(session.token)
      .then((data) => {
        if (!data.space) return;
        setDraft(data.space);
        setActiveId(data.space.id);
        setSelectedFurniture(null);
        setAddingByQr(data.space.furniture.length > 0);
        qrReturnPage.current = "review";
        setMeasurementReady(false);
        setPage("capture");
      })
      .catch((e) =>
        setError(userMessage(e, "휴대폰과 연결하지 못했습니다. 측정 요청을 다시 열어 주세요.")),
      );
  }
  useEffect(() => {
    const token = new URLSearchParams(location.search).get("handoff");
    if (!token || authStatus !== "in") return;
    let cancelled = false;
    connectHandoff(token)
      .then((data) => {
        if (cancelled || !data.space) return;
        setMobileToken(token);
        setDraft(data.space);
        setActiveId(data.space.id);
        setAddingByQr(data.space.furniture.length > 0);
        qrReturnPage.current = "review";
        setPageState("capture");
      })
      .catch((e) => {
        if (!cancelled)
          setError(userMessage(e, "촬영 화면에 연결하지 못했습니다. PC에서 받은 링크를 다시 열어 주세요."));
      });
    return () => {
      cancelled = true;
    };
  }, [authStatus]);
  useEffect(() => {
    const media = matchMedia("(max-width: 760px)");
    const update = () => {
      const mode = new URLSearchParams(location.search).get("mode");
      setIsApp(mode === "app" || (mode !== "web" && media.matches));
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  function locateBuilding(id: string, replace = false) {
    if (editorExample) return;
    const requestId = ++locationRequest.current;
    if (!window.isSecureContext || !navigator.geolocation) {
      setLocationStatus(
        "현재 위치를 사용할 수 없습니다. HTTPS로 접속하거나 직접 입력해 주세요.",
      );
      return;
    }
    setLocating(true);
    setLocationStatus("현재 위치 확인 중…");
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        if (requestId !== locationRequest.current) return;
        setLocating(false);
        setDraft((current) => {
          if (!current || current.id !== id || (!replace && current.site))
            return current;
          return {
            ...current,
            revision: current.revision + 1,
            site: {
              latitude: coords.latitude,
              longitude: coords.longitude,
              timezone: current.site?.timezone ?? "Asia/Seoul",
              northConfirmed: true,
            },
          };
        });
        setLocationStatus(
          `현재 위치 입력 완료 · 위치 정확도 약 ${Math.round(coords.accuracy)}m. 건물에서 떨어져 있다면 직접 수정해 주세요.`,
        );
      },
      (error) => {
        if (requestId !== locationRequest.current) return;
        setLocating(false);
        setLocationStatus(
          error.code === 1
            ? "위치 권한이 허용되지 않았습니다. 브라우저의 위치 권한을 허용하거나 직접 입력해 주세요."
            : error.code === 3
              ? "위치 확인 시간이 초과됐습니다. 다시 시도하거나 직접 입력해 주세요."
              : "현재 위치를 찾지 못했습니다. 다시 시도하거나 직접 입력해 주세요.",
        );
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
    );
  }
  useEffect(() => {
    if (
      page !== "register" ||
      !draft ||
      draft.site ||
      locationAttempts.current.has(draft.id)
    )
      return;
    locationAttempts.current.add(draft.id);
    locateBuilding(draft.id);
  }, [page, draft]);

  const modalRef = useRef<HTMLDialogElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const draftPage = useRef<Page>("register");
  const pendingWebAction = useRef<(() => void) | null>(null);
  const takePendingWebAction = () => {
    const pending = pendingWebAction.current;
    pendingWebAction.current = null;
    return pending;
  };
  const rememberQrReturn = (next: Page) => {
    qrReturnPage.current = next;
  };
  // Saving an editor is explicit. Never recreate server-missing spaces from a browser cache.
  const [syncFailed] = useState<string[]>([]);
  const retryUpload = () => { void refreshServer(); };
  const active = spaces.find((s) => s.id === activeId) ?? spaces[0];
  const editing = [
    "register",
    "capture",
    "processing",
    "review",
    "protect",
  ].includes(page);
  useEffect(() => {
    if (page !== "detail" || !active?.serverRevision || authStatus !== "in") return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const id = active.id, seen = active.serverRevision;
    async function refreshDetail() {
      try {
        const saved = await getServerSpace(id);
        if (!stopped && saved.serverRevision !== seen) {
          setSpaces(current => current.map(old => old.id !== id ? old : {...saved, zone: saved.zones.includes(old.zone) ? old.zone : saved.zone}));
          setToast("다른 기기에서 바뀐 내용을 불러왔습니다.");
        }
      } catch { /* Preserve the visible space during a temporary network failure. */ }
      if (!stopped) timer = setTimeout(refreshDetail, 3000);
    }
    void refreshDetail();
    return () => { stopped = true; clearTimeout(timer); };
  }, [page, active?.id, active?.serverRevision, authStatus]);
  const space = editing ? draft : active;
  // When sunlight reaches each protected area (simplified demo calculation). Blocking itself only
  // happens inside the time range the user sets.
  // FR-01: the saved space's real sun times for today. Results are keyed by scene revision, so a
  // result for an older save is never shown after the space changes.
  const [predictionNow] = useState(() => new Date());
  // Today in the space's time zone; the forecast has no date picker.
  const predictionDate = active?.site ? new Intl.DateTimeFormat("en-CA", { timeZone: active.site.timezone }).format(predictionNow) : "";
  const windowReady = !!active?.windowDimensions && active.windowDimensions.wall != null && active.windowDimensions.offset != null && active.windowDimensions.sill != null;
  const predictionKey =
    USE_FR01 && authStatus === "in" && page === "detail" && !editorExample && active?.serverSceneId && active.serverRevision && active.site && windowReady && active.regions.length
      ? `${active.serverSceneId}:${active.serverRevision}:${predictionDate}`
      : null;
  const [prediction, setPrediction] = useState<{ key: string; result?: SunpathPrediction; error?: string } | null>(null);
  const [predictionRun, setPredictionRun] = useState(0);
  const sceneId = active?.serverSceneId, sceneRevision = active?.serverRevision, timeZone = active?.site?.timezone;
  useEffect(() => {
    if (!predictionKey || !sceneId || !sceneRevision || !timeZone) return;
    let stopped = false;
    predictSunpath(sceneId, sceneRevision, predictionDay(timeZone, new Date(`${predictionDate}T12:00:00Z`)).start).then(
      (result) => !stopped && setPrediction({ key: predictionKey, result }),
      (error) => !stopped && setPrediction({ key: predictionKey, error: userMessage(error, "햇빛 예측을 불러오지 못했습니다. 다시 계산해 주세요.") }),
    );
    return () => {
      stopped = true;
    };
  }, [predictionKey, predictionRun, sceneId, sceneRevision, timeZone, predictionDate]);
  const [shadingPrediction, setShadingPrediction] = useState<(ShadingResult & { key: string; run: number }) | null>(null);
  useEffect(() => {
    if (!predictionKey || !sceneId || !sceneRevision || !timeZone) return;
    let stopped = false;
    const start = predictionDay(timeZone, new Date(`${predictionDate}T12:00:00Z`)).start;
    predictShadingPlan(sceneId, sceneRevision, start).then(
      plan => {
        const checked = validateShadingPlan(plan, sceneId, sceneRevision, start);
        if (!stopped) setShadingPrediction({ key: predictionKey, run: predictionRun, plan: checked });
      },
    ).catch(error => {
      if (stopped) return;
      if (error instanceof SpaceApiError && error.status === 404) setShadingPrediction({ key: predictionKey, run: predictionRun, empty: true });
      else setShadingPrediction({ key: predictionKey, run: predictionRun,
        error: userMessage(error, "차광 계획을 불러오지 못했습니다. 다시 계산해 주세요.") });
    });
    return () => { stopped = true; };
  }, [predictionKey, predictionRun, sceneId, sceneRevision, timeZone, predictionDate]);
  const filmPrediction: ShadingResult | undefined = predictionKey
    ? shadingPrediction?.key === predictionKey && shadingPrediction.run === predictionRun ? shadingPrediction : {}
    : undefined;
  const currentPrediction = prediction?.key === predictionKey ? prediction : null;
  let predicted: BlockingSchedule | undefined, predictionError = currentPrediction?.error;
  if (currentPrediction?.result && active) {
    try {
      predicted = scheduleFromPrediction(active, currentPrediction.result);
    } catch {
      predictionError = "예측 결과와 저장된 보호 영역이 맞지 않습니다. 다시 계산해 주세요.";
    }
  }
  const retryPrediction = () => {
    setPrediction(null);
    setPredictionRun((n) => n + 1);
  };
  const savedOverlapWarning = space ? validateFurnitureOverlap(space) : null;
  const sunSource = predictionKey && active?.site
    ? {
        label: `햇빛이 닿는 시간 (${predictionDate})`,
        note: (
          <>
            {predictionError && (
              <p className="error" role="alert">
                {predictionError}{" "}
                <button type="button" className="text-button" onClick={retryPrediction}>다시 계산</button>
              </p>
            )}
            <details className="forecast-details">
              <summary className="muted small">예측 근거 자세히</summary>
              <PredictionSummary
                result={!predictionError ? currentPrediction?.result : undefined}
                space={active}
                date={predictionDate}
                onRetry={retryPrediction}
              />
            </details>
          </>
        ),
      }
    : active && !editorExample ? { label: "예측 준비 필요", note: <p className="muted small">햇빛 예측을 사용하려면 보호 영역과 창문 위치를 확인한 뒤 공간을 저장하세요. 현재는 간이 계산으로 표시합니다.</p> } : undefined;
  const predictionPill = !predictionKey ? "시뮬레이션" : predictionError ? "예측 실패" : predicted ? "햇빛 예측" : "계산 중";
  const sunSchedule = useMemo(
    () =>
      page === "detail" && active
        ? predictionKey
          ? (predicted ?? new Map())
          : blockingSchedule(active)
        : page === "protect" && draft
          ? estimatedSchedule(draft)
          : new Map(),
    [page, active, draft, predictionKey, predicted],
  );
  // Detail page: the user's unsaved edit of the blocking time (null = showing the saved value).
  // Keyed by space so an unsaved edit never carries over to another space.
  const [shadingDraft, setShadingDraft] = useState<{ id: string; time: ShadingTime } | null>(null);
  const shadingEdit = shadingDraft && shadingDraft.id === active?.id ? shadingDraft.time : null;
  const setShadingEdit = (time: ShadingTime | null) =>
    setShadingDraft(time && active ? { id: active.id, time } : null);
  const detailShading = shadingEdit ?? active?.shadingSchedule ?? defaultShadingTime;
  const activeRegionCount =
    active?.regions.filter((r) => r.zone === active.zone && r.grade !== "D")
      .length ?? 0;
  const furniture = space?.furniture.find((f) => f.id === selectedFurniture);
  const region = space?.regions.find((r) => r.id === selectedRegion);

  const historyReady = useRef(false);
  useEffect(() => {
    const route: BrowserLocation = { page, guideIndex, activeId, draft };
    const current = readLocation(window.history.state, window.location.hash);
    const state = { ...window.history.state, sunSyncNavigation: route };
    // Form edits replace the current entry; only screen/guide/space changes add an entry.
    if (
      !historyReady.current ||
      locationKey(current) === locationKey(route) ||
      current.page === "processing"
    ) {
      window.history.replaceState(state, "", locationUrl(route));
    } else {
      window.history.pushState(state, "", locationUrl(route));
    }
    historyReady.current = true;
  }, [page, guideIndex, activeId, draft]);
  useEffect(() => {
    function restore() {
      const target = resolveLocation(
        readLocation(window.history.state, window.location.hash),
        true,
        draft,
        spaces,
      );
      if (draft && isEditingPage(page) && !isEditingPage(target.page)) {
        if (leaveUnsaved) {
          // Back out of an edited editor: put the editor entry back and ask first.
          const here: BrowserLocation = { page, guideIndex, activeId, draft };
          window.history.pushState({ ...window.history.state, sunSyncNavigation: here }, "", locationUrl(here));
          setPendingPage(target.page);
          setModal("discard");
          return;
        }
        target.draft = null;
      }
      window.history.replaceState(
        { ...window.history.state, sunSyncNavigation: target },
        "",
        locationUrl(target),
      );
      setError("");
      setModal(null);
      pendingWebAction.current = null;
      setPageState(target.page);
      setGuideIndex(target.guideIndex);
      setActiveId(target.activeId);
      setDraft(target.draft);
      const scene = isEditingPage(target.page)
        ? target.draft
        : spaces.find((s) => s.id === target.activeId);
      setSelectedFurniture((id) =>
        scene?.furniture.some((f) => f.id === id)
          ? id
          : isEditingPage(target.page) ? null : (scene?.furniture[0]?.id ?? null),
      );
      setSelectedRegion((id) =>
        scene?.regions.some((r) => r.id === id) ? id : null,
      );
    }
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [draft, spaces, page, guideIndex, activeId, leaveUnsaved]);

  // Every account gets the home page's example room once and keeps it until the person deletes it.
  // It is a normal space: deleting it is final (a flag per account on this browser).
  async function giveExampleSpace(email: string, signal: AbortSignal, existing: Space[]) {
    const example = homeExampleSpace();
    if (existing.some((x) => x.building === example.building && x.name === example.name)) return;
    const key = `sunsync-example-given:${email || "local"}`;
    try {
      if (localStorage.getItem(key)) return;
      localStorage.setItem(key, "1");
    } catch { return; }
    try {
      const saved = await saveServerSpace(example);
      if (!signal.aborted) setSpaces((current) => upsertSpace(current, saved));
    } catch {
      try { localStorage.removeItem(key); } catch { /* not remembered */ }
    }
  }
  useEffect(() => {
    if (editorExample || authStatus !== "in") return;
    const controller = new AbortController();
    listServerSpaces(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setSpaces((current) => mergeServerSpaces(current, result.spaces, result.unavailable, result.deleted));
        setUnavailable(result.unavailable);
        setServerState("online");
        setServerError("");
        void giveExampleSpace(account?.email ?? "", controller.signal, result.spaces);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setServerState("offline");
        setServerError(
          userMessage(error, "서버 연결을 확인해 주세요."),
        );
      });
    return () => controller.abort();
  }, [authStatus]);
  async function refreshServer() {
    if (editorExample || authStatus !== "in") return;
    setServerState("loading");
    try {
      const result = await listServerSpaces();
      setSpaces((current) => mergeServerSpaces(current, result.spaces, result.unavailable, result.deleted));
      setUnavailable(result.unavailable);
      setServerState("online");
      setServerError("");
    } catch (error) {
      setServerState("offline");
      setServerError(
        userMessage(error, "서버 연결을 확인해 주세요."),
      );
    }
  }
  async function reloadServerDraft() {
    if (!draft) return;
    setModal(null);
    setSaving(true);
    try {
      const saved = await getServerSpace(draft.id);
      setDraft(saved);
      setSpaces((current) => upsertSpace(current, saved));
      setSelectedFurniture(null);
      setSelectedRegion(null);
      setConflict(false);
      setError("");
      setToast("서버의 최신 공간을 불러왔습니다.");
    } catch (error) {
      setError(
        userMessage(error, "공간을 불러오지 못했습니다."),
      );
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (editorExample || authStatus !== "in") return;
    const id = new URLSearchParams(location.search).get("space");
    if (!id || new URLSearchParams(location.search).has("handoff")) return;
    let cancelled = false;
    getServerSpace(id)
      .then((saved) => {
        if (cancelled) return;
        setSpaces((current) => upsertSpace(current, saved));
        setDraft(saved);
        setActiveId(saved.id);
        setSelectedFurniture(null);
        if (isApp) {
          setAddingByQr(saved.furniture.length > 0);
          qrReturnPage.current = "review";
        }
        setPageState(isApp ? "capture" : "protect");
      })
      .catch((error) => {
        if (!cancelled)
          setError(
            userMessage(error, "공간을 불러오지 못했습니다."),
          );
      });
    return () => {
      cancelled = true;
    };
  }, [isApp, authStatus]);
  async function transferSpace(target: "app" | "web") {
    if (editorExample) {
      setToast("둘러보기 화면에서는 휴대폰 연결을 쓸 수 없습니다.");
      return;
    }
    if (!draft) return;
    const message = validateSpace(draft);
    if (
      target === "app" &&
      (!draft.windowDimensions?.width || !draft.windowDimensions?.height)
    ) {
      setError("촬영 전 공간 정보에서 창문 가로와 높이를 입력해 주세요.");
      return;
    }
    if (message || !draft.site) {
      setError(message ?? "건물 위치를 입력해 주세요.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const saved =
        target === "web" && mobileToken
          ? await completeHandoff(mobileToken, draft)
          : await saveServerSpace(withWindowPosition(draft));
      if (target === "web" && mobileToken) {
        setMobileToken(null);
        setToast("촬영 결과 저장 완료 · PC에서 도면 확인 가능");
      }
      setDraft(saved);
      setSpaces((current) => upsertSpace(current, saved));
      setActiveId(saved.id);
      if (target === "app") {
        setDeviceSession(await createHandoff(saved.id));
        setMeasurementReady(false);
        setToast("측정 요청 완료 · 앱의 내 공간에서 촬영 시작");
        return;
      }
      const url = new URL(location.href);
      url.search = new URLSearchParams({
        mode: target,
        space: saved.id,
      }).toString();
      url.hash = "/protect";
      setHandoffLink(url.toString());
      setToast("연결할 공간을 서버에 저장했습니다.");
    } catch (error) {
      setError(saveErrorMessage(error, "공간을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    } finally {
      setSaving(false);
    }
  }
  // applySession runs from a one-time effect; this keeps it reading the current values.
  const latest = useRef({ spaces, draft, tempDrafts });
  useEffect(() => {
    latest.current = { spaces, draft, tempDrafts };
  });
  function applySession(session: Session) {
    if (session.status === "out") {
      setAccount(null);
      setAuthStatus("out");
      return;
    }
    // Server without auth routes (404) or offline: keep working as before with what is stored.
    const user = session.status === "in" ? session.user : (initial.account ?? { name: "SunSync 사용자", email: "" });
    // With the login off (local development) everyone is the same development user: no switch.
    if (session.status === "in" && session.authEnabled) {
      let last = initial.account?.email || "";
      try { last ||= localStorage.getItem(LAST_EMAIL_KEY) ?? ""; } catch { /* storage blocked */ }
      if (last && user.email && last.toLowerCase() !== user.email.toLowerCase()) {
        // Another person: server copies are dropped (they return from the server); their local-only
        // work is parked under their email and disappears from this person's screens.
        try { saveStash(localStorage, last, splitForStash(latest.current.spaces, latest.current.draft, latest.current.tempDrafts)); } catch { /* storage blocked */ }
        setSpaces([]);
        setUnavailable([]);
        setDraft(null);
        setTempDrafts([]);
        setActiveId(null);
        setPageState((p) => (isEditingPage(p) || p === "detail" ? "spaces" : p));
      }
      // Work this person parked earlier on this device comes back.
      let back = null;
      try { back = takeStash(localStorage, user.email); } catch { /* storage blocked */ }
      if (back) {
        const parked = back;
        const base = last && user.email && last.toLowerCase() !== user.email.toLowerCase() ? { spaces: [], tempDrafts: [] } : { spaces: latest.current.spaces, tempDrafts: latest.current.tempDrafts };
        const merged = mergeStash(base, parked);
        setSpaces(merged.spaces);
        setTempDrafts(merged.tempDrafts);
      }
      try { if (user.email) localStorage.setItem(LAST_EMAIL_KEY, user.email); } catch { /* storage blocked */ }
    }
    setAccount(user);
    setAuthNotice("");
    setAuthStatus("in");
  }
  useEffect(() => {
    if (editorExample) return;
    const controller = new AbortController();
    fetchSession(controller.signal).then(applySession, () => {});
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once on load
  }, []);
  // Drop the failed-login marker from the address once it has been read; other params and the hash stay.
  useEffect(() => {
    const url = new URL(location.href);
    if (!url.searchParams.has("login")) return;
    url.searchParams.delete("login");
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
  }, []);
  // Any request answered 401: back to the login screen, local spaces and drafts untouched.
  useEffect(
    () =>
      onUnauthorized(() => {
        if (editorExample) return;
        setAccount(null);
        setAuthStatus("out");
        setAuthNotice(EXPIRED_MESSAGE);
        setModal((m) => (m === "login" ? m : null));
      }),
    [],
  );
  // After a failed Google login the web lands on the public home page, so the login dialog shows the notice.
  useEffect(() => {
    if (authStatus === "out" && authNotice === LOGIN_FAILED_MESSAGE && !isApp && (page === "home" || page === "guide")) setModal("login");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the session answer arrives
  }, [authStatus]);
  // Web, signed out on a private page (session expired, or a deep link): show the public home with the login dialog, and come back to this page after signing in.
  useEffect(() => {
    if (authStatus !== "out" || isApp || editorExample || page === "home" || page === "guide") return;
    loginNext.current = currentPath();
    setPage("home");
    setModal("login");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the session answer arrives or the page changes while signed out
  }, [authStatus, page]);
  // Once the session answer arrives, the address (a deep link) is resolved again with the real spaces.
  useEffect(() => {
    if (authStatus !== "in" || editorExample) return;
    const target = resolveLocation(readLocation(window.history.state, window.location.hash), true, draft, spaces);
    if (target.page !== page) {
      setPageState(target.page);
      setActiveId(target.activeId);
      setGuideIndex(target.guideIndex);
      setDraft(target.draft);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the session answer arrives
  }, [authStatus]);
  // Storage failures must be surfaced to avoid silently losing saved spaces.
  useEffect(() => {
    if (editorExample) return;
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ version: 1, account: storedAccount, spaces }),
      );
      // eslint-disable-next-line react/set-state-in-effect -- Reflect external storage availability in the UI.
      setStorageError(false);
    } catch {
      // eslint-disable-next-line react/set-state-in-effect -- Reflect external storage availability in the UI.
      setStorageError(true);
    }
  }, [storedAccount, spaces]);
  useEffect(() => {
    if (editorExample) return;
    try {
      localStorage.setItem(TEMP_DRAFTS_KEY, JSON.stringify(tempDrafts));
    } catch {
      // eslint-disable-next-line react/set-state-in-effect -- Reflect external storage availability in the UI.
      setStorageError(true);
    }
  }, [tempDrafts]);
  // Errors take focus so they are read out; a form error focuses its first wrong field instead.
  const fieldFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (error) {
      const target = fieldFocus.current ?? errorRef.current;
      fieldFocus.current = null;
      target?.focus();
      target?.scrollIntoView({ block: "center" });
    }
  }, [error]);
  // A new page moves keyboard and screen-reader focus to its content (not on first load).
  const shownPage = useRef(page);
  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.title = `${titles[page]} · SunSync`;
    if (shownPage.current !== page) {
      shownPage.current = page;
      document.querySelector<HTMLElement>("#main, #ap-main, #award-guide-main")?.focus({ preventScroll: true });
    }
    if (["register", "capture", "review", "protect"].includes(page))
      draftPage.current = page;
  }, [page]);
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => {
      setToast("");
    }, 3500);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    const d = modalRef.current;
    if (modal && d && !d.open) {
      d.showModal();
      d.focus();
    } else if (!modal && d?.open) d.close();
  }, [modal]);
  // True while real measurement runs, so the processing screen waits for it instead of a timer.
  const measuring = useRef(false);
  useEffect(() => {
    if (page !== "processing" || measuring.current) return;
    const id = window.setInterval(
      () => setProgress((p) => Math.min(100, p + 17)),
      320,
    );
    const done = window.setTimeout(() => {
      setPageState(qrReturnPage.current);
      setToast("도면 생성 완료 · 치수를 확인해 주세요");
    }, 2300);
    return () => {
      clearInterval(id);
      clearTimeout(done);
    };
  }, [page]);

  useEffect(() => {
    if (!leaveUnsaved) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [leaveUnsaved]);

  function navigate(next: Page) {
    if (next === page) return;
    if (shouldConfirmNavigation(!!draft, page, next)) {
      // An editor left without edits (e.g. reopened with Back after saving) closes quietly.
      if (leaveUnsaved) {
        setPendingPage(next);
        setModal("discard");
        return;
      }
      setDraft(null);
    }
    if (next === "guide") {
      setGuideIndex(null);
    }
    setPage(next);
  }
  // Confirm dialog choices: keep the edit as a temporary draft on this device, or drop it.
  function leaveEditor(keep: boolean) {
    if (keep && draft) {
      const entry = { space: draft, page: draftPage.current, savedAt: new Date().toISOString() };
      setTempDrafts((current) => [entry, ...current.filter((t) => t.space.id !== draft.id)]);
      setToast("입력한 내용을 임시 저장했습니다.");
    }
    setDraft(null);
    setModal(null);
    if (pendingPage === "guide") setGuideIndex(null);
    setPage(pendingPage);
  }
  function dropTempDraft(id: string) {
    setTempDrafts((current) => current.filter((t) => t.space.id !== id));
  }
  // The list's delete asks first; the draft only lives on this device, so it cannot be restored.
  const [tempDeleteId, setTempDeleteId] = useState<string | null>(null);
  function askDeleteTempDraft(id: string) {
    setTempDeleteId(id);
    setModal("delete-temp");
  }
  function confirmDeleteTempDraft() {
    if (tempDeleteId) dropTempDraft(tempDeleteId);
    setTempDeleteId(null);
    setModal(null);
    setToast("작성 중인 공간을 삭제했습니다.");
  }
  function resumeTempDraft(t: TempDraft, at: Page = t.page) {
    const server = spaces.find((s) => s.id === t.space.id);
    if (server) setActiveId(server.id);
    if (server?.serverRevision && t.space.serverRevision !== server.serverRevision) setToast("서버에 최근 임시 저장본이 있어요");
    setDraft(repairRegions(structuredClone(t.space)));
    setSelectedFurniture(null);
    setSelectedRegion(null);
    setPage(at);
  }
  // Where Google sends the person back to: the page they wanted (hash route), not the landing.
  const loginNext = useRef<string | null>(null);
  const pathFor = (wanted: Page) =>
    location.pathname + location.search + locationUrl({ page: wanted, guideIndex: null, activeId: null, draft: null });
  function startLogin() {
    location.assign(googleLoginUrl(loginNext.current ?? currentPath()));
  }
  function requireAccount(action: () => void, wanted?: Page) {
    if (authStatus === "in") action();
    else {
      pendingWebAction.current = action;
      loginNext.current = wanted ? pathFor(wanted) : null;
      setModal("login");
    }
  }
  async function signOut() {
    await logout();
    setAccount(null);
    setAuthStatus("out");
    setAuthNotice("");
    setDraft(null);
    setPage("home");
    setModal(null);
  }
  function dismissModal() {
    pendingWebAction.current = null;
    setModal(null);
  }
  function begin() {
    if (draft) {
      setPage(draftPage.current);
      return;
    }
    setAddingByQr(false);
    setDraft(blankSpace());
    setSelectedFurniture(null);
    setSelectedRegion(null);
    setPage("register");
  }
  function selectSpace(s: Space) {
    setShadingEdit(null);
    setActiveId(s.id);
    setSelectedFurniture(s.furniture[0]?.id ?? null);
    setSelectedRegion(null);
    setPage("detail");
    setModal(null);
  }
  async function saveShadingTime() {
    if (!active || !shadingEdit || saving) return;
    const message = validateShadingTime(shadingEdit);
    if (message) {
      setError(message);
      return;
    }
    const next = { ...active, shadingSchedule: shadingEdit, revision: active.revision + 1 };
    const spaceMessage = validateSpace(next);
    if (spaceMessage) {
      setError(spaceMessage);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const saved = editorExample ? next : await saveServerSpace(next);
      setSpaces((current) => upsertSpace(current, saved));
      setShadingEdit(null);
      setToast(
        shadingEdit.enabled
          ? `차단 시간 ${shadingEdit.start} ~ ${shadingEdit.end} 저장`
          : "차단 시간을 끄고 저장했습니다.",
      );
    } catch (error) {
      setError(saveErrorMessage(error, "차단 시간을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    } finally {
      setSaving(false);
    }
  }
  function editSpace(s: Space, at: Page = "protect") {
    const temp = tempDrafts.find((t) => t.space.id === s.id);
    if (temp) {
      resumeTempDraft(temp, at);
      return;
    }
    setActiveId(s.id);
    setDraft(repairRegions(structuredClone(s)));
    setSelectedFurniture(null);
    setSelectedRegion(null);
    setPage(at);
  }
  function change(patch: Partial<Space>) {
    setError("");
    setDraft((s) => (s ? { ...s, ...patch, revision: s.revision + 1 } : s));
  }
  function changeFurniture(f: Furniture) {
    setError("");
    setDraft((s) => (s ? updateFurniture(s, f) : s));
  }
  function chooseFurniture(id: string) {
    setSelectedFurniture(id);
    setSelectedRegion(
      space?.regions.find((r) => r.furnitureId === id)?.id ?? null,
    );
  }
  function chooseRegion(id: string) {
    setSelectedRegion(id);
    setSelectedFurniture(
      space?.regions.find((r) => r.id === id)?.furnitureId ?? null,
    );
  }
  function patchRegion(patch: Partial<Region>) {
    if (draft && region)
      change({
        regions: draft.regions.map((r) =>
          r.id === region.id ? { ...r, ...patch } : r,
        ),
      });
  }
  function protectFurniture() {
    if (!draft || !furniture) return;
    const existing = draft.regions.find((r) => r.furnitureId === furniture.id);
    if (existing) {
      setSelectedRegion(existing.id);
      return;
    }
    const r = regionForFurniture(furniture);
    change({ regions: [...draft.regions, r] });
    setSelectedRegion(r.id);
  }
  // Deletes on the server first, so a failed delete leaves the space everywhere as it was.
  const [deleting, setDeleting] = useState(false);
  async function deleteSpace() {
    if (!active || deleting) return;
    setDeleting(true);
    try {
      if (!editorExample) await deleteServerSpace(active.id);
      setSpaces((current) => current.filter((s) => s.id !== active.id));
      dropTempDraft(active.id);
      setActiveId(null);
      setDraft(null);
      setModal(null);
      setPageState("spaces");
      setToast("공간을 삭제했습니다.");
    } catch (error) {
      setModal(null);
      setError(
        error instanceof SpaceApiError && error.status === 0
          ? "서버에 연결할 수 없어 삭제하지 못했습니다. 서버 연결 후 다시 시도해 주세요."
          : userMessage(error, "공간을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요."),
      );
    } finally {
      setDeleting(false);
    }
  }
  async function save() {
    if (!draft || saving) return false;
    const message = validateSpace(draft);
    if (message) {
      setError(message);
      return false;
    }
    if (page !== "register" && !draft.furniture.length && !draft.regions.length) {
      setError("가구 또는 보호 영역을 추가해 주세요.");
      return false;
    }
    if (!draft.site) {
      setPage("register");
      setError("서버 저장에 필요한 건물의 위도·경도를 입력해 주세요.");
      return false;
    }
    setSaving(true);
    setConflict(false);
    setError("");
    try {
      // A phone measuring for the web completes its request, so the web stops waiting on it.
      const finishing = !editorExample && !!mobileToken;
      const saved = editorExample
        ? { ...draft, savedAt: new Date().toISOString() }
        : finishing
          ? await completeHandoff(mobileToken!, withWindowPosition(draft))
          : await saveServerSpace(withWindowPosition(draft));
      if (finishing) setMobileToken(null);
      setSpaces((current) => upsertSpace(current, saved));
      dropTempDraft(saved.id);
      setActiveId(saved.id);
      setDraft(null);
      setPageState("detail");
      setServerState("online");
      setServerError("");
      setToast(
        editorExample
          ? "둘러보기 공간 저장 완료"
          : finishing
            ? "측정 결과를 저장했습니다. PC에서 배치를 확인할 수 있어요."
            : "공간과 보호 영역을 서버에 저장했습니다.",
      );
      return true;
    } catch (error) {
      setError(
        saveErrorMessage(error, "서버에 저장하지 못했습니다."),
      );
      setConflict(error instanceof SpaceApiError && error.status === 409);
      if (error instanceof SpaceApiError && error.status === 0)
        setServerState("offline");
      return false;
    } finally {
      setSaving(false);
    }
  }

  function renameZone(name: string) {
    if (!draft) return;
    change({
      zone: name,
      zones: draft.zones.map((z) => (z === draft.zone ? name : z)),
      furniture: draft.furniture.map((f) =>
        f.zone === draft.zone ? { ...f, zone: name } : f,
      ),
      regions: draft.regions.map((r) =>
        r.zone === draft.zone ? { ...r, zone: name } : r,
      ),
    });
  }
  function switchZone(zone: string) {
    const f = space?.furniture.find((f) => f.zone === zone);
    setSelectedFurniture(f?.id ?? null);
    setSelectedRegion(null);
    if (draft && editing) setDraft({ ...draft, zone });
    else if (active)
      setSpaces((s) => s.map((x) => (x.id === active.id ? { ...x, zone } : x)));
  }
  function addZone() {
    if (!draft) return;
    let n = draft.zones.length + 1;
    while (draft.zones.includes(`구역 ${String(n).padStart(2, "0")}`)) n++;
    const zone = `구역 ${String(n).padStart(2, "0")}`;
    change({ zones: [...draft.zones, zone], zone });
    setSelectedFurniture(null);
    setSelectedRegion(null);
  }
  const zoneTabs = space && (
    <div className="zone-tabs" aria-label="구역 선택">
      {space.zones.map((z) => (
        <button
          type="button"
          key={z}
          className={`chip ${z === space.zone ? "active" : ""}`}
          aria-pressed={z === space.zone}
          onClick={() => switchZone(z)}
        >
          {z}
        </button>
      ))}
      {draft && editing && (
        <>
          <button type="button" className="zone-action" onClick={addZone}>
            ＋ 구역 추가
          </button>
          <button
            type="button"
            className="zone-action zone-delete"
            disabled={draft.zones.length <= 1}
            title={draft.zones.length <= 1 ? "최소 1개 구역 필요" : undefined}
            onClick={() => {
              const next = removeZone(draft, draft.zone);
              const furnitureCount = draft.furniture.filter((f) => f.zone === draft.zone).length;
              const regionCount = draft.regions.filter((r) => r.zone === draft.zone).length;
              askDelete(
                next,
                `'${draft.zone}'을 삭제할까요?`,
                `이 구역의 가구 ${furnitureCount}개와 보호 영역 ${regionCount}개도 함께 삭제됩니다.`,
                () => {
                  setSelectedFurniture(next.furniture.find((f) => f.zone === next.zone)?.id ?? null);
                  setSelectedRegion(null);
                  setError("");
                },
              );
            }}
          >
            선택 구역 삭제
          </button>
        </>
      )}
    </div>
  );
  const capturedPhotos = useRef<CapturedFurniture[]>([]);
  // Real measurement when every photo was checked by the measurement server and this phone's
  // camera is set up; otherwise the default-size placement below.
  async function measureCaptured(space: Space, items: CapturedFurniture[]) {
    measuring.current = true;
    setProgress(10);
    setPage("processing");
    try {
      let current = space;
      for (const [i, item] of items.entries()) {
        if (!item.capture?.session) throw new MeasureError("촬영 세션을 다시 시작해 주세요.");
        const camera = item.capture.camera ?? savedCamera();
        if (!camera) throw new MeasureError("카메라 준비를 완료해 주세요.");
        current = await measureAndRegister(current, camera, {
          checkedMeasurement: item.capture.checkedMeasurement,
          session: item.capture.session,
          name: item.name,
          shape: item.capture!.shape,
          vertices: item.capture!.points.map((p) => p.position),
          points: item.capture!.points.map((p) => ({ id: p.id, frames: p.frames! })),
        });
        setProgress(10 + Math.round((90 * (i + 1)) / items.length));
      }
      const markerSession = items[0].capture?.session;
      let markerEndFailed = false;
      if (markerSession) { try { await endCaptureMarker(markerSession); } catch { markerEndFailed = true; } }
      const added = current.furniture.filter((f) => !space.furniture.some((o) => o.id === f.id));
      setSpaces((all) => upsertSpace(all, current));
      setDraft({ ...current, regions: withDefaultRegions(current, added) });
      setSelectedFurniture(added[0]?.id ?? null);
      setSelectedRegion(null);
      setAddingByQr(false);
      setToast(`측정 완료 · 가구 ${added.length}개를 배치했어요. ${markerEndFailed ? "마커 표시 종료에 실패했어요. 촬영 화면에서 종료를 다시 시도해 주세요." : "위치와 크기를 확인해 주세요."}`);
    } catch (e) {
      setError(e instanceof MeasureError ? e.message : "측정하지 못했어요. 다시 촬영해 주세요.");
      setPageState("capture");
      return;
    } finally {
      measuring.current = false;
    }
    setPageState(qrReturnPage.current);
  }
  function finishCapture(items: CapturedFurniture[]) {
    if (!browserCaptureEnabled) return;
    capturedPhotos.current = items;
    if (!draft || !items.length) return;
    const measurable = !!(items[0]?.capture?.camera ?? savedCamera()) && !!draft.serverSceneId &&
      items.every((item) => item.capture?.points.every((p) => p.frames?.length));
    if (measurable) {
      void measureCaptured(draft, items);
      return;
    }
    if (items.some(item => item.capture?.session)) { setError("카메라 준비와 사진 검증을 완료한 뒤 측정해 주세요."); return; }
    const kept = addingByQr
      ? draft.furniture
      : draft.furniture.filter((f) => f.zone !== draft.zone);
    const measured = placeCapturedFurniture(draft, items, kept);
    change({
      furniture: [...kept, ...measured],
      regions: withDefaultRegions(
        { ...draft, regions: addingByQr ? draft.regions : draft.regions.filter((r) => r.zone !== draft.zone) },
        measured,
      ),
      source: "sample",
    });
    setSelectedFurniture(measured[0].id);
    setSelectedRegion(null);
    setAddingByQr(false);
    setProgress(0);
    setPage("processing");
  }
  function addFurniture() {
    setModal("add-furniture");
  }
  function addManualFurniture() {
    setModal(null);
    if (!draft) return;
    const width = Math.min(1, draft.width);
    const depth = Math.min(0.6, draft.depth);
    const height = Math.min(1.8, draft.height);
    const spot = findOpenFurnitureSpot(draft, width, depth, height);
    const f: Furniture = {
      id: uid(),
      name: `가구 ${draft.furniture.length + 1}`,
      zone: draft.zone,
      type: "other",
      x: spot.x,
      y: spot.y,
      width,
      depth,
      height,
    };
    const regions = withDefaultRegions(draft, [f]);
    change({ furniture: [...draft.furniture, f], regions });
    setSelectedFurniture(f.id);
    setSelectedRegion(regions.at(-1)!.id);
  }
  function addRegion() {
    if (!draft) return;
    const r: Region = {
      id: uid(),
      name: `보호 영역 ${draft.regions.filter((r) => !r.furnitureId).length + 1}`,
      zone: draft.zone,
      furnitureId: null,
      x: 0,
      y: Math.max(0, draft.depth / 2 - 0.5),
      width: Math.min(1, draft.width),
      depth: Math.min(1, draft.depth),
      bottom: 0,
      top: Math.min(0.75, draft.height),
      face: "상단",
      grade: "A",
    };
    change({ regions: [...draft.regions, r] });
    setSelectedRegion(r.id);
    setSelectedFurniture(null);
  }
  function confirmReview() {
    if (!draft) return;
    const m = validateSpace(draft);
    if (m || !draft.furniture.length) {
      setError(m ?? "가구를 추가해 주세요.");
      return;
    }
    setPage("protect");
    setTool("furniture");
    const f =
      draft.furniture.find(
        (f) =>
          f.id === selectedFurniture &&
          f.zone === draft.zone,
      ) ??
      draft.furniture.find(
        (f) => f.zone === draft.zone,
      );
    if (!f) {
      setSelectedFurniture(null);
      setSelectedRegion(null);
      return;
    }
    setSelectedFurniture(f.id);
    const r =
      draft.regions.find(
        (r) => r.furnitureId === f.id,
      ) ?? regionForFurniture(f);
    if (!draft.regions.includes(r))
      change({ regions: [...draft.regions, r] });
    setSelectedRegion(r.id);
  }
  // Marks wrong fields under each input, focuses the first and summarises them in the error banner.
  function checkRegistration(form: HTMLFormElement | null, checkValidity: boolean) {
    if (!draft) return false;
    const m = validateSpace(draft);
    const issues = form ? markInvalidFields(form, m ?? undefined, checkValidity) : [];
    if (issues.length) {
      fieldFocus.current = issues[0].input;
      setError(`입력값 ${issues.length}곳을 확인해 주세요: ${issues.map((i) => i.label).join(", ")}`);
      return false;
    }
    if (m) {
      setError(m);
      return false;
    }
    return true;
  }
  // "직접 가구 추가·수정": the window size is optional here, so only the space checks apply.
  function continueManually(form: HTMLFormElement | null) {
    if (checkRegistration(form, false)) setPage("review");
  }
  async function registerSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!draft || saving || !checkRegistration(e.currentTarget, true)) return;
    if (
      !draft.windowDimensions ||
      draft.windowDimensions.width <= 0 ||
      draft.windowDimensions.height <= 0
    ) {
      setError("촬영 전 창문 가로와 높이를 입력해 주세요.");
      return;
    }
    if (isApp && !editorExample) {
      // Persist the selected film before SpatialCapture reads the server's target.
      setSaving(true);
      setError("");
      try {
        const saved = await saveServerSpace(withWindowPosition(draft));
        setDraft(saved);
        setSpaces(current => upsertSpace(current, saved));
        setActiveId(saved.id);
      } catch (error) {
        setError(saveErrorMessage(error, "선택한 필름 정보를 저장하지 못했어요. 다시 시도해 주세요."));
        return;
      } finally {
        setSaving(false);
      }
    }
    qrReturnPage.current = "review";
    setAddingByQr(false);
    setHandoffLink("");
    setPage("capture");
  }
  const plan = space && (
    <Plan
      modeled={page === "review" || page === "protect" || (isApp && page === "detail")}
      space={space}
      selectedFurniture={selectedFurniture}
      selectedRegion={selectedRegion}
      onFurniture={chooseFurniture}
      onRegion={chooseRegion}
      onRegions={
        draft && page === "protect"
          ? (regions) => change({ regions })
          : undefined
      }
      onFurnitureMove={
        draft && (page === "review" || page === "protect")
          ? changeFurniture
          : undefined
      }
      tool={tool}
      hour={hour}
      showDemoSun={!predictionKey}
      exposedRegionIds={predictionKey && !predictionError && currentPrediction?.result
        ? currentPrediction.result.regions.flatMap((region, index) => {
            const target = Date.parse(currentPrediction.result!.start_time) + hour * 3600000;
            const sample = region.timeline.filter(t => Date.parse(t.timestamp) <= target).at(-1);
            // The top of a furniture comes back after the regular areas; it lights its furniture's region.
            const owner = (active ? regionOwners(active, currentPrediction.result!.regions.length)?.[index] : undefined) ?? index;
            return sample?.status === "exposed" && active?.regions[owner] ? [active.regions[owner].id] : [];
          }) : []}
    />
  );
  const navItems: {
    page: Page;
    mobile: string;
    desktop: string;
    icon: string;
  }[] = [
    { page: "home", mobile: "홈", desktop: "내 공간", icon: "⌂" },
    { page: "spaces", mobile: "공간", desktop: "공간 현황", icon: "▦" },
    { page: "guide", mobile: "가이드", desktop: "사용 가이드", icon: "▤" },
    { page: "more", mobile: "더보기", desktop: "더보기", icon: "☰" },
  ];
  const isActive = (p: Page) =>
    page === p ||
    (p === "spaces" &&
      [
        "register",
        "capture",
        "processing",
        "review",
        "protect",
        "detail",
      ].includes(page)) ||
    (p === "more" && page === "account");
  // App view: the screen's one primary action sits in a bar above the bottom nav, within thumb reach.
  const existingDraft = !!draft && spaces.some((s) => s.id === draft.id);
  const actionBar = !isApp || !account
    ? null
    : page === "register" && draft
      ? existingDraft
        ? <button type="button" className="primary" disabled={saving || !!draftWarning} onClick={save}>{saving ? "저장 중…" : "변경사항 저장"}</button>
        : <button type="submit" form="register-form" className="primary">QR 인식으로 계속</button>
      : page === "review" && draft
        ? mobileToken
          ? <button type="button" className="primary" disabled={saving || !!draftWarning} onClick={save}>{saving ? "저장 중…" : "측정 결과 저장"}</button>
          : <button type="button" className="primary" onClick={confirmReview}>확인하고 보호 영역 지정</button>
        : page === "protect" && draft
          ? <button type="button" className="primary" disabled={saving || !!draftWarning} onClick={save}>{saving ? "저장 중…" : "보호 영역 저장"}</button>
          : null;
  const draftWindow = draft && resolveWindow(draft);
  function changeWindow(patch: Partial<NonNullable<Space["windowDimensions"]>>) {
    if (!draft) return;
    const next = { ...(draft.windowDimensions ?? { width: NaN, height: NaN }), ...patch };
    const previous = draft.windowDimensions;
    if (previous?.captureModule && (patch.width !== undefined || patch.height !== undefined)) {
      const selection = previous.captureModule;
      const row = selection.rowOrigin === "bottom"
        ? selection.row
        : Math.floor(previous.height + 1e-6) - 1 - selection.row;
      next.captureModule = row >= 0 && row < Math.floor(next.height + 1e-6)
        && selection.col < Math.floor(next.width + 1e-6)
        ? { row, col: selection.col, rowOrigin: "bottom" }
        : null;
    }
    // Capture sessions own marker placement; discard obsolete manual marker settings.
    const resolved = resolveWindow({ ...draft, windowDimensions: next }) ?? next;
    change({ windowDimensions: { ...resolved, marker: undefined } });
  }
  const draftSite = draft?.site ?? {
    latitude: NaN,
    longitude: NaN,
    timezone: "Asia/Seoul",
    northConfirmed: true,
  };

  return {
    predictionDate,
    predictionKey,
    sunpathResult: predictionError ? undefined : currentPrediction?.result,
    deleteSpace,
    deleting,
    continueManually,
    sunSource,
    predictionPill,
    filmPrediction,
    syncFailed,
    retryUpload,
    initial,
    initialNavigation,
    isApp,
    setIsApp,
    handoffLink,
    setHandoffLink,
    deviceSession,
    setDeviceSession,
    measurementJobs,
    setMeasurementJobs,
    mobileToken,
    setMobileToken,
    measurementReady,
    setMeasurementReady,
    account,
    spaces,
    setSpaces,
    page,
    setPageState,
    activeId,
    setActiveId,
    draft,
    setDraft,
    selectedFurniture,
    setSelectedFurniture,
    selectedRegion,
    setSelectedRegion,
    tool,
    setTool,
    modal,
    setModal,
    error,
    setError,
    editedDraftId,
    setEditedDraftId,
    inputEdited,
    draftWarning,
    savedOverlapWarning,
    setPage,
    toast,
    setToast,
    pendingDelete,
    confirmDelete,
    askDelete,
    guideIndex,
    setGuideIndex,
    pendingPage,
    setPendingPage,
    addingByQr,
    setAddingByQr,
    progress,
    setProgress,
    hour,
    setHour,
    storageError,
    setStorageError,
    saving,
    setSaving,
    serverState,
    setServerState,
    serverError,
    setServerError,
    unavailable,
    setUnavailable,
    conflict,
    setConflict,
    locationStatus,
    setLocationStatus,
    locating,
    setLocating,
    locationAttempts,
    locationRequest,
    receiveMeasurement,
    joinMeasurement,
    locateBuilding,
    authStatus,
    authNotice,
    startLogin,
    signOut,
    modalRef,
    errorRef,
    draftPage,
    takePendingWebAction,
    rememberQrReturn,
    active,
    editing,
    space,
    sunSchedule,
    shadingDraft,
    setShadingDraft,
    shadingEdit,
    setShadingEdit,
    detailShading,
    activeRegionCount,
    furniture,
    region,
    historyReady,
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
    change,
    changeFurniture,
    chooseFurniture,
    chooseRegion,
    patchRegion,
    protectFurniture,
    save,
    renameZone,
    switchZone,
    addZone,
    zoneTabs,
    finishCapture,
    addFurniture,
    addManualFurniture,
    addRegion,
    confirmReview,
    registerSubmit,
    plan,
    navItems,
    isActive,
    existingDraft,
    actionBar,
    draftWindow,
    changeWindow,
    draftSite,
  };
}
