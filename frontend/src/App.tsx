import NumberField from "./NumberField";
import "./ReferencePreview.css";
import "./WorkspaceDesign.css";
import { DeviceHandoff } from "./DeviceHandoff";
import { createHandoff, connectHandoff, completeHandoff, listMeasurements } from "./spaceApi";
import type { HandoffSession } from "./spaceApi";
import { FilmGrid, ShadingTimeControl } from "./ShadingPlan";
import { applyShadingTime, blockingSchedule, formatMinute } from "./sunlight";
import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { editorExample, exampleState } from "./exampleSpace";
import Plan from "./Plan";
import SpatialCapture from "./SpatialCapture";
import {
  blankSpace,
  defaultShadingTime,
  furnitureTypes,
  grades,
  isWidthWall,
  placeCapturedFurniture,
  readState,
  resolveWindow,
  regionForFurniture,
  removeZone,
  STORAGE_KEY,
  uid,
  hasUnsavedChanges,
  updateFurniture,
  upsertSpace,
  validateSpace,
  validateShadingTime,
  walls,
} from "./model";
import type {
  Account,
  CapturedFurniture,
  Furniture,
  FurnitureType,
  Grade,
  Region,
  ShadingTime,
  Space,
  Wall,
} from "./model";
import "./App.css";
import {
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
} from "./navigation";
import type { BrowserLocation, Page } from "./navigation";
import { AwardHeader, AwardLanding, AwardSpaces, AwardGuide, AwardFooter } from "./AwardSystem";
import { AccountOverview, AuthPanel } from "./AccountPage";
const titles: Record<Page, string> = {
  home: "홈",
  spaces: "내 공간",
  register: "새 공간 등록",
  capture: "QR 측정",
  processing: "공간 구성",
  review: "도면 확인",
  protect: "도면과 보호구역",
  detail: "공간 현황",
  guide: "사용 가이드",
  more: "더보기",
  account: "내 계정 정보",
};

function Icon({ name }: { name: "sun" }) {
  return <img className="icon" src={`/design/${name}.svg`} alt="" />;
}
function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`card ${className}`}>{children}</section>;
}

