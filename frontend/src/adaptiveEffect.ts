import type { AdaptiveMetrics } from "./spaceApi.ts";

export type EffectBadge = { label: string; value: string; better: boolean };

const pct = (n: number) => `${Math.round(Math.abs(n))}%`;

/** The two badges for a day. A day the baseline never changes has no percentage to show. */
export function effectBadges(m: AdaptiveMetrics): [EffectBadge, EffectBadge] {
  let flicker: EffectBadge;
  if (m.baseline_total_change === 0) {
    flicker = m.adaptive_total_change === 0
      ? { label: "깜빡임", value: "변화 없음", better: false }
      : { label: "깜빡임", value: "기본 계획보다 늘어요", better: false };
  } else if (Math.round(Math.abs(m.total_change_reduction_pct)) === 0) {
    flicker = { label: "깜빡임", value: "변화 없음", better: false };
  } else if (m.total_change_reduction_pct > 0) {
    flicker = { label: "깜빡임", value: `${pct(m.total_change_reduction_pct)} 줄어요`, better: true };
  } else {
    flicker = { label: "깜빡임", value: `${pct(m.total_change_reduction_pct)} 늘어요`, better: false };
  }
  let daylight: EffectBadge;
  if (m.baseline_total_change === 0 && m.adaptive_total_change === 0 || Math.round(Math.abs(m.daylight_gain_pct)) === 0) {
    daylight = { label: "자연 채광", value: "변화 없음", better: false };
  } else if (m.daylight_gain_pct > 0) {
    daylight = { label: "자연 채광", value: `${pct(m.daylight_gain_pct)} 늘어요`, better: true };
  } else {
    // A loss is shown as it is, never rounded up to zero.
    daylight = { label: "자연 채광", value: `${pct(m.daylight_gain_pct)} 줄어요`, better: false };
  }
  return [flicker, daylight];
}
