import test from "node:test";
import assert from "node:assert/strict";
import { effectBadges } from "./adaptiveEffect.ts";

const m = (o: Partial<Parameters<typeof effectBadges>[0]> = {}) => ({
  baseline_total_change: 10, adaptive_total_change: 6, total_change_reduction_pct: 40, daylight_gain_pct: 5.2, ...o,
});

test("flicker and daylight badges read in plain words", () => {
  const [f, d] = effectBadges(m());
  assert.deepEqual([f.value, f.better], ["40% 줄어요", true]);
  assert.deepEqual([d.value, d.better], ["5% 늘어요", true]);
});

test("a daylight loss is shown, not rounded to zero", () => {
  const [, d] = effectBadges(m({ daylight_gain_pct: -3.4 }));
  assert.deepEqual([d.value, d.better], ["3% 줄어요", false]);
});

test("a day the baseline never changes says there is no change", () => {
  const [f, d] = effectBadges(m({ baseline_total_change: 0, adaptive_total_change: 0, total_change_reduction_pct: 0, daylight_gain_pct: 0 }));
  assert.equal(f.value, "변화 없음");
  assert.equal(d.value, "변화 없음");
  const [more] = effectBadges(m({ baseline_total_change: 0, adaptive_total_change: 2, total_change_reduction_pct: 0 }));
  assert.equal(more.value, "기본 계획보다 늘어요");
});
