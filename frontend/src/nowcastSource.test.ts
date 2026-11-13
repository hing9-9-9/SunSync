import assert from "node:assert/strict";
import test from "node:test";
import { nowcastSourceNote } from "./nowcastSource.ts";
import type {
  NowcastComparisonResponse,
  QuantileNowcastPoint,
} from "./spaceApi.ts";

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

test("normal operation says which values are AI and which are the general forecast", () => {
  const note = nowcastSourceNote(
    data(
      [
        point({
          horizon_minutes: 10,
          prediction_source: "ai_model",
          kma_observed_at: "2026-11-13T05:00:00Z",
        }),
        point({
          horizon_minutes: 30,
          prediction_source: "ai_model",
          kma_observed_at: "2026-11-13T05:00:00Z",
        }),
        point({ horizon_minutes: 60 }),
      ],
      "active",
    ),
  );
  assert.equal(note?.tone, "info");
  assert.equal(
    note?.text,
    "10·30분 값은 기상청 관측 기반 AI 예측, 60분 값은 일반 날씨 예보예요 (관측 14:00)",
  );
  // the chart as a whole is not called an AI forecast
  assert.doesNotMatch(note?.text ?? "", /^기상청 관측을 쓴 AI/);
});

test("when every point is AI the note says so for all of them", () => {
  const note = nowcastSourceNote(
    data([
      point({ horizon_minutes: 10, prediction_source: "ai_model" }),
      point({ horizon_minutes: 30, prediction_source: "ai_model" }),
    ]),
  );
  assert.equal(note?.text, "10·30분 값 모두 기상청 관측 기반 AI 예측이에요");
});

test("a mixed chart marks the values that replaced the AI as a replacement", () => {
  const note = nowcastSourceNote(
    data([
      point({ horizon_minutes: 10, prediction_source: "ai_model" }),
      point({
        horizon_minutes: 30,
        prediction_source: "open_meteo_fallback",
        fallback_used: true,
        fallback_reason: "kma_stale",
      }),
      point({ horizon_minutes: 60 }),
    ]),
  );
  assert.equal(note?.tone, "warning");
  assert.match(
    note?.text ?? "",
    /^10분 값은 기상청 관측 기반 AI 예측, 30·60분 값은 일반 날씨 예보로 대체한 값이에요/,
  );
});

test("a fallback tells that the KMA observation was not received", () => {
  const note = nowcastSourceNote(
    data([
      point({ prediction_source: "open_meteo_fallback", fallback_used: true }),
    ]),
  );
  assert.equal(note?.tone, "warning");
  assert.match(note?.text ?? "", /대체/);
});

test("a fallback caused by a low sun is not blamed on the KMA observation", () => {
  const note = nowcastSourceNote(
    data([
      point({
        prediction_source: "open_meteo_fallback",
        fallback_used: true,
        fallback_reason: "low_solar_elevation",
      }),
    ]),
  );
  assert.equal(note?.tone, "info");
  assert.match(note?.text ?? "", /해가 낮아/);
  assert.doesNotMatch(note?.text ?? "", /기상청/);
});

test("a malformed observation time does not break the note", () => {
  const note = nowcastSourceNote(
    data([
      point({ prediction_source: "ai_model", kma_observed_at: "not a time" }),
    ]),
  );
  assert.equal(note?.text, "10분 값 모두 기상청 관측 기반 AI 예측이에요");
});

test("shadow mode says the AI result is only compared", () => {
  const note = nowcastSourceNote(data([point({})], "shadow"));
  assert.equal(note?.tone, "info");
  assert.match(note?.text ?? "", /시험 운영/);
});

test("active mode without AI points says the forecast is the general one", () => {
  assert.equal(
    nowcastSourceNote(data([point({})]))?.text,
    "일반 날씨 예보 기준이에요",
  );
});

test("no usable value is a warning and no points is no note", () => {
  const none = nowcastSourceNote(
    data([point({ prediction_source: "unavailable", dni_W_m2: null })]),
  );
  assert.equal(none?.tone, "warning");
  assert.equal(nowcastSourceNote(data([])), null);
});
