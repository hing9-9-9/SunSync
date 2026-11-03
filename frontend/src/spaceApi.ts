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
  samples?: { direct_irradiance_W_m2: number | null }[];
};
export type SunpathPrediction = {
  scene_revision: number;
  start_time: string;
  interval_seconds?: number;
  irradiance?: { timestamp: string; source: string; final_dni_W_m2: number | null; fetched_at?: string | null; fallback_used: boolean }[];
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
