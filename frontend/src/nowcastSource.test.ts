import assert from "node:assert/strict";
import test from "node:test";
import { nowcastSourceNote } from "./nowcastSource.ts";
import type { NowcastComparisonResponse, QuantileNowcastPoint } from "./spaceApi.ts";

function point(over: Partial<QuantileNowcastPoint>): QuantileNowcastPoint {
  return {
    horizon_minutes: 10,
    valid_time: "2026-11-13T05:10:00Z",
    prediction_source: "open_meteo_default",
    dni_W_m2: 400,
    p10_dni_W_m2: null,
    p50_dni_W_m2: null,
    p90_dni_W_m2: null,
    open_meteo_dni_W_m2: 400,
    fallback_used: false,
    fallback_reason: null,
    quality_flags: [],
    kma_snapshot_id: null,
    kma_observed_at: null,
    open_meteo_snapshot_id: "om",
    open_meteo_fetched_at: "2026-11-13T05:00:00Z",
    ...over,
  };
}

function data(
  points: QuantileNowcastPoint[],
  mode: "shadow" | "active" = "active",
): NowcastComparisonResponse {
  return {
    run_id: "r",
    scene_id: "s",
    prediction_origin: "2026-11-13T05:00:00Z",
    created_at: "2026-11-13T05:00:00Z",
    model_version: "v",
    model_manifest_sha256: "x",
    rollout_mode: mode,
    status: "ready",
    result_hash: "h",
    points,
  };
}

test("a point from the AI model says the KMA observation time", () => {
  const note = nowcastSourceNote(
    data([
      point({ prediction_source: "ai_model", kma_observed_at: "2026-11-13T05:00:00Z" }),
      point({ horizon_minutes: 60 }),
    ]),
  );
  assert.equal(note?.tone, "info");
  assert.match(note?.text ?? "", /기상청 관측을 쓴 AI 예측/);
  assert.match(note?.text ?? "", /14:00/); // 05:00 UTC is 14:00 in Seoul
});

test("a fallback tells that the KMA observation was not received", () => {
  const note = nowcastSourceNote(
    data([point({ prediction_source: "open_meteo_fallback", fallback_used: true })]),
  );
  assert.equal(note?.tone, "warning");
  assert.match(note?.text ?? "", /대체/);
});

test("shadow mode says the AI result is only compared", () => {
  const note = nowcastSourceNote(data([point({})], "shadow"));
  assert.equal(note?.tone, "info");
  assert.match(note?.text ?? "", /시험 운영/);
});

test("active mode without AI points says the forecast is the general one", () => {
  assert.equal(nowcastSourceNote(data([point({})]))?.text, "일반 날씨 예보 기준이에요");
});

test("no usable value is a warning and no points is no note", () => {
  const none = nowcastSourceNote(
    data([point({ prediction_source: "unavailable", dni_W_m2: null })]),
  );
  assert.equal(none?.tone, "warning");
  assert.equal(nowcastSourceNote(data([])), null);
});
