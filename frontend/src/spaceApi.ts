import type { Space } from "./model.ts";
import { EXPIRED_MESSAGE, notifyUnauthorized } from "./auth.ts";

export type UnavailableScene = {
  scene_id: string;
  name?: string;
  reason: string;
};
type SpacePage = {
  spaces: Space[];
  unavailable: UnavailableScene[];
  deleted?: string[];
  has_more: boolean;
};
export class SpaceApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "SpaceApiError";
    this.status = status;
  }
}
const fieldLabels: Record<string, string> = {
  name: "공간 이름",
  building: "건물 이름",
  floor: "층 / 위치",
  width: "가로",
  depth: "세로",
  height: "높이",
  latitude: "위도",
  longitude: "경도",
  timezone: "시간대",
  windowDimensions: "창문 규격",
  furniture: "가구",
  regions: "보호 영역",
};
function invalidFieldsMessage(detail: unknown) {
  const fields = Array.isArray(detail)
    ? [
        ...new Set(
          detail.flatMap((e: { loc?: (string | number)[] }) =>
            (e.loc ?? []).map((part) => fieldLabels[String(part)]).filter(Boolean),
          ),
        ),
      ]
    : [];
  return fields.length
    ? `입력값을 확인해 주세요: ${fields.join(", ")}`
    : "입력값 중 허용 범위를 벗어난 항목이 있습니다. 숫자와 필수 항목을 확인해 주세요.";
}
/** Text safe to show people: our own Korean messages, never raw runtime errors. */
export function userMessage(error: unknown, fallback: string) {
  if (error instanceof SpaceApiError) return error.message;
  console.warn("SunSync", error);
  return fallback;
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      signal: init?.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(15000)])
        : AbortSignal.timeout(15000),
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    throw new SpaceApiError(
      0,
      "저장 서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요.",
    );
  }
  if (response.status === 401) {
    // Never an empty listing: callers keep what they have and the app shows the login screen.
    notifyUnauthorized();
    throw new SpaceApiError(401, EXPIRED_MESSAGE);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    // Server text is shown only when it is already written for people (Korean);
    // framework messages stay in the console.
    if (detail) console.warn("SunSync API", response.status, detail);
    const message =
      typeof detail === "string" && /[가-힣]/.test(detail)
        ? detail
        : response.status === 409
          ? "다른 곳에서 수정된 공간입니다. 최신 내용을 불러온 뒤 다시 수정해 주세요."
          : response.status === 404
            ? "요청한 공간을 찾을 수 없습니다. 목록을 새로고침해 주세요."
            : response.status === 422
              ? invalidFieldsMessage(detail)
              : response.status >= 500
                ? "서버에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요."
                : "공간 정보를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.";
    throw new SpaceApiError(response.status, message);
  }
  return data as T;
}
export async function listServerSpaces(signal?: AbortSignal) {
  const spaces: Space[] = [],
    unavailable: UnavailableScene[] = [],
    deleted: string[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = await request<SpacePage>(
      `/api/ui/spaces?offset=${offset}&limit=100`,
      { signal },
    );
    if (!Array.isArray(page?.spaces) || !Array.isArray(page?.unavailable))
      throw new SpaceApiError(0, "서버의 공간 응답 형식을 확인해 주세요.");
    spaces.push(...page.spaces);
    unavailable.push(...page.unavailable);
    deleted.push(...(page.deleted ?? []));
    if (!page.has_more) break;
  }
  return { spaces, unavailable, deleted };
}
export function saveServerSpace(space: Space) {
  return request<Space>(
    space.serverRevision
      ? `/api/ui/spaces/${encodeURIComponent(space.id)}`
      : "/api/ui/spaces",
    {
      method: space.serverRevision ? "PUT" : "POST",
      body: JSON.stringify(space),
    },
  );
}
export function deleteServerSpace(id: string) {
  return request<null>(`/api/ui/spaces/${encodeURIComponent(id)}`, { method: "DELETE" });
}
export function getServerSpace(id: string) {
  return request<Space>(`/api/ui/spaces/${encodeURIComponent(id)}`);
}
/**
 * A successful server listing is authoritative. Browser caches must neither add
 * missing spaces to this list nor turn removed spaces into new uploads.
 * Unsaved editor content is kept separately in drafts.
 */
