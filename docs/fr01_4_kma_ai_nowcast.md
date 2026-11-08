# FR-01.4: KMA AI × Open-Meteo Hybrid Quantile Nowcasting & Uncertainty Band

> **상태: 레거시 설계·합성 benchmark 기록.** 아래의 GK2A patch advection, Gradient
> Ensemble, API·DB·프론트엔드 흐름과 성능 표는 현재 동결된 실데이터 V1 모델의 구현
> 사양이나 final-test 결과가 아니다. 삭제하지 않고 초기 방향과 합성 회귀 결과로 보존한다.
>
> 현재 모델은 `fr01_4_ai_model_implementation.md`, 실제 Validation 결과는
> `fr01_4_ai_kma_analysis.md`를 따른다. V1은 GK2A 지점 DSR·CA와 Open-Meteo를 사용하는
> 10·30·60분 **선형 분위수 모델**이며 `cloud_patch_used=false`다.

<details>
<summary>레거시 설계와 합성 benchmark 전체 보기</summary>

## 1. Executive Summary

In high-performance smart window film automation and direct-sun protection (FR-01 / FR-02 / FR-04), building occupants require dependable solar irradiance forecasting. While synoptic numerical weather models like Open-Meteo provide 1-hour mean irradiance forecasts, passing cumulus clouds cause severe intra-hour intermittency that static forecasts cannot capture.

The **FR-01.4 KMA AI × Open-Meteo Hybrid Quantile Nowcaster** resolves this challenge by fusing high-frequency Korea Meteorological Administration (KMA) surface observations with localized GK2A geostationary satellite cloud patch advection vectors and Open-Meteo background forecasts into calibrated Direct Normal Irradiance (DNI) quantiles ($q_{10}, q_{50}, q_{90}$).

---

## 2. Resolving GK2A DSR Technical Limitations

Previous architectural evaluations of KMA GK2A satellite products identified two fundamental bottlenecks:

1. **Physical Divergence ($DSR \ne DNI$):**
   - KMA GK2A provides Downward Shortwave Radiation (DSR, i.e., Global Horizontal Irradiance GHI in $W/m^2$).
   - Window film solar heat gain and direct glare algorithms require **Direct Normal Irradiance (DNI)**.
   - When passing clouds partially obscure the sun, DSR may only drop moderately because diffuse radiation ($DHI$) remains high ($100-300\text{ W/m}^2$), whereas $DNI$ drops precipitously to near zero.
2. **Computational & Bandwidth Overhead of Full NetCDF Files:**
   - Full 2D NetCDF downloads for the East Asia domain incur high latency (several megabytes per timestamp) and unnecessary parsing overhead for local building automation.

### The 2-Stage Hybrid Architecture

To overcome these limitations without sacrificing precision or real-time performance:

```
[KMA AWS / GK2A DSR (GHI)]
             │
             ▼
┌────────────────────────────────────────────────────────┐
│ Stage 1: Fast Physics & Solar Geometry Decomposition   │
│ - pvlib / Analytical Solar Position & Clear-Sky Curves │
│ - Clearness Index kt = DSR / GHI_clear                │
│ - Erbs / DIRINT Non-linear Diffuse Decomposition       │
│ - Direct Decomposition: DNI_erbs = DSR*(1-kd) / sin(γ) │
│ - GK2A Satellite Patch: Upwind Advection & Volatility  │
└────────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: Quantile Gradient Ensemble (q10, q50, q90)    │
│ - Multi-quantile basis regression on physical priors   │
│ - Exponential decay blending with Open-Meteo NWP       │
│ - Guaranteed Monotonicity: 0 <= p10 <= p50 <= p90      │
│ - Cloud Volatility Spread Expansion (p90 - p10)        │
└────────────────────────────────────────────────────────┘
             │
             ▼
[SQLite DB: nowcast_point (v3)] ──► [REST API] ──► [Frontend SVG Chart]
```

---

## 3. Mathematical Formulation

### 3.1 Clearness Index & Erbs Diffuse Decomposition

At any target timestamp $t$ with solar elevation $\gamma_s > 0$:
$$k_t = \text{clip}\left(\frac{\text{DSR}}{\text{GHI}_{\text{clear}}}, 0.0, 1.25\right)$$

