import { useEffect, useState } from "react";
import type { NowcastComparisonResponse, SunpathPrediction } from "./spaceApi.ts";
import { buildDemoNowcastComparison, fetchNowcastComparison } from "./spaceApi.ts";
import { regionOwners } from "./model.ts";
import type { Space } from "./model.ts";
import NowcastComparisonChart from "./NowcastComparisonChart.tsx";
import { nowcastSourceNote } from "./nowcastSource.ts";
import { daylightProvenanceSegments, weatherProvenanceMessage } from "./weatherProvenance.ts";
import "./PredictionSummary.css";

/** The furniture's own name when the area was set for a piece of furniture (the face is chosen
 *  automatically, so it is not shown); otherwise the area's name. */
function regionLabel(space: Space, index: number): string | undefined {
  const region = space.regions[index];
  const furniture = region?.furnitureId ? space.furniture.find((f) => f.id === region.furnitureId) : undefined;
  return furniture?.name ?? region?.name;
}

export default function PredictionSummary({
  result,
  space,
  date,
}: {
  result?: SunpathPrediction;
  space: Space;
  date: string;
}) {
  const [serverComparison, setServerComparison] = useState<NowcastComparisonResponse | null>(null);

  const time = (timestamp: string) =>
    new Intl.DateTimeFormat("ko-KR", {
      timeZone: space.site!.timezone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(timestamp));

  const sources = new Set(result?.irradiance?.map((p) => p.source));
  const provenance = daylightProvenanceSegments(result?.irradiance, result?.solar_positions, result?.interval_seconds ?? 1800);
  const provenanceMessage = weatherProvenanceMessage(provenance, space.site!.timezone);
  const startTime = `${date}T00:00:00Z`;

  useEffect(() => {
    let cancelled = false;
    if (!space.serverSceneId) {
      return;
    }

    fetchNowcastComparison(space.serverSceneId)
      .then((resp) => {
        if (!cancelled) setServerComparison(resp);
      })
      .catch(() => {
        // Development only: ?test=1 (or ?test=fallback) shows sample forecast data when the server has no run, so the chart and its note can be looked at.
        const demo = import.meta.env.DEV ? new URLSearchParams(location.search).get("test") : null;
        const sample = demo ? buildDemoNowcastComparison(startTime) : null;
        if (sample && demo === "fallback") {
          sample.points = sample.points.map((p) =>
            p.horizon_minutes === 30
              ? { ...p, prediction_source: "open_meteo_fallback", fallback_used: true, fallback_reason: "kma_stale" }
              : p,
          );
        }
        if (!cancelled) setServerComparison(sample);
      });

    return () => {
      cancelled = true;
    };
  }, [space.serverSceneId, startTime]);

  const comparison = serverComparison;
  const sourceNote = comparison ? nowcastSourceNote(comparison) : null;
  const owners = result ? regionOwners(space, result.regions.length) ?? result.regions.map((_, i) => i) : [];

  // The natural-language summary deliberately ignores sub-30-minute gaps; the bar below does not.
  const noWeather = false;
  const gaps = !!result && (sources.has("unknown") || result.regions.some((r) => r.timeline.some((t) => t.samples?.some((x) => x.direct_irradiance_W_m2 === null))));

  return (
    <div className="forecast-panel">
      {comparison && <NowcastComparisonChart data={comparison} />}
      {sourceNote?.tone === "warning" && <p role="note" className="forecast-note">{sourceNote.text}</p>}
      {provenanceMessage && <p role="note" className="forecast-note">{provenanceMessage}</p>}
      {provenance.length > 0 && <div className="forecast-provenance" aria-label="시간대별 날씨 예보 적용 상태">
        <div className="forecast-provenance-bar" aria-hidden="true">
          {provenance.map((segment) => <span
            className={`forecast-provenance-segment forecast-provenance-${segment.kind}`}
            key={`${segment.kind}-${segment.start.toISOString()}`}
            style={{ flexGrow: segment.end.getTime() - segment.start.getTime() }}
          />)}
        </div>
        <div className="forecast-provenance-legend">
          <span><i className="forecast-provenance-key forecast-provenance-forecast" />날씨 예보 반영</span>
          {provenance.some((segment) => segment.kind === "stored") && <span><i className="forecast-provenance-key forecast-provenance-stored" />저장된 날씨 정보</span>}
          {provenance.some((segment) => segment.kind === "unavailable") && <span><i className="forecast-provenance-key forecast-provenance-unavailable" />예보 미적용</span>}
          {provenance.some((segment) => segment.kind === "unknown") && <span><i className="forecast-provenance-key forecast-provenance-unknown" />계산 불가</span>}
        </div>
      </div>}
      {noWeather && <p role="note" className="forecast-note">날씨 정보 없음, 맑은 날 기준으로 계산.</p>}
      {gaps && <p role="note" className="forecast-note">일부 시간대 일사량 확인 불가.</p>}

      {result && (
        <ul className="forecast-list">
          <li className="forecast-list-head" aria-hidden="true"><span>가구</span><span>직사광선 시작</span></li>
          {[...new Set(owners)].map((index) => {
            // The furniture's top is a second server area of the same furniture: the first exposure is the earlier of the two.
            const group = result.regions.filter((_, j) => owners[j] === index);
            const r = group[0];
            const first = group.flatMap((g) => g.timeline.filter((t) => t.status === "exposed").slice(0, 1)).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))[0];
            return (
              <li key={r.region_id}>
                <span>{regionLabel(space, index) ?? r.region_id}</span>
                <span>{first ? time(first.timestamp) : "없음"}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
