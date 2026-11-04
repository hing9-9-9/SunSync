import type { SunpathPrediction } from "./spaceApi";
import type { Space } from "./model";

export default function PredictionSummary({ result, space, date, onDate, onRetry }: {
  result?: SunpathPrediction; space: Space; date: string;
  onDate: (value: string) => void; onRetry: () => void;
}) {
  const time = (timestamp: string) => new Intl.DateTimeFormat("ko-KR", {
    timeZone: space.site!.timezone, hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(timestamp));
  const sources = new Set(result?.irradiance?.map(p => p.source));
  return <div className="sun-reference muted small">
    <label>예측 날짜 <input type="date" value={date} onChange={e => e.target.value && onDate(e.target.value)} /></label>{" "}
    <button type="button" className="text-button" onClick={onRetry}>다시 계산</button>
    {result && <>
      <p>공간 시간대: {space.site!.timezone} · {(result.interval_seconds ?? 1800) / 60}분 간격 예상</p>
      <p>{[...sources].map(source => source.includes("open_meteo") ? "지역 날씨 예보 기반" : source.includes("clear_sky") ? "맑은 하늘 가정" : source === "unknown" ? "일사량 확인 불가" : "저장된 기상 자료 기반").join(" · ")}</p>
      <ul>{result.regions.map((r, index) => {
        const first = r.timeline.find(t => t.status === "exposed");
        const values = r.timeline.flatMap(t => t.samples?.map(s => s.direct_irradiance_W_m2) ?? []);
        const known = values.filter((v): v is number => v !== null);
        return <li key={r.region_id}>{space.regions[index]?.name ?? r.region_id}: {first ? `첫 노출 예상 ${time(first.timestamp)}` : "계산 시각에 직사광선 경로 없음"}
          {known.length > 0 && ` · 최대 ${Math.round(known.reduce((max, value) => Math.max(max, value), 0))} W/m²`}
          {values.some(v => v === null) && " · 일부 일사량 확인 불가"}
        </li>;
      })}</ul>
      <p>창 투과율·실외 건물 차폐·반사는 미반영입니다. 필름 표시는 계산된 차광 계획이며 실제 장치 실행 상태가 아닙니다.</p>
    </>}
  </div>;
}