The diffuse fraction $k_d = \frac{\text{DHI}}{\text{GHI}}$ is decomposed using the non-linear Erbs correlation:
$$k_d = \begin{cases}
1.0 - 0.09 \cdot k_t & \text{if } k_t \le 0.22 \\
0.9511 - 0.1604 k_t + 4.388 k_t^2 - 16.638 k_t^3 + 12.336 k_t^4 & \text{if } 0.22 < k_t \le 0.80 \\
0.165 & \text{if } k_t > 0.80
\end{cases}$$

The direct normal irradiance prior is obtained by geometric projection:
$$\text{DNI}_{\text{erbs}} = \text{clip}\left(\frac{\text{DSR} \cdot (1.0 - k_d)}{\sin(\gamma_s)}, 0.0, \text{DNI}_{\text{clear}} \times 1.05\right)$$

### 3.2 GK2A Localized Cloud Patch Advection

Rather than fetching full 2D NetCDF grids, the model consumes a compact `CloudPatchSummary` ($10 \times 10\text{ km}$ neighborhood):
- Wind motion vector: $V = \sqrt{u^2 + v^2}$ (m/s)
- Projected advection factor over lead time $\Delta t$:
  $$\Delta_{\text{advection}} = \text{upwind\_cloud\_gradient} \times \left(\frac{V \cdot 3.6}{\text{patch\_size\_km}}\right) \times \Delta t$$

Positive upwind gradients reduce transmission as clouds advance toward the site.

### 3.3 Multi-Quantile Loss & Guaranteed Monotonicity

For quantiles $\alpha \in \{0.10, 0.50, 0.90\}$, the ensemble minimizes the pinball loss function:
$$L_\alpha(y, \hat{y}) = \frac{1}{N} \sum_{i=1}^N \max\left(\alpha (y_i - \hat{y}_i), (\alpha - 1.0)(y_i - \hat{y}_i)\right)$$

At inference time, physical bounds and monotonicity are strictly enforced:
1. When $\gamma_s \le 0.0^\circ$ (night):
   $$\text{DNI}_{p10} = \text{DNI}_{p50} = \text{DNI}_{p90} = 0.0\text{ W/m}^2$$
2. When $\gamma_s > 0.0^\circ$:
   $$\text{DNI}_{p10} = \max(0.0, k_{10} \cdot \text{DNI}_{\text{clear}})$$
   $$\text{DNI}_{p50} = \max(\text{DNI}_{p10}, k_{50} \cdot \text{DNI}_{\text{clear}})$$
   $$\text{DNI}_{p90} = \min(\text{DNI}_{\text{clear}} \cdot 1.05, \max(\text{DNI}_{p50}, k_{90} \cdot \text{DNI}_{\text{clear}}))$$
   $$\text{uncertainty\_spread} = \text{DNI}_{p90} - \text{DNI}_{p10} \ge 0.0$$

---

## 4. Database Schema Migration (Version 3)

The durable SQLite schema is updated in `backend/app/migrations/runner.py` with `LATEST_VERSION = 3`:

```sql
ALTER TABLE nowcast_point ADD COLUMN dni_p10_W_m2 REAL;
ALTER TABLE nowcast_point ADD COLUMN dni_p50_W_m2 REAL;
ALTER TABLE nowcast_point ADD COLUMN dni_p90_W_m2 REAL;
ALTER TABLE nowcast_point ADD COLUMN kma_ai_dni_W_m2 REAL;
ALTER TABLE nowcast_point ADD COLUMN open_meteo_dni_W_m2 REAL;
```

Existing v1 and v2 records remain fully valid; new columns default to `NULL` for legacy points.

---

## 5. API Contracts

### 5.1 POST `/api/scenes/{scene_id}/ai-nowcast`

Accepts real-time observations and optional satellite patch summary:

```json
{
  "start_time": "2026-10-14T03:00:00Z",
  "duration_minutes": 120,
  "interval_seconds": 600,
  "mode": "kma_ai_ensemble",
  "kma_observations": [
    {
      "timestamp": "2026-10-14T02:50:00Z",
      "dsr_W_m2": 680.0,
      "cloud_cover_pct": 25.0,
      "temperature_c": 21.0,
      "humidity_pct": 50.0
    }
  ],
  "cloud_patch": {
    "patch_size_km": 10.0,
    "mean_cloud_fraction": 0.28,
    "upwind_cloud_gradient": 0.08,
    "motion_u_m_s": 3.5,
    "motion_v_m_s": -1.2,
    "cloud_top_height_m": 3200.0
  }
}
```

Response includes `QuantileNowcastPoint` series and `ComparisonMetrics`:

```json
{
  "run_id": "c138d6f5-...",
  "scene_id": "scene-library-01",
  "mode": "kma_ai_ensemble",
  "model_version": "kma_openmeteo_quantile_nowcast_v1",
  "start_time": "2026-10-14T03:00:00.000000Z",
  "end_time": "2026-10-14T05:00:00.000000Z",
  "interval_seconds": 600,
  "points": [
    {
      "timestamp": "2026-10-14T03:00:00.000000Z",
      "solar_elevation_deg": 44.12,
      "clear_sky_dni_W_m2": 890.4,
      "open_meteo_dni_W_m2": 694.5,
      "kma_ai_dni_W_m2": 724.8,
      "dni_p10_W_m2": 612.0,
      "dni_p50_W_m2": 724.8,
      "dni_p90_W_m2": 850.2,
      "clear_sky_index_p50": 0.814,
      "uncertainty_spread_W_m2": 238.2,
      "selected_dni_W_m2": 724.8,
      "selected_source": "kma_ai_ensemble",
      "fallback_used": false
    }
  ],
  "metrics": {
    "sample_count": 13,
    "mean_uncertainty_spread_W_m2": 218.4,
    "cloud_volatility_index": 0.285,
    "kma_vs_open_meteo_mae_W_m2": 45.2,
    "kma_ai_rmse_W_m2": null,
    "open_meteo_rmse_W_m2": null,
    "kma_ai_pinball_loss": null
  }
}
```

### 5.2 GET `/api/scenes/{scene_id}/nowcast-comparison`

Enables one-call retrieval from frontend components with optional `start_time`, defaulting to the scene's scenario time:

- `duration_minutes: int` (default `240`, range 5..1440)
- `interval_seconds: int` (default `900`, range 60..3600)
- `mode: NowcastMode` (default `"kma_ai_ensemble"`)

---

## 6. Frontend Visualization (`NowcastComparisonChart.tsx`)

Rendered inside `PredictionSummary.tsx`:

- **AI Uncertainty Band ($q_{10} \sim q_{90}$):** Shaded purple polygon (`fill="rgba(102, 96, 229, 0.22)"`) representing direct-sun risk corridor.
- **KMA AI Median ($q_{50}$):** Solid purple line (`#6660e5`, width 2.5px).
- **Open-Meteo 1-Hour Forecast:** Dashed orange line (`#ff7426`, dash 5 3).
- **pvlib Clear-Sky Envelope:** Dotted gray curve (`#9d9d9d`, dot 2 3).
- **Summary Pills:** Live readouts of average uncertainty spread ($\pm W/m^2$), cloud volatility index, and KMA AI vs Open-Meteo MAE.
- **Client Offline Fallback (`buildDemoNowcastComparison`):** Automatically renders realistic simulation if backend server is offline or scene is not yet uploaded.

---

## 7. Experimental Benchmark Results

Run the standalone verification suite:
```bash
PYTHONPATH=backend .venv/bin/python tools/ai_experiments/run_fr01_kma_nowcast_experiment.py
```

### Benchmark Summary Across Atmospheric Regimes

| Atmospheric Regime | Open-Meteo RMSE | KMA AI RMSE | RMSE Improvement | Open-Meteo MAE | KMA AI MAE | MAE Improvement | Pinball Loss | 80% PICP | Mean Spread |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Clear** | 63.8 W/m² | 53.1 W/m² | **+16.7%** | 61.0 W/m² | 41.9 W/m² | **+31.3%** | 13.8 | 78.9% | ±90.2 W/m² |
| **Intermittent** | 408.6 W/m² | 310.6 W/m² | **+24.0%** | 360.5 W/m² | 276.7 W/m² | **+23.3%** | 96.7 | 26.3% | ±185.2 W/m² |
| **Overcast** | 110.7 W/m² | 43.6 W/m² | **+60.6%** | 110.7 W/m² | 37.1 W/m² | **+66.5%** | 11.7 | 100.0% | ±82.0 W/m² |

### Key Observations for Parallel Tracks (Track 2 & Track 3)
1. **Adaptive Film Control (Track 2 - FR-04):**
   - The uncertainty spread ($p90 - p10$) provides the exact confidence margin needed for dynamic programming film transitions, avoiding annoying rapid on/off cycling during partly cloudy days.
2. **Degradation & Optical Profiling (Track 3 - FR-05):**
   - True DNI exposure integrated over time using $p50$ prevents cumulative UV/irradiance degradation under-counting caused by diffuse-only approximations.

</details>
