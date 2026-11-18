import test from "node:test";
import assert from "node:assert/strict";
import { daylightProvenanceSegments, weatherProvenanceMessage } from "./weatherProvenance.ts";

const time = (hour: number, minute = 0) => new Date(Date.UTC(2026, 10, 18, hour, minute)).toISOString();
const solar = (minutes: number[]) => minutes.map((minute) => ({ timestamp: time(Math.floor(minute / 60), minute % 60), sun_direction_enu: [0, 0, 1] as [number, number, number] }));

test("summarizes one forecast interval in the space time zone", () => {
  const segments = daylightProvenanceSegments([
    { timestamp: time(0), source: "pvlib_clear_sky" }, { timestamp: time(1), source: "open_meteo" },
    { timestamp: time(2), source: "open_meteo" }, { timestamp: time(3), source: "pvlib_clear_sky" },
  ], solar([0, 60, 120, 180]), 3600);
  assert.equal(weatherProvenanceMessage(segments, "Asia/Seoul"), "오전 10시부터 오후 12시까지는 날씨 예보를 반영했어요.");
});

test("does not mention a short gap in the sentence but retains it in the provenance segments", () => {
  const segments = daylightProvenanceSegments([
    { timestamp: time(0), source: "open_meteo" }, { timestamp: time(0, 10), source: "pvlib_clear_sky" },
    { timestamp: time(0, 20), source: "open_meteo" }, { timestamp: time(0, 30), source: "open_meteo" },
  ], solar([0, 10, 20, 30]), 600);
  assert.equal(segments.length, 3);
  assert.equal(weatherProvenanceMessage(segments, "Asia/Seoul"), "오전 9시부터 오전 9시 40분까지는 날씨 예보를 반영했어요.");
});

test("keeps a thirty-minute gap as separate forecast ranges", () => {
  const segments = daylightProvenanceSegments([
    { timestamp: time(0), source: "open_meteo" }, { timestamp: time(0, 30), source: "pvlib_clear_sky" },
    { timestamp: time(1), source: "open_meteo" }, { timestamp: time(1, 30), source: "open_meteo" },
  ], solar([0, 30, 60, 90]), 1800);
  assert.equal(weatherProvenanceMessage(segments, "Asia/Seoul"), "오전 9시부터 오전 9시 30분까지와 오전 10시부터 오전 11시까지는 날씨 예보를 반영했어요.");
});