export function mergeServerSpaces(
  _current: Space[],
  remote: Space[],
  _unavailable: UnavailableScene[] = [],
  _deleted: string[] = [],
) {
  return [...remote];
}

export type HandoffSession = {
  token: string;
  code?: string;
  space_id: string;
  status: "waiting" | "connected" | "completed";
  expires_at?: string;
  space?: Space;
};
export const createHandoff = (space_id: string) =>
  request<HandoffSession>("/api/ui/measurements", {
    method: "POST",
    body: JSON.stringify({ space_id }),
  });
export const readHandoff = (token: string, signal?: AbortSignal) =>
  request<HandoffSession>(`/api/ui/measurements/${encodeURIComponent(token)}`, {
    signal,
  });
export const connectHandoff = (token: string) =>
  request<HandoffSession>(
    `/api/ui/measurements/${encodeURIComponent(token)}/connect`,
    { method: "POST" },
  );
export const completeHandoff = (token: string, space: Space) =>
  request<Space>(`/api/ui/measurements/${encodeURIComponent(token)}/complete`, {
    method: "POST",
    body: JSON.stringify(space),
  });

export const listMeasurements = (signal?: AbortSignal) => request<HandoffSession[]>("/api/ui/measurements", {signal});

type SunpathTimeline = {
  timestamp: string; status: "exposed" | "not_exposed";
  exposed_sample_count?: number;
  samples?: { direct_irradiance_W_m2: number | null }[];
};
export type SunpathPrediction = {
  scene_revision: number;
  start_time: string;
  interval_seconds?: number;
  irradiance?: { timestamp: string; source: string; final_dni_W_m2: number | null; fetched_at?: string | null; fallback_used: boolean }[];
  solar_positions?: { timestamp: string; sun_direction_enu: [number, number, number] }[];
  regions: { region_id: string; timeline: SunpathTimeline[] }[];
};
/** FR-01: direct-sun exposure of every protected region over one day, from the saved Scene. */
export function predictSunpath(sceneId: string, revision: number, startTime: string) {
  return request<SunpathPrediction>(
    `/api/scenes/${encodeURIComponent(sceneId)}/sunpath-predictions`,
    {
      method: "POST",
      body: JSON.stringify({
        start_time: startTime,
        duration_minutes: 1440,
        interval_seconds: 1800,
        expected_scene_revision: revision,
      }),
    },
  );
}

/** FR02 uses the same date, resolution and saved Scene revision as the exposure view. */
export function predictShadingPlan(sceneId: string, revision: number, startTime: string) {
  return request<import("./shading.ts").ShadingPlan>(
    `/api/scenes/${encodeURIComponent(sceneId)}/shading-plans`,
    { method: "POST", body: JSON.stringify({
      start_time: startTime, duration_minutes: 1440, interval_seconds: 1800,
      expected_scene_revision: revision,
    }) },
  );
}

export type QuantileNowcastPoint = {
  horizon_minutes: 10 | 30 | 60;
  valid_time: string;
  prediction_source: "ai_model" | "open_meteo_default" | "open_meteo_fallback" | "unavailable";
  dni_W_m2: number | null;
  p10_dni_W_m2: number | null;
  p50_dni_W_m2: number | null;
  p90_dni_W_m2: number | null;
  open_meteo_dni_W_m2: number | null;
  fallback_used: boolean;
  fallback_reason: string | null;
  quality_flags: string[];
  kma_snapshot_id: string | null;
  kma_observed_at: string | null;
  open_meteo_snapshot_id: string | null;
  open_meteo_fetched_at: string | null;
};

export type NowcastComparisonResponse = {
  run_id: string;
  scene_id: string;
  prediction_origin: string;
  created_at: string;
  model_version: string;
  model_manifest_sha256: string;
  rollout_mode: "shadow" | "active";
  status: "ready" | "degraded";
  result_hash: string;
  points: QuantileNowcastPoint[];
};

export function fetchNowcastComparison(sceneId: string) {
  return request<NowcastComparisonResponse>(
    `/api/scenes/${encodeURIComponent(sceneId)}/ai-nowcast/latest`,
  );
}

