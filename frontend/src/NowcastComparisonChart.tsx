import { useState } from "react";
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

export default function NowcastComparisonChart({
  data,
  width = 560,
  height = 240,
}: NowcastComparisonChartProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const points = data.points ?? [];
  const margin = DEFAULT_CHART_MARGIN;

  const rawMax = Math.max(
    ...points.map((p) => Math.max(p.dni_W_m2 ?? 0, p.p90_dni_W_m2 ?? 0, p.open_meteo_dni_W_m2 ?? 0)),
    800,
  );
  // Round up to clean 100s
  const maxDni = Math.ceil(rawMax / 100) * 100;

  const bandPolygon = buildQuantileBandPolygon(points, width, height, maxDni, margin);
  const p50Points = buildPolylinePoints(
    points.map((p) => p.p50_dni_W_m2),
    width,
    height,
    maxDni,
    margin,
  );
  const openMeteoPoints = buildPolylinePoints(
    points.map((p) => p.open_meteo_dni_W_m2),
    width,
    height,
    maxDni,
    margin,
  );
  const selectedPoints = buildPolylinePoints(
    points.map((p) => p.dni_W_m2),
    width,
    height,
    maxDni,
    margin,
  );

  const yTicks = [0, Math.round(maxDni * 0.33), Math.round(maxDni * 0.66), maxDni];
  const stepCount = Math.max(1, Math.floor(points.length / 5));
  const xIndices = points
    .map((_, i) => i)
    .filter((i) => i % stepCount === 0 || i === points.length - 1);

  const hoveredPoint = hoverIndex !== null && points[hoverIndex] ? points[hoverIndex] : null;

  return (
    <div
      className="nowcast-comparison-container"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        margin: "12px 0",
        background: "var(--c-pressed, #fafafc)",
        padding: "14px 16px",
        borderRadius: "var(--radius-card, 12px)",
        border: "1px solid var(--c-hairline, #ebebf0)",
      }}
    >
      <div
        className="nowcast-summary-bar"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "8px",
          fontSize: "12px",
        }}
      >
        <span
          className="pill"
          style={{
            background: "var(--c-action-tint, rgba(102, 96, 229, 0.12))",
            color: "var(--c-action-ink, #554ed0)",
            padding: "3px 10px",
            fontWeight: 600,
          }}
        >
          운영 모드 {data.rollout_mode === "active" ? "AI 활성" : "Shadow"}
        </span>
        <span
          className="pill"
          style={{
            background: "var(--c-block, #e9e9e9)",
            color: "var(--c-protect-ink, #343434)",
            padding: "3px 10px",
          }}
        >
          모델 {data.model_version}
        </span>
        <span
          className="pill"
          style={{
            background: "var(--c-block, #e9e9e9)",
            color: "var(--c-protect-ink, #343434)",
            padding: "3px 10px",
          }}
        >
          {data.status === "ready" ? "입력 정상" : "일부 입력 불가"}
        </span>
      </div>

      <div style={{ position: "relative", width: "100%", overflowX: "auto" }}>
        <svg
          role="img"
          aria-label="KMA AI × Open-Meteo 일사량 예측 불확실성 밴드 및 비교 차트"
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: "100%", maxWidth: `${width}px`, height: "auto", display: "block" }}
        >
          <title>KMA AI × Open-Meteo 일사량 예측 불확실성 밴드 및 비교 차트</title>

          {/* Grid lines and Y labels */}
          {yTicks.map((val) => {
            const y = computeY(val, maxDni, height, margin);
            return (
              <g key={`y-${val}`}>
                <line
                  x1={margin.left}
                  y1={y}
                  x2={width - margin.right}
                  y2={y}
                  stroke="var(--c-hairline, #e0e0e5)"
                  strokeDasharray="3 3"
                  strokeWidth="1"
                />
                <text
                  x={margin.left - 6}
                  y={y + 3}
                  textAnchor="end"
                  fill="var(--c-muted, #75727d)"
                  fontSize="10"
                >
                  {val}
                </text>
              </g>
            );
          })}

          {/* X axis labels */}
          {xIndices.map((idx) => {
            const x = computeX(idx, points.length, width, margin);
            const pt = points[idx];
            return (
              <text
                key={`x-${idx}`}
                x={x}
                y={height - margin.bottom + 16}
                textAnchor="middle"
                fill="var(--c-muted, #75727d)"
                fontSize="10"
              >
                +{pt.horizon_minutes}분
              </text>
            );
          })}

          {/* Uncertainty band polygon (q10 to q90) */}
          {bandPolygon && (
            <polygon
              points={bandPolygon}
              fill="rgba(102, 96, 229, 0.22)"
              aria-label="AI 예측 불확실성 밴드 (p10-p90)"
            />
          )}

          {/* Selected operational output */}
          {selectedPoints && (
            <polyline
              points={selectedPoints}
              fill="none"
              stroke="var(--c-action, #6660e5)"
              strokeWidth="2.5"
            />
          )}

          {/* Open-Meteo hourly curve */}
          {openMeteoPoints && (
            <polyline
              points={openMeteoPoints}
              fill="none"
              stroke="var(--c-sun, #ff7426)"
              strokeDasharray="5 3"
              strokeWidth="2"
            />
          )}

          {/* KMA AI Median (q50) curve where public quantiles exist */}
          {p50Points && (
            <polyline
              points={p50Points}
              fill="none"
              stroke="var(--c-action-ink, #554ed0)"
              strokeDasharray="3 2"
              strokeWidth="1.5"
            />
          )}

          {/* Interactive hover points */}
          {points.map((_, i) => {
            const x = computeX(i, points.length, width, margin);
            return (
              <rect
                key={`hit-${i}`}
                x={x - width / (points.length * 2)}
                y={margin.top}
                width={width / points.length}
                height={height - margin.top - margin.bottom}
                fill="transparent"
                style={{ cursor: "pointer" }}
                onMouseEnter={() => setHoverIndex(i)}
                onMouseLeave={() => setHoverIndex(null)}
              />
            );
          })}

          {/* Hover highlight line */}
          {hoverIndex !== null && (
            <line
              x1={computeX(hoverIndex, points.length, width, margin)}
              y1={margin.top}
              x2={computeX(hoverIndex, points.length, width, margin)}
              y2={height - margin.bottom}
              stroke="var(--c-action-ink, #554ed0)"
              strokeWidth="1.2"
              strokeDasharray="2 2"
            />
          )}
        </svg>
      </div>

      {/* Legend & Active Point readout */}
      <div
        className="nowcast-legend-bar"
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "11px",
          color: "var(--c-muted, #5f5b66)",
          paddingTop: "4px",
          borderTop: "1px solid var(--c-hairline, #ebebf0)",
        }}
      >
        <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <span
              style={{
                width: "12px",
                height: "10px",
                background: "rgba(102, 96, 229, 0.28)",
                borderRadius: "2px",
              }}
            />
            AI 불확실성 밴드(q10~q90)
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <span
              style={{
                width: "14px",
                height: "2.5px",
                background: "var(--c-action, #6660e5)",
              }}
            />
            선택된 운영 DNI
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <span
              style={{
                width: "14px",
                height: "2px",
                background: "var(--c-sun, #ff7426)",
                borderBottom: "1px dashed #ff7426",
              }}
            />
            Open-Meteo 예보
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <span
              style={{
                width: "14px",
                height: "1.5px",
                background: "#9d9d9d",
                borderBottom: "1px dotted #9d9d9d",
              }}
            />
            AI 중앙값(q50, 10·30분 활성 시)
          </span>
        </div>

        {hoveredPoint && (
          <div style={{ fontWeight: 600, color: "var(--c-ink, #1d1c22)" }}>
            +{hoveredPoint.horizon_minutes}분: {hoveredPoint.dni_W_m2 ?? "확인 불가"} W/m²
            {hoveredPoint.p50_dni_W_m2 !== null
              ? ` (AI 밴드 ${hoveredPoint.p10_dni_W_m2}~${hoveredPoint.p90_dni_W_m2})`
              : ""}
          </div>
        )}
      </div>
    </div>
  );
}
