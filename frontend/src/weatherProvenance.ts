export type IrradianceProvenance = {
  timestamp: string;
  source: "scene_weather" | "open_meteo" | "pvlib_clear_sky" | "unknown" | string;
};

export type SolarProvenance = {
  timestamp: string;
  sun_direction_enu: [number, number, number];
};

export type ProvenanceKind = "forecast" | "stored" | "unavailable" | "unknown";

export type ProvenanceSegment = {
  kind: ProvenanceKind;
  start: Date;
  end: Date;
};

const SHORT_GAP_MS = 30 * 60 * 1000;

function kindFor(source: IrradianceProvenance["source"]): ProvenanceKind {
  if (source === "open_meteo") return "forecast";
  if (source === "scene_weather") return "stored";
  if (source === "unknown") return "unknown";
  return "unavailable";
}

/** Build the visible provenance bar from daylight samples only. */
export function daylightProvenanceSegments(
  irradiance: IrradianceProvenance[] | undefined,
  solarPositions: SolarProvenance[] | undefined,
  intervalSeconds = 1800,
): ProvenanceSegment[] {
  if (!irradiance?.length || !solarPositions?.length) return [];
  const elevation = new Map(solarPositions.map((point) => [point.timestamp, point.sun_direction_enu[2]]));
  const sorted = [...irradiance].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const segments: ProvenanceSegment[] = [];
  for (let index = 0; index < sorted.length; index += 1) {
    const point = sorted[index];
    if ((elevation.get(point.timestamp) ?? 0) <= 0) continue;
    const start = new Date(point.timestamp);
    const next = sorted[index + 1];
    const end = next ? new Date(next.timestamp) : new Date(start.getTime() + intervalSeconds * 1000);
    if (end <= start) continue;
    const kind = kindFor(point.source);
    const previous = segments.at(-1);
    if (previous?.kind === kind && previous.end.getTime() === start.getTime()) previous.end = end;
    else segments.push({ kind, start, end });
  }
  return segments;
}

/** Merge only tiny non-forecast gaps in the sentence. The provenance bar stays exact. */
function sentenceSegments(segments: ProvenanceSegment[]): ProvenanceSegment[] {
  const merged = segments.map((segment) => ({ ...segment }));
  for (let index = 1; index < merged.length - 1; index += 1) {
    const segment = merged[index];
    if (
      segment.kind !== "forecast" &&
      segment.end.getTime() - segment.start.getTime() < SHORT_GAP_MS &&
      merged[index - 1].kind === "forecast" &&
      merged[index + 1].kind === "forecast"
    ) {
      merged[index - 1].end = merged[index + 1].end;
      merged.splice(index, 2);
      index -= 1;
    }
  }
  return merged;
}

function formatTime(value: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("ko-KR", { timeZone, hour: "numeric", minute: "numeric", hour12: true }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
  const period = part("dayPeriod") ?? "";
  const hour = part("hour") ?? "0";
  const minute = part("minute") ?? "00";
  return minute === "00" ? `${period} ${hour}시` : `${period} ${hour}시 ${minute}분`;
}

function formatRange(segment: ProvenanceSegment, timeZone: string): string {
  return `${formatTime(segment.start, timeZone)}부터 ${formatTime(segment.end, timeZone)}까지`;
}

export function weatherProvenanceMessage(segments: ProvenanceSegment[], timeZone: string): string | null {
  if (!segments.length) return null;
  const sentence = sentenceSegments(segments);
  const forecasts = sentence.filter((segment) => segment.kind === "forecast");
  if (forecasts.length) {
    if (forecasts.length === 1 && sentence.length === 1 && segments.every((segment) => segment.kind === "forecast"))
      return "오늘 일조 예측 전체에는 날씨 예보를 반영했어요.";
    const ranges = forecasts.map((segment) => formatRange(segment, timeZone));
    const joined = ranges.length === 1 ? ranges[0] : `${ranges.slice(0, -1).join("와 ")}와 ${ranges.at(-1)}`;
    return `${joined}는 날씨 예보를 반영했어요.`;
  }
  const stored = sentence.filter((segment) => segment.kind === "stored");
  if (stored.length === 1 && sentence.length === 1) return "오늘 일조 예측 전체에는 저장된 날씨 정보를 반영했어요.";
  if (stored.length) return `${formatRange(stored[0], timeZone)}는 저장된 날씨 정보를 반영했어요.`;
  return "현재 날씨 예보를 적용할 수 없었어요.";
}