/** Test/Storybook fixture only. Production never falls back to these values. */
export function buildDemoNowcastComparison(startTime: string): NowcastComparisonResponse {
  const start = new Date(startTime);
  const baseTime = Number.isNaN(start.getTime()) ? new Date("2026-10-14T02:00:00Z") : start;
  const points: QuantileNowcastPoint[] = ([10, 30, 60] as const).map((horizon) => ({
    horizon_minutes: horizon,
    valid_time: new Date(baseTime.getTime() + horizon * 60_000).toISOString(),
    prediction_source: horizon === 60 ? "open_meteo_default" : "ai_model",
    dni_W_m2: horizon === 10 ? 520 : horizon === 30 ? 470 : 410,
    p10_dni_W_m2: horizon === 60 ? null : horizon === 10 ? 390 : 310,
    p50_dni_W_m2: horizon === 60 ? null : horizon === 10 ? 520 : 470,
    p90_dni_W_m2: horizon === 60 ? null : horizon === 10 ? 650 : 640,
    open_meteo_dni_W_m2: horizon === 10 ? 480 : horizon === 30 ? 450 : 410,
    fallback_used: false,
    fallback_reason: null,
    quality_flags: [],
    kma_snapshot_id: "demo-kma",
    kma_observed_at: baseTime.toISOString(),
    open_meteo_snapshot_id: "demo-open-meteo",
    open_meteo_fetched_at: baseTime.toISOString(),
  }));
  return {
    run_id: "demo-operational-run",
    scene_id: "demo-scene",
    prediction_origin: baseTime.toISOString(),
    created_at: baseTime.toISOString(),
    model_version: "fr01_kma_quantile_v1",
    model_manifest_sha256: "demo",
    rollout_mode: "active",
    status: "ready",
    result_hash: "demo",
    points,
  };
}

export interface ChartMargin {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export const DEFAULT_CHART_MARGIN: ChartMargin = {
  top: 24,
  right: 20,
  bottom: 34,
  left: 48,
};

export function computeY(val: number, maxVal: number, height: number, margin: ChartMargin = DEFAULT_CHART_MARGIN): number {
  const plotH = height - margin.top - margin.bottom;
  const safeMax = maxVal > 0 ? maxVal : 1000;
  const clamped = Math.max(0, Math.min(val, safeMax));
  return margin.top + (1.0 - clamped / safeMax) * plotH;
}

export function computeX(index: number, total: number, width: number, margin: ChartMargin = DEFAULT_CHART_MARGIN): number {
  const plotW = width - margin.left - margin.right;
  if (total <= 1) return margin.left;
  return margin.left + (index / (total - 1)) * plotW;
}

export function buildQuantileBandPolygon(
  points: QuantileNowcastPoint[],
  width: number,
  height: number,
  maxDni: number,
  margin: ChartMargin = DEFAULT_CHART_MARGIN,
): string {
  if (points.length < 2) return "";
  const topCoords: string[] = [];
  const bottomCoords: string[] = [];

  for (let i = 0; i < points.length; i++) {
    const p90 = points[i].p90_dni_W_m2;
    const p10 = points[i].p10_dni_W_m2;
    if (p90 === null || p10 === null) continue;
    const x = computeX(i, points.length, width, margin).toFixed(1);
    const yTop = computeY(p90, maxDni, height, margin).toFixed(1);
    const yBottom = computeY(p10, maxDni, height, margin).toFixed(1);
    topCoords.push(`${x},${yTop}`);
    bottomCoords.unshift(`${x},${yBottom}`);
  }

  return [...topCoords, ...bottomCoords].join(" ");
}

export function buildPolylinePoints(
  values: (number | null)[],
  width: number,
  height: number,
  maxDni: number,
  margin: ChartMargin = DEFAULT_CHART_MARGIN,
): string {
  if (values.length < 2) return "";
  const coords: string[] = [];
  for (let i = 0; i < values.length; i++) {
    const val = values[i];
    if (val === null) continue;
    const x = computeX(i, values.length, width, margin).toFixed(1);
    const y = computeY(val, maxDni, height, margin).toFixed(1);
    coords.push(`${x},${y}`);
  }
  return coords.join(" ");
}

export function formatTimeLabel(iso: string, timezone?: string): string {
  try {
    const date = new Date(iso);
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: timezone || "Asia/Seoul",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return iso.slice(11, 16);
  }
}
