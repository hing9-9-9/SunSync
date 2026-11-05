import type { Space } from "./model.ts";

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
async function request<T>(url: string, init?: RequestInit, timeoutMs = 15000): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      signal: init?.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(timeoutMs)])
        : AbortSignal.timeout(timeoutMs),
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    throw new SpaceApiError(
      0,
      "저장 서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요.",
    );
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
 * Server spaces first, then every local space the server does not have. A space the server
 * has lost keeps its content and drops its server stamps, so it is uploaded again as new.
 */
export function mergeServerSpaces(
  current: Space[],
  remote: Space[],
  unavailable: UnavailableScene[] = [],
  deleted: string[] = [],
) {
  // Spaces deleted on any device are dropped here instead of being uploaded again.
  const ids = new Set([...remote.map((s) => s.id), ...unavailable.map((s) => s.scene_id), ...deleted]);
  return [
    ...remote,
    ...current
      .filter((s) => !ids.has(s.id))
      .map(({ serverRevision: _r, serverSceneId: _s, ...s }) => s),
  ];
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
export function predictSunpath(sceneId: string, revision: number, startTime: string, durationMinutes = 1440, intervalSeconds = 1800) {
  return request<SunpathPrediction>(
    `/api/scenes/${encodeURIComponent(sceneId)}/sunpath-predictions`,
    {
      method: "POST",
      body: JSON.stringify({
        start_time: startTime,
        duration_minutes: durationMinutes,
        interval_seconds: intervalSeconds,
        expected_scene_revision: revision,
      }),
    },
    60000,
  );
}
export type AdaptivePlan = {
  scene_id: string;
  scene_revision: number;
  stabilized_plan: import("./shading.ts").ShadingPlan;
  metrics: { flicker_reduction_pct: number; protection_violation_count: number };
};
/** FR-04: the FR-02 plan with the cell-edge margin (FR-04.4) and flicker rules, for a two-state film. */
export function predictAdaptivePlan(sceneId: string, startTime: string, durationMinutes: number) {
  return request<AdaptivePlan>(
    `/api/scenes/${encodeURIComponent(sceneId)}/adaptive-shading-plan`,
    { method: "POST", body: JSON.stringify({
      start_time: startTime, duration_minutes: durationMinutes, interval_seconds: 300,
      hardware_mode: "PDLC", enable_edge_margin: true, auto_tune_weights: false,
    }) },
    // A half day at 5-minute steps takes the server about 20 s.
    120000,
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
  timestamp: string;
  solar_elevation_deg: number;
  clear_sky_dni_W_m2: number;
  open_meteo_dni_W_m2: number | null;
  kma_ai_dni_W_m2: number | null;
  dni_p10_W_m2: number;
  dni_p50_W_m2: number;
  dni_p90_W_m2: number;
  clear_sky_index_p50: number;
  uncertainty_spread_W_m2: number;
  selected_dni_W_m2: number;
  selected_source: string;
  fallback_used: boolean;
};

export type ComparisonMetrics = {
  sample_count: number;
  mean_uncertainty_spread_W_m2: number;
  cloud_volatility_index: number;
  kma_vs_open_meteo_mae_W_m2: number | null;
  kma_ai_rmse_W_m2?: number | null;
  open_meteo_rmse_W_m2?: number | null;
  kma_ai_pinball_loss?: number | null;
};

export type NowcastComparisonResponse = {
  run_id: string;
  scene_id: string;
  mode: "kma_ai_ensemble" | "open_meteo_only" | "shadow_compare";
  model_version: string;
  start_time: string;
  end_time: string;
  interval_seconds: number;
  points: QuantileNowcastPoint[];
  metrics: ComparisonMetrics;
};

export function fetchNowcastComparison(sceneId: string, startTime: string) {
  return request<NowcastComparisonResponse>(
    `/api/scenes/${encodeURIComponent(sceneId)}/nowcast-comparison?start_time=${encodeURIComponent(startTime)}&duration_minutes=720&interval_seconds=900`,
  );
}

export function buildDemoNowcastComparison(startTime: string): NowcastComparisonResponse {
  const start = new Date(startTime);
  const baseTime = Number.isNaN(start.getTime()) ? new Date("2026-10-14T02:00:00Z") : start;
  const points: QuantileNowcastPoint[] = [];
  const totalSteps = 24;
  const intervalSeconds = 1800; // 30 min

  let sumSpread = 0;
  let sumDiff = 0;
  let countSun = 0;

  for (let i = 0; i <= totalSteps; i++) {
    const ptDate = new Date(baseTime.getTime() + i * intervalSeconds * 1000);
    const iso = ptDate.toISOString();
    // Daylight fraction: bell curve for daytime (midday peak)
    const sinProgress = Math.sin((Math.PI * i) / totalSteps);
    const elevation = Math.max(0, Number((62 * sinProgress).toFixed(1)));

    if (elevation <= 0) {
      points.push({
        timestamp: iso,
        solar_elevation_deg: 0,
        clear_sky_dni_W_m2: 0,
        open_meteo_dni_W_m2: 0,
        kma_ai_dni_W_m2: 0,
        dni_p10_W_m2: 0,
        dni_p50_W_m2: 0,
        dni_p90_W_m2: 0,
        clear_sky_index_p50: 0,
        uncertainty_spread_W_m2: 0,
        selected_dni_W_m2: 0,
        selected_source: "pvlib_clear_sky",
        fallback_used: false,
      });
      continue;
    }

    const clearDni = Math.round(890 * Math.pow(sinProgress, 0.75));
    // Simulated cloud variability and Open-Meteo synoptic forecast
    const cloudWave = 0.72 + 0.18 * Math.cos(i * 0.9);
    const omDni = Math.round(clearDni * 0.78);
    const kmaP50 = Math.min(clearDni, Math.round(clearDni * cloudWave));

    const p10 = Math.max(0, Math.round(kmaP50 - (90 + 35 * Math.sin(i * 1.2))));
    const p90 = Math.min(Math.round(clearDni * 1.02), Math.max(kmaP50, Math.round(kmaP50 + (110 + 40 * Math.sin(i * 1.2)))));
    const spread = p90 - p10;

    sumSpread += spread;
    sumDiff += Math.abs(kmaP50 - omDni);
    countSun++;

    points.push({
      timestamp: iso,
      solar_elevation_deg: elevation,
      clear_sky_dni_W_m2: clearDni,
      open_meteo_dni_W_m2: omDni,
      kma_ai_dni_W_m2: kmaP50,
      dni_p10_W_m2: p10,
      dni_p50_W_m2: kmaP50,
      dni_p90_W_m2: p90,
      clear_sky_index_p50: clearDni > 0 ? Number((kmaP50 / clearDni).toFixed(3)) : 0,
      uncertainty_spread_W_m2: spread,
      selected_dni_W_m2: kmaP50,
      selected_source: "kma_ai_ensemble",
      fallback_used: false,
    });
  }

  const meanSpread = countSun > 0 ? Number((sumSpread / countSun).toFixed(1)) : 0;
  const mae = countSun > 0 ? Number((sumDiff / countSun).toFixed(1)) : 0;

  return {
    run_id: "demo-hybrid-run",
    scene_id: "demo-scene",
    mode: "kma_ai_ensemble",
    model_version: "kma_openmeteo_quantile_nowcast_v1",
    start_time: baseTime.toISOString(),
    end_time: new Date(baseTime.getTime() + totalSteps * intervalSeconds * 1000).toISOString(),
    interval_seconds: intervalSeconds,
    points,
    metrics: {
      sample_count: points.length,
      mean_uncertainty_spread_W_m2: meanSpread,
      cloud_volatility_index: 0.285,
      kma_vs_open_meteo_mae_W_m2: mae,
      kma_ai_rmse_W_m2: 42.6,
      open_meteo_rmse_W_m2: 89.4,
      kma_ai_pinball_loss: 23.8,
    },
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
    const x = computeX(i, points.length, width, margin).toFixed(1);
    const yTop = computeY(points[i].dni_p90_W_m2, maxDni, height, margin).toFixed(1);
    const yBottom = computeY(points[i].dni_p10_W_m2, maxDni, height, margin).toFixed(1);
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
