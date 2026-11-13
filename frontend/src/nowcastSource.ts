import type { NowcastComparisonResponse } from "./spaceApi.ts";

export type NowcastSourceNote = { tone: "info" | "warning"; text: string };

/** "HH:mm" in Seoul, or null for a value that is not a time (formatting would throw). */
const clock = (iso: string): string | null => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
};

/**
 * One line that says where the forecast chart's numbers come from, taken only from what the
 * server stored: the source of each point, whether it fell back, and the rollout mode.
 */
export function nowcastSourceNote(data: NowcastComparisonResponse): NowcastSourceNote | null {
  const points = data.points ?? [];
  if (points.length === 0) return null;
  if (points.every((p) => p.prediction_source === "unavailable")) {
    return { tone: "warning", text: "예측 값을 받지 못했어요. 잠시 뒤 다시 확인해 주세요." };
  }
  const ai = points.filter((p) => p.prediction_source === "ai_model");
  if (ai.length > 0) {
    const observed = ai.map((p) => p.kma_observed_at).find((value) => value);
    const time = observed ? clock(observed) : null;
    return {
      tone: "info",
      text: time
        ? `기상청 관측을 쓴 AI 예측이에요 (관측 ${time})`
        : "기상청 관측을 쓴 AI 예측이에요",
    };
  }
  const fallbacks = points.filter(
    (p) => p.fallback_used || p.prediction_source === "open_meteo_fallback",
  );
  if (fallbacks.length > 0) {
    // At night the AI is skipped because the sun is low, not because the observation is missing.
    if (fallbacks.every((p) => p.fallback_reason === "low_solar_elevation")) {
      return { tone: "info", text: "해가 낮아 AI 예측 대신 일반 날씨 예보를 써요" };
    }
    return {
      tone: "warning",
      text: "기상청 관측을 쓰지 못해 일반 날씨 예보로 대체했어요",
    };
  }
  if (data.rollout_mode === "shadow") {
    return {
      tone: "info",
      text: "AI 예측은 시험 운영 중이라 비교만 하고, 화면의 값은 일반 날씨 예보 기준이에요",
    };
  }
  return { tone: "info", text: "일반 날씨 예보 기준이에요" };
}
