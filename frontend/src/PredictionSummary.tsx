import { useEffect, useState } from "react";
import type { NowcastComparisonResponse, SunpathPrediction } from "./spaceApi.ts";
import { fetchNowcastComparison } from "./spaceApi.ts";
import { regionOwners } from "./model.ts";
import type { Space } from "./model.ts";
import NowcastComparisonChart from "./NowcastComparisonChart.tsx";
import { nowcastSourceNote } from "./nowcastSource.ts";

export default function PredictionSummary({
  result,
  space,
  date,
  onRetry,
}: {
  result?: SunpathPrediction;
  space: Space;
  date: string;
  onRetry: () => void;
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
        if (!cancelled) setServerComparison(null);
      });

    return () => {
      cancelled = true;
    };
  }, [space.serverSceneId, startTime]);

  const comparison = serverComparison;
  const sourceNote = comparison ? nowcastSourceNote(comparison) : null;
  const owners = result ? regionOwners(space, result.regions.length) ?? result.regions.map((_, i) => i) : [];

  return (
    <div className="sun-reference muted small">
      <button type="button" className="text-button" onClick={onRetry}>
        다시 계산
      </button>

      {comparison && (
        <NowcastComparisonChart data={comparison} />
      )}
      {sourceNote && <p role="note">{sourceNote.text}</p>}

      {result && (
        <>
          <p>{(result.interval_seconds ?? 1800) / 60}분 간격으로 계산했어요.</p>
          <p>
            {[...sources]
              .map((source) =>
                source.includes("open_meteo")
                  ? "지역 날씨 예보 기반"
                  : source.includes("clear_sky")
                    ? "맑은 하늘 가정"
                    : source === "unknown"
                      ? "일사량 확인 불가"
                      : "저장된 기상 자료 기반",
              )
              .join(" · ")}
          </p>
          <ul>
            {[...new Set(owners)].map((index) => {
              // The furniture's top is a second server area of the same furniture: the first exposure is the earlier of the two.
              const group = result.regions.filter((_, j) => owners[j] === index);
              const r = group[0];
              const first = group.flatMap((g) => g.timeline.filter((t) => t.status === "exposed").slice(0, 1)).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))[0];
              const values = group.flatMap((g) => g.timeline.flatMap(
                (t) => t.samples?.map((s) => s.direct_irradiance_W_m2) ?? [],
              ));
              return (
                <li key={r.region_id}>
                  {space.regions[index]?.name ?? r.region_id}:{" "}
                  {first
                    ? `첫 노출 예상 ${time(first.timestamp)}`
                    : "계산 시각에 직사광선 경로 없음"}
                  {values.some((v) => v === null) && " · 일부 시간은 햇빛 세기를 알 수 없어요"}
                </li>
              );
            })}
          </ul>
          <p>바깥 건물의 그늘과 유리 투과는 계산에 넣지 않았어요.</p>
        </>
      )}
    </div>
  );
}