function App() {
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
          !!initial.account,
          null,
          initial.spaces,
        ),
  );
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
  const [account, setAccount] = useState<Account | null>(initial.account);
  const [spaces, setSpaces] = useState<Space[]>(initial.spaces);
  const [page, setPageState] = useState<Page>(initialNavigation.page);
  const [activeId, setActiveId] = useState<string | null>(
    initialNavigation.activeId ?? initial.spaces[0]?.id ?? null,
  );
  const [draft, setDraft] = useState<Space | null>(initialNavigation.draft);
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
    | null
  >(null);
  const [error, setError] = useState("");
  // Inline warnings start once the user edits this draft; opening another draft starts clean.
  const [editedDraftId, setEditedDraftId] = useState<string | null>(null);
  const inputEdited = !!draft && editedDraftId === draft.id;
  const draftWarning = inputEdited && draft ? validateSpace(draft) : null;
  const setPage = (next: Page) => {
    setError("");
    setPageState(next);
  };
  const [toast, setToast] = useState("");
  // Deletions in the editor are immediate, so the toast offers a one-step undo.
  const [undoDraft, setUndoDraft] = useState<Space | null>(null);
  function deleteWithUndo(next: Space, message: string) {
    if (!draft) return;
    setUndoDraft(draft);
    setDraft(next);
    setToast(message);
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
    if (page !== "spaces" || !account) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function refreshJobs() {
      try { const [jobs, remote] = await Promise.all([listMeasurements(controller.signal), listServerSpaces(controller.signal)]); if (!stopped) { setMeasurementJobs(jobs);setSpaces(current=>mergeServerSpaces(current,remote.spaces));setServerState("online"); } }
      catch (error) { if(!stopped) {setServerState("offline");setServerError(userMessage(error, "측정 상태를 불러오지 못했습니다. 잠시 후 자동으로 다시 확인합니다."));} }
      if (!stopped) timer = setTimeout(refreshJobs, 3000);
    }
    void refreshJobs();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [page, account]);
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
    if (!token) return;
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
  }, []);
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
              northConfirmed: current.site?.northConfirmed ?? false,
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

  const [loginName, setLoginName] = useState("SunSync 사용자");
  const [loginEmail, setLoginEmail] = useState("sun@example.com");
  const modalRef = useRef<HTMLDialogElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const draftPage = useRef<Page>("register");
  const pendingWebAction = useRef<(() => void) | null>(null);
  const active = spaces.find((s) => s.id === activeId) ?? spaces[0];
  const editing = [
    "register",
    "capture",
    "processing",
    "review",
    "protect",
  ].includes(page);
  useEffect(() => {
    if (page !== "detail" || !active?.serverRevision) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const id = active.id;
    async function refreshDetail() {
      try {
        const saved = await getServerSpace(id);
        if (!stopped) setSpaces(current => current.map(old => old.id !== id || old.serverRevision === saved.serverRevision ? old : {...saved, zone: saved.zones.includes(old.zone) ? old.zone : saved.zone}));
      } catch { /* Preserve the visible space during a temporary network failure. */ }
      if (!stopped) timer = setTimeout(refreshDetail, 3000);
    }
    void refreshDetail();
    return () => { stopped = true; clearTimeout(timer); };
  }, [page, active?.id, active?.serverRevision]);
  const space = editing ? draft : active;
  // When sunlight reaches each protected area (simplified demo calculation). Blocking itself only
  // happens inside the time range the user sets.
  const sunSchedule = useMemo(
    () =>
      page === "detail" && active
        ? blockingSchedule(active)
        : page === "protect" && draft
          ? blockingSchedule(draft)
          : new Map(),
    [page, active, draft],
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
        !!account,
        draft,
        spaces,
      );
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
          : (scene?.furniture[0]?.id ?? null),
      );
      setSelectedRegion((id) =>
        scene?.regions.some((r) => r.id === id) ? id : null,
      );
    }
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [account, draft, spaces]);

  useEffect(() => {
    if (editorExample) return;
    const controller = new AbortController();
    listServerSpaces(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setSpaces((current) => mergeServerSpaces(current, result.spaces));
        setUnavailable(result.unavailable);
        setServerState("online");
        setServerError("");
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setServerState("offline");
        setServerError(
          userMessage(error, "서버 연결을 확인해 주세요."),
        );
      });
    return () => controller.abort();
  }, []);
  async function refreshServer() {
    if (editorExample) return;
    setServerState("loading");
    try {
      const result = await listServerSpaces();
      setSpaces((current) => mergeServerSpaces(current, result.spaces));
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
      setSelectedFurniture(saved.furniture[0]?.id ?? null);
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
    if (editorExample) return;
    const id = new URLSearchParams(location.search).get("space");
    if (!id || new URLSearchParams(location.search).has("handoff")) return;
    let cancelled = false;
    getServerSpace(id)
      .then((saved) => {
        if (cancelled) return;
        setSpaces((current) => upsertSpace(current, saved));
        setDraft(saved);
        setActiveId(saved.id);
        setSelectedFurniture(saved.furniture[0]?.id ?? null);
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
  }, [isApp]);
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
          : await saveServerSpace(draft);
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
      setError(userMessage(error, "공간을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    } finally {
      setSaving(false);
    }
  }
  // Storage failures must be surfaced to avoid silently losing saved spaces.
  useEffect(() => {
    if (editorExample) return;
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ version: 1, account, spaces }),
      );
      // eslint-disable-next-line react/set-state-in-effect -- Reflect external storage availability in the UI.
      setStorageError(false);
    } catch {
      // eslint-disable-next-line react/set-state-in-effect -- Reflect external storage availability in the UI.
      setStorageError(true);
    }
  }, [account, spaces]);
  useEffect(() => {
    if (error) {
      errorRef.current?.focus();
      errorRef.current?.scrollIntoView({ block: "center" });
    }
  }, [error]);
  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.title = `${titles[page]} · SunSync`;
    if (["register", "capture", "review", "protect"].includes(page))
      draftPage.current = page;
  }, [page]);
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => {
      setToast("");
      setUndoDraft(null);
    }, undoDraft ? 6000 : 3500);
    return () => clearTimeout(id);
  }, [toast, undoDraft]);
  useEffect(() => {
    const d = modalRef.current;
    if (modal && d && !d.open) {
      d.showModal();
      d.focus();
    } else if (!modal && d?.open) d.close();
  }, [modal]);
  useEffect(() => {
    if (page !== "processing") return;
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

  const unsaved = useMemo(() => hasUnsavedChanges(draft, spaces), [draft, spaces]);
  useEffect(() => {
    if (!unsaved) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [unsaved]);

  function navigate(next: Page) {
    if (next === page) return;
    if (shouldConfirmNavigation(!!draft, page, next)) {
      // An editor left without edits (e.g. reopened with Back after saving) closes quietly.
      if (unsaved) {
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
  function requireAccount(action: () => void) {
    if (account) action();
    else {
      pendingWebAction.current = action;
      setModal("login");
    }
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
    setSaving(true);
    setError("");
    try {
      const next = { ...active, shadingSchedule: shadingEdit, revision: active.revision + 1 };
      const saved = editorExample ? next : await saveServerSpace(next);
      setSpaces((current) => upsertSpace(current, saved));
      setShadingEdit(null);
      setToast(
        shadingEdit.enabled
          ? `차단 시간 ${shadingEdit.start} ~ ${shadingEdit.end} 저장`
          : "차단 시간을 끄고 저장했습니다.",
      );
    } catch (error) {
      setError(userMessage(error, "차단 시간을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    } finally {
      setSaving(false);
    }
  }
  function editSpace(s: Space) {
    setActiveId(s.id);
    setDraft(structuredClone(s));
    setSelectedFurniture(s.furniture[0]?.id ?? null);
    setSelectedRegion(null);
    setPage("protect");
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
  async function save() {
    if (!draft || saving) return false;
    const message = validateSpace(draft);
    if (message) {
      setError(message);
      return false;
    }
    if (page !== "register" && !draft.furniture.length && !draft.regions.length) {
      setError("가구 또는 보호영역을 추가해 주세요.");
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
      const saved = editorExample
        ? { ...draft, savedAt: new Date().toISOString() }
        : await saveServerSpace(draft);
      setSpaces((current) => upsertSpace(current, saved));
      setActiveId(saved.id);
      setDraft(null);
      setPageState("detail");
      setServerState("online");
      setServerError("");
      setToast(
        editorExample
          ? "둘러보기 공간 저장 완료"
          : "공간과 보호구역을 서버에 저장했습니다.",
      );
      return true;
    } catch (error) {
      setError(
        userMessage(error, "서버에 저장하지 못했습니다."),
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
              // Immediate with undo (toast) instead of a confirmation dialog.
              const next = removeZone(draft, draft.zone);
              const furnitureCount = draft.furniture.filter((f) => f.zone === draft.zone).length;
              const regionCount = draft.regions.filter((r) => r.zone === draft.zone).length;
              deleteWithUndo(
                next,
                `${draft.zone} 삭제됨 · 가구 ${furnitureCount}개, 보호영역 ${regionCount}개`,
              );
              setSelectedFurniture(
                next.furniture.find((f) => f.zone === next.zone)?.id ?? null,
              );
              setSelectedRegion(null);
              setError("");
            }}
          >
            선택 구역 삭제
          </button>
        </>
      )}
    </div>
  );
  function finishCapture(items: CapturedFurniture[]) {
    if (!draft || !items.length) return;
    const kept = addingByQr
      ? draft.furniture
      : draft.furniture.filter((f) => f.zone !== draft.zone);
    const measured = placeCapturedFurniture(draft, items, kept);
    change({
      furniture: [...kept, ...measured],
      regions: addingByQr
        ? draft.regions
        : draft.regions.filter((r) => r.zone !== draft.zone),
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
    const f: Furniture = {
      id: uid(),
      name: `가구 ${draft.furniture.length + 1}`,
      zone: draft.zone,
      type: "other",
      x: 0,
      y: 0,
      width: Math.min(1, draft.width),
      depth: Math.min(0.6, draft.depth),
      height: Math.min(1.8, draft.height),
    };
    change({ furniture: [...draft.furniture, f] });
    setSelectedFurniture(f.id);
    setSelectedRegion(null);
  }
  function addRegion() {
    if (!draft) return;
    const r: Region = {
      id: uid(),
      name: `보호영역 ${draft.regions.filter((r) => !r.furnitureId).length + 1}`,
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
  function registerSubmit(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    const m = validateSpace(draft);
    if (m) {
      setError(m);
      return;
    }
    if (
      !draft.windowDimensions ||
      draft.windowDimensions.width <= 0 ||
      draft.windowDimensions.height <= 0
    ) {
      setError("촬영 전 창문 가로와 높이를 입력해 주세요.");
      return;
    }
    qrReturnPage.current = "review";
    setAddingByQr(false);
    setHandoffLink("");
    setPage("capture");
  }
  const plan = space && (
    <Plan
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
        ? <button type="button" className="primary" onClick={confirmReview}>확인하고 보호구역 지정</button>
        : page === "protect" && draft
          ? <button type="button" className="primary" disabled={saving || !!draftWarning} onClick={save}>{saving ? "저장 중…" : "보호구역 저장"}</button>
          : page === "detail" && active
            ? <button type="button" className="primary" onClick={() => editSpace(active)}>가구·도면·보호구역 수정</button>
            : null;
  const draftWindow = draft && resolveWindow(draft);
  function changeWindow(patch: Partial<NonNullable<Space["windowDimensions"]>>) {
    if (!draft) return;
    change({
      windowDimensions: {
        ...(draft.windowDimensions ?? { width: NaN, height: NaN }),
        ...patch,
      },
    });
  }
  const draftSite = draft?.site ?? {
    latitude: NaN,
    longitude: NaN,
    timezone: "Asia/Seoul",
    northConfirmed: false,
  };

  return (
    <>
      <div
        inert={saving || undefined}
        aria-busy={saving}
        className={`app ap award-app ${isApp ? "mode-app" : "mode-web"} page-${page} ${page === "more" ? "more-page" : ""} ${!account ? "auth-page" : ""} ${actionBar || (isApp && page === "capture" && draft) ? "has-action-bar" : ""}`}
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
        {<AwardHeader account={account} page={page} editing={editing} onNavigate={p=>{if(p==="spaces"||p==="account")requireAccount(()=>navigate(p));else navigate(p)}} onLogin={()=>requireAccount(()=>navigate("spaces"))}/>}
        {page === "home" && <AwardLanding onStart={()=>requireAccount(()=>navigate("spaces"))} onGuide={()=>navigate("guide")}/>}
        {page === "guide" && (<AwardGuide index={guideIndex} onIndex={setGuideIndex} onStart={()=>requireAccount(()=>navigate("spaces"))}/>)}
        {!account ? (
          <AuthPanel hidden={page === "home" || page === "guide"} brand={<Icon name="sun" />} onLogin={() => setModal("login")} />
        ) : (
          <>
            <main id="main" className="main" tabIndex={-1} onChangeCapture={() => setEditedDraftId(draft?.id ?? null)}>
              {draftWarning && <p className="live-validation" role="alert">{draftWarning}</p>}
              {editing && space && (
                <div className="space-context desktop-only">
                  <span>내 공간</span>
                  <span aria-hidden="true">/</span>
                  <strong>
                    {space.building || "새 건물"} · {space.name || "새 공간"}
                  </strong>
                  <span className="context-mode">
                    {editing ? "편집 중" : "공간 현황"}
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
                    "가구·보호구역 편집",
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
                  {page === "detail" && active && <p className="page-subtitle">{active.building} · {active.floor} <span>공간 현황</span></p>}
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
              </div>
              )}
              {draft && !editing && page !== "guide" && (
                <button
                  className="secondary resume-edit"
                  onClick={() => setPage(draftPage.current)}
                >
                  편집 중인 공간 이어가기
                </button>
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
              {(page === "spaces") && (
                <div className="list-content">
                  {/* App home stays quiet while the server is fine; the status shows when it needs attention. */}
                  {(!isApp || page === "spaces" || serverState !== "online") && (
                  <div className="server-sync-row">
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
                  {<AwardSpaces spaces={spaces} jobs={measurementJobs} onSelect={selectSpace} onStart={begin} onMeasurement={job=>{const s=spaces.find(s=>s.id===job.space_id);if(isApp && job.status!=="completed")joinMeasurement(job);else if(s){selectSpace(s);if(job.status!=="completed"){setDraft(structuredClone(s));setPage("capture");}}}}/>}
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
                <form id="register-form" onSubmit={registerSubmit} className="registration">
                  <p className="step-label">1 / 5 · 공간 준비</p>
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
                          <div className="two-fields">
                            <NumberField
                              label={
                                isWidthWall(draftWindow.wall)
                                  ? "서쪽 끝에서 창까지"
                                  : "북쪽 끝에서 창까지"
                              }
                              value={draftWindow.offset}
                              onChange={(offset) => changeWindow({ offset })}
                            />
                            <NumberField
                              label="바닥에서 창 아래까지"
                              value={draftWindow.sill}
                              onChange={(sill) => changeWindow({ sill })}
                            />
                          </div>
                          <h3>필름 QR 마커</h3>
                          <div className="dimensions">
                            <NumberField
                              label="마커 크기"
                              value={draftWindow.marker.size}
                              min={0.05}
                              onChange={(size) =>
                                changeWindow({ marker: { ...draftWindow.marker, size } })
                              }
                            />
                            <NumberField
                              label="창 왼쪽에서 중심"
                              value={draftWindow.marker.x}
                              onChange={(x) =>
                                changeWindow({ marker: { ...draftWindow.marker, x } })
                              }
                            />
                            <NumberField
                              label="창 아래에서 중심"
                              value={draftWindow.marker.y}
                              onChange={(y) =>
                                changeWindow({ marker: { ...draftWindow.marker, y } })
                              }
                            />
                          </div>
                          <FilmGrid space={draft} marker />
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
                        onClick={() => {
                          const m = validateSpace(draft);
                          if (m) {
                            setError(m);
                            return;
                          }
                          setPage("review");
                        }}
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
                    <li>앱에서 가구 이름과 종류 선택</li>
                    <li>가구 네 모서리에 차례로 서서 창문 QR 마커 촬영</li>
                    <li>마커가 다 보이지 않거나 흔들리면 앱이 재촬영 안내</li>
                    <li>촬영이 끝나면 웹에서 도면 확인 후 가구·보호구역 편집</li>
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
                      측정 완료 · 도면 확인
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
                    <p className="step-label">2 / 5 · 공간 촬영</p>
                    <h2>
                      {addingByQr
                        ? "추가 가구 모서리 촬영"
                        : "가구 모서리에서 QR 촬영"}
                    </h2>
                    {zoneTabs}
                    {draft.windowDimensions?.width &&
                    draft.windowDimensions?.height ? (
                      <SpatialCapture
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
                      <li>촬영할 가구의 이름과 종류 선택</li>
                      <li>가구 모서리 1~4에 차례로 서서 마커 전체가 보이게 촬영</li>
                      <li>재촬영 안내가 나오면 안내대로 같은 모서리 다시 촬영</li>
                      <li>모든 가구 촬영 후 도면과 가로·깊이·높이 확인</li>
                    </ol>
                    <button
                      className="secondary"
                      onClick={() => navigate("guide")}
                    >
                      촬영 방법 보기
                    </button>
                  </Card>
                </div>
              )}
              {page === "processing" && (
                <Card className="processing">
                  <p className="step-label">3 / 5 · 사진 분석</p>
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
                      <span>도면·보호구역 편집</span>
                    </div>
                    <div className="editor-command-actions">
                      <span className="editor-save-status" role="status">
                        {!unsaved
                          ? "변경사항 없음"
                          : "저장되지 않은 변경사항"}
                      </span>
                      <button
                        className="secondary"
                        onClick={() => navigate("detail")}
                      >
                        편집 종료
                      </button>
                      <button
                        className="primary"
                        disabled={saving || !!draftWarning}
                        onClick={save}
                      >
                        {saving ? "저장 중…" : "저장"}
                      </button>
                    </div>
                  </div>}
                  <p className="editing-help">
                    도면에서 가구를 끌어 옮기거나 치수를 입력해 수정
                    {isApp && " · 넓은 화면의 웹에서 더 편하게 편집할 수 있어요"}
                  </p>
                  <p className="step-label editor-step-label">
                    {page === "review"
                      ? "4 / 5 · 가구 선택·치수 수정"
                      : "5 / 5 · 보호면·높이 확인"}
                  </p>
                  <div className="workspace editor-workspace">
                    <div className="workspace-main">
                      {zoneTabs}
                      <div className="toolbar">
                        {page === "protect" && (
                          <>
                            <button
                              className={`chip ${tool === "furniture" ? "active" : ""}`}
                              onClick={() => setTool("furniture")}
                            >
                              가구 선택
                            </button>
                            <button
                              className={`chip ${tool === "region" ? "active" : ""}`}
                              onClick={() => setTool("region")}
                            >
                              영역 그리기
                            </button>
                          </>
                        )}
                      </div>
                      <Card className="plan-card">{plan}</Card>
                      <div className="item-tabs" aria-label="측정한 가구">
                        {draft.furniture
                          .filter((f) => f.zone === draft.zone)
                          .map((f) => (
                            <button
                              key={f.id}
                              aria-pressed={selectedFurniture === f.id}
                              className={
                                selectedFurniture === f.id ? "active" : ""
                              }
                              onClick={() => chooseFurniture(f.id)}
                            >
                              {f.name}
                              {f.type && <small> · {furnitureTypes[f.type]}</small>}
                            </button>
                          ))}
                        <button className="item-add" onClick={addFurniture}>＋ 가구 추가</button>
                      </div>
                      {page === "protect" && (
                        <Card className="shading-time-card">
                          <ShadingTimeControl
                            space={draft}
                            value={draft.shadingSchedule ?? defaultShadingTime}
                            onChange={(shadingSchedule) => change({ shadingSchedule })}
                            sunSchedule={sunSchedule}
                            hour={hour}
                          />
                        </Card>
                      )}
                      {page === "protect" && (
                        <div className="region-list">
                          <div className="section-row">
                            <h3>보호영역</h3>
                            <button className="text-button" onClick={addRegion}>
                              ＋ 영역 추가
                            </button>
                          </div>
                          {draft.regions.length === 0 && (
                            <p className="muted small">
                              가구 선택 후 보호영역 지정 또는 도면에 영역 그리기
                            </p>
                          )}
                          {draft.regions
                            .filter((r) => r.zone === draft.zone)
                            .map((r) => (
                              <button
                                aria-pressed={r.id === selectedRegion}
                          className={`row ${r.id === selectedRegion ? "selected" : ""}`}
                                key={r.id}
                                onClick={() => chooseRegion(r.id)}
                              >
                                <span>
                                  {r.name}
                                  <small>
                                    {r.face} · {r.bottom}~{r.top} m
                                  </small>
                                </span>
                                <span className="grade-summary">
                                  {r.grade}{isApp ? ": " : " : "}{grades[r.grade]}
                                </span>
                              </button>
                            ))}
                        </div>
                      )}
                    </div>
                    <aside className="inspector">
                      <h2>
                        {region ? "선택한 보호영역 설정" : "선택한 가구 설정"}
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
                              ? "가구 종류별 기본 치수 · 실제 치수로 수정"
                              : "직접 입력"}{" "}
                            · m
                          </p>
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
                                보호영역 지정
                              </button>
                            )}
                            <button
                              className="text-button furniture-delete"
                              onClick={() => {
                                deleteWithUndo(
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
                                  `${furniture.name} 삭제됨`,
                                );
                                setSelectedFurniture(null);
                                setSelectedRegion(null);
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
                            <span>보호영역 이름</span>
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
                          <button
                            className="text-button danger"
                            onClick={() => {
                              deleteWithUndo(
                                {
                                  ...draft,
                                  revision: draft.revision + 1,
                                  regions: draft.regions.filter(
                                    (r) => r.id !== region.id,
                                  ),
                                },
                                `${region.name} 삭제됨`,
                              );
                              setSelectedRegion(null);
                            }}
                          >
                            보호영역 삭제
                          </button>
                        </>
                      )}
                      <div className="actions">
                        {page === "review" ? (
                          <>
                            {!isApp && (
                            <button className="primary" onClick={confirmReview}>
                              확인하고 보호구역 지정
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
                                편집 종료
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
                              보호구역 저장
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
                                편집 종료
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
                <div className="workspace">
                  <div className="workspace-main">
                    {zoneTabs}
                    <Card className="status-card">
                      <span
                        className={`status-dot ${activeRegionCount ? "" : "pending"}`}
                      />
                      <div>
                        <strong>
                          {activeRegionCount
                            ? "보호구역 설정 완료"
                            : "보호구역 설정 전"}
                        </strong>
                        <p className="muted small">
                          {active.floor} · {active.zone} · 보호면{" "}
                          {activeRegionCount}개
                        </p>
                      </div>
                      <span className="pill">시뮬레이션</span>
                    </Card>
                    <Card className="plan-card">{plan}</Card>
                    <div className="item-tabs">
                      {active.furniture
                        .filter((f) => f.zone === active.zone)
                        .map((f) => (
                          <button
                            className={
                              selectedFurniture === f.id ? "active" : ""
                            }
                            key={f.id}
                            onClick={() => chooseFurniture(f.id)}
                          >
                            {f.name}
                          </button>
                        ))}
                    </div>
                    <Card>
                      <div className="section-row">
                        <h3>시간별 햇빛 위치</h3>
                        <strong className="brand-text">
                          {formatMinute(Math.round(hour * 60))}
                        </strong>
                      </div>
                      <input
                        aria-label="햇빛 확인 시각"
                        type="range"
                        min="0"
                        max={1439 / 60}
                        step={1 / 60}
                        value={hour}
                        onChange={(e) => setHour(Number(e.target.value))}
                      />
                      <div className="range-labels">
                        <span>00:00</span>
                        <span>06:00</span>
                        <span>12:00</span>
                        <span>18:00</span>
                        <span>23:59</span>
                      </div>
                    </Card>
                    <Card>
                      <div className="section-row">
                        <h3>필름 칸 상태</h3>
                        <strong className="brand-text">
                          {formatMinute(Math.round(hour * 60))}
                        </strong>
                      </div>
                      {resolveWindow(active) ? (
                        <FilmGrid space={active} schedule={applyShadingTime(sunSchedule, detailShading)} hour={hour} />
                      ) : (
                        <p className="muted small">
                          창문 규격을 입력하면 시각별로 가려질 필름 칸을 볼 수 있습니다.
                        </p>
                      )}
                    </Card>
                  </div>
                  <aside className="inspector detail-info">
                    <h2>{active.name}</h2>
                    <p className="muted">
                      {active.building} · {active.floor}
                    </p>
                    {furniture && (
                      <div className="summary-box">
                        <strong>
                          {furniture.name}
                          {furniture.type && ` · ${furnitureTypes[furniture.type]}`}
                        </strong>
                        <p>
                          {furniture.width} × {furniture.depth} ×{" "}
                          {furniture.height} m
                        </p>
                      </div>
                    )}
                    <h3>설정된 보호영역</h3>
                    {!activeRegionCount && (
                      <p className="muted small">
                        아직 없음 · 아래 수정 버튼으로 보호할 가구를 지정
                      </p>
                    )}
                    {active.regions
                      .filter((r) => r.zone === active.zone && r.grade !== "D")
                      .map((r) => (
                        <button
                          key={r.id}
                          aria-pressed={r.id === selectedRegion}
                          className={`row ${r.id === selectedRegion ? "selected" : ""}`}
                          onClick={() => chooseRegion(r.id)}
                        >
                          <span>
                            {r.name}
                            <small>
                              {r.face} · {r.bottom}~{r.top} m
                            </small>
                          </span>
                          <span className="grade-summary">
                            {r.grade}{isApp ? ": " : " : "}{grades[r.grade]}
                          </span>
                        </button>
                      ))}
                    <div className="divider" />
                    <ShadingTimeControl
                      space={active}
                      value={detailShading}
                      onChange={setShadingEdit}
                      sunSchedule={sunSchedule}
                      hour={hour}
                      onSave={saveShadingTime}
                      dirty={
                        !!shadingEdit &&
                        JSON.stringify(shadingEdit) !==
                          JSON.stringify(active.shadingSchedule ?? defaultShadingTime)
                      }
                      saving={saving}
                    />
                    <div className="detail-actions">
                      {!isApp && (
                        <button
                          className="primary"
                          onClick={() => editSpace(active)}
                        >
                          가구·도면·보호구역 수정
                        </button>
                      )}
                      <button
                        className="secondary"
                        onClick={() => {
                          setDraft(structuredClone(active));
                          setSelectedFurniture(null);
                          setSelectedRegion(null);
                          setPage("register");
                        }}
                      >
                        공간 정보 수정
                      </button>
                      {isApp && (
                        <button
                          className="secondary"
                          onClick={() => { window.open(`${location.origin}/?mode=web#/detail/${active.id}`, "_blank", "noopener"); }}
                        >
                          웹 페이지 이동
                        </button>
                      )}
                    </div>
                  </aside>
                </div>
              )}
              {(page === "account" || page === "more") && (
                <AccountOverview
                  account={account}
                  spaces={spaces}
                  serverState={serverState}
                  onOpen={selectSpace}
                  onEdit={editSpace}
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
          className={modal === "login" ? "acct-login-dialog" : undefined}
          tabIndex={-1}
          onCancel={dismissModal}
          onClick={(e) => {
            if (e.target === e.currentTarget) dismissModal();
          }}
        >
          {modal === "login" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setAccount({
                  name: loginName.trim(),
                  email: loginEmail.trim(),
                });
                setPage("home");
                setModal(null);
                const pending = pendingWebAction.current;
                pendingWebAction.current = null;
                pending?.();
              }}
            >
              <aside className="acct-login-visual" aria-hidden="true">
                <div className="acct-auth-glow" />
                <div className="acct-wordmark">SunSync</div>
                <p>
                  햇빛은 들이고,
                  <br />
                  <mark className="ap-mark">책은 지킨다.</mark>
                </p>
                <span className="ap-state-chip on">보호 중</span>
              </aside>
              <h2>체험 계정으로 계속</h2>
              <p className="muted small">Google 로그인은 준비 중이라 이름과 이메일로 체험합니다.</p>
              <label className="field">
                <span>이름</span>
                <input
                  autoFocus
                  required
                  name="name"
                  autoComplete="name"
                  value={loginName}
                  onChange={(e) => setLoginName(e.target.value)}
                />
              </label>
              <label className="field">
                <span>이메일</span>
                <input
                  required
                  type="email"
                  name="email"
                  autoComplete="email"
                  spellCheck={false}
                  value={loginEmail}
                  onChange={(e) => setLoginEmail(e.target.value)}
                />
              </label>
              <button className="primary" type="submit">
                계속
              </button>
              <button
                className="secondary"
                type="button"
                onClick={dismissModal}
              >
                취소
              </button>
            </form>
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
                onClick={() => {
                  setAccount(null);
                  setDraft(null);
                  setPage("home");
                  setModal(null);
                }}
              >
                로그아웃
              </button>
              <button className="secondary" onClick={() => setModal(null)}>
                취소
              </button>
            </>
          )}
          {modal === "add-furniture" && (
            <>
              <h2>가구 추가</h2>
              <button className="primary" onClick={addManualFurniture}>
                직접 추가 · 위치·치수 입력
              </button>
              <button
                className="secondary"
                onClick={() => {
                  qrReturnPage.current = page;
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
          )}
          {modal === "discard" && (
            <>
              <h2>변경 내용을 저장하시겠습니까?</h2>
              <p className="muted">
                저장하지 않고 이동하면 수정 내용이 사라집니다.
              </p>
              <button
                className="primary"
                disabled={saving}
                onClick={async () => {
                  setModal(null);
                  if (await save()) {
                    if (pendingPage === "guide") setGuideIndex(null);
                    setPage(pendingPage);
                  }
                }}
              >
                저장 후 이동
              </button>
              <button
                className="secondary"
                onClick={() => {
                  setDraft(null);
                  if (pendingPage === "guide") setGuideIndex(null);
                  setPage(pendingPage);
                  setModal(null);
                }}
              >
                저장하지 않고 이동
              </button>
              <button className="secondary" onClick={() => setModal(null)}>
                계속 편집
              </button>
            </>
          )}
        </dialog>
        {toast && (
          <div className="toast" role="status">
            {toast}
            {undoDraft && draft && (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  setDraft(undoDraft);
                  setUndoDraft(null);
                  setToast("");
                }}
              >
                되돌리기
              </button>
            )}
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
