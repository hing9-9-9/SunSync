import type { NowcastComparisonResponse } from "./spaceApi.ts";

export type NowcastSourceNote = { tone: "info" | "warning"; text: string };

const clock = (iso: string) =>
  new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));

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
    return {
      tone: "info",
      text: observed
        ? `기상청 관측을 쓴 AI 예측이에요 (관측 ${clock(observed)})`
        : "기상청 관측을 쓴 AI 예측이에요",
    };
  }
  if (points.some((p) => p.fallback_used || p.prediction_source === "open_meteo_fallback")) {
    return {
      tone: "warning",
      text: "기상청 관측을 받지 못해 일반 날씨 예보로 대체했어요",
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
