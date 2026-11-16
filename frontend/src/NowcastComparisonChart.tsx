import type { NowcastComparisonResponse } from "./spaceApi.ts";
import {
  DEFAULT_CHART_MARGIN,
  buildPolylinePoints,
  buildQuantileBandPolygon,
  computeX,
  computeY,
} from "./spaceApi.ts";

export interface NowcastComparisonChartProps {
  data: NowcastComparisonResponse;
  width?: number;
  height?: number;
}

const TICK_STEP = 200;

/**
 * Sunlight strength for the next hour as a report figure: titled, both axes drawn with ticks and
 * titles (time after now across, W/m² up), applied values with a marker on every forecast point,
 * the general forecast and the AI range. No hover readout (a phone has none).
 */
export default function NowcastComparisonChart({
  data,
  width = 360,
  height = 230,
}: NowcastComparisonChartProps) {
  const points = data.points ?? [];
  const margin = { ...DEFAULT_CHART_MARGIN, top: 14, right: 16, bottom: 50, left: 54 };

  const rawMax = Math.max(
    ...points.map((p) => Math.max(p.dni_W_m2 ?? 0, p.p90_dni_W_m2 ?? 0, p.open_meteo_dni_W_m2 ?? 0)),
    100,
  );
  const maxDni = Math.ceil((rawMax * 1.1) / TICK_STEP) * TICK_STEP;
  const yTicks = Array.from({ length: maxDni / TICK_STEP + 1 }, (_, i) => i * TICK_STEP);

  const line = (values: (number | null)[]) => buildPolylinePoints(values, width, height, maxDni, margin);
  const bandPolygon = buildQuantileBandPolygon(points, width, height, maxDni, margin);
  const usedPoints = line(points.map((p) => p.dni_W_m2));
  const forecastPoints = line(points.map((p) => p.open_meteo_dni_W_m2));

  const left = margin.left;
  const right = width - margin.right;
  const bottom = computeY(0, maxDni, height, margin);
  const top = computeY(maxDni, maxDni, height, margin);

  return (
    <figure className="forecast-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="향후 60분 일사량 예측 그래프. 가로축은 현재 기준 경과 시간, 세로축은 햇빛 세기(W/m²)">
        {/* gridlines, y ticks */}
        {yTicks.map((value) => {
          const y = computeY(value, maxDni, height, margin);
          return (
            <g key={value}>
              {value > 0 && <line x1={left} y1={y} x2={right} y2={y} className="forecast-grid" />}
              <line x1={left - 4} y1={y} x2={left} y2={y} className="forecast-axis-line" />
              <text x={left - 8} y={y + 4} textAnchor="end" className="forecast-tick">{value}</text>
            </g>
          );
        })}

        {/* axes */}
        <line x1={left} y1={top} x2={left} y2={bottom} className="forecast-axis-line" />
        <line x1={left} y1={bottom} x2={right} y2={bottom} className="forecast-axis-line" />

        {/* x ticks */}
        {points.map((p, i) => {
          const x = computeX(i, points.length, width, margin);
          return (
            <g key={p.horizon_minutes}>
              <line x1={x} y1={bottom} x2={x} y2={bottom + 4} className="forecast-axis-line" />
              <text x={x} y={bottom + 18} textAnchor="middle" className="forecast-tick">+{p.horizon_minutes}분</text>
            </g>
          );
        })}

        {/* axis titles */}
        <text x={(left + right) / 2} y={height - 6} textAnchor="middle" className="forecast-axis-title">현재 기준 경과 시간</text>
        <text transform={`translate(12 ${(top + bottom) / 2}) rotate(-90)`} textAnchor="middle" className="forecast-axis-title">햇빛 세기 (W/m²)</text>

        {bandPolygon && <polygon points={bandPolygon} className="forecast-band" />}
        {forecastPoints && <polyline points={forecastPoints} className="forecast-line forecast-line-general" />}
        {usedPoints && <polyline points={usedPoints} className="forecast-line forecast-line-used" />}
        {points.map((p, i) =>
          p.dni_W_m2 === null ? null : (
            <circle
              key={p.horizon_minutes}
              cx={computeX(i, points.length, width, margin)}
              cy={computeY(p.dni_W_m2, maxDni, height, margin)}
              r="3.5"
              className="forecast-dot"
            />
          ),
        )}
      </svg>

      <figcaption className="forecast-legend">
        {usedPoints && <span><i className="forecast-key forecast-key-used" />적용값</span>}
        {forecastPoints && <span><i className="forecast-key forecast-key-general" />일반 날씨 예보</span>}
        {bandPolygon && <span><i className="forecast-key forecast-key-band" />AI 예측 범위</span>}
      </figcaption>
    </figure>
  );
}
