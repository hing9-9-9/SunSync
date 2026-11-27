# SunSync-AI: Machine Learning-Driven Solar Nowcasting and Computer Vision for Proactive Smart Film Shading Control

**Track:** Prototype Service with AIML Integration and Smart Appliance Ecosystem  
**Repository:** [https://github.com/hing9-9-9/SunSync](https://github.com/hing9-9-9/SunSync)  
**Codebase Reference:** `main` (`882521e`, November 24, 2026)

## Members

- **Jiyeon Kim**, Department of Information Systems, Hanyang University, kjy8402@hanyang.ac.kr
- **Daeun Lee**, Department of Information Systems, Hanyang University, daeun4862@hanyang.ac.kr
- **Dain Lee**, Department of Information Systems, Hanyang University, ldi0308@hanyang.ac.kr
- **Heegyeong Chung**, Department of Information Systems, Hanyang University, hgchung04@gmail.com
- **Misong Ha**, Department of Information Systems, Hanyang University, msha9865@hanyang.ac.kr

## Table of Contents

- [I. Introduction](#i-introduction)
  - [A. Motivation](#a-motivation)
  - [B. System Capabilities](#b-system-capabilities)
- [II. Datasets](#ii-datasets)
  - [A. Solar Irradiance Nowcasting Dataset (FR-01.4)](#a-solar-irradiance-nowcasting-dataset-fr-014)
  - [B. Real-Weather and 1-Minute Measured Irradiance Replay Datasets (FR-04)](#b-real-weather-and-1-minute-measured-irradiance-replay-datasets-fr-04)
  - [C. Vision AI 3D Sensing Benchmark and Field Diagnostics Inputs (FR-03 & FR-06)](#c-vision-ai-3d-sensing-benchmark-and-field-diagnostics-inputs-fr-03--fr-06)
- [III. Methodology](#iii-methodology)
  - [A. End-to-End System Architecture](#a-end-to-end-system-architecture)
  - [B. Track 1: Sub-Hourly Probabilistic Solar Nowcasting (FR-01.4)](#b-track-1-sub-hourly-probabilistic-solar-nowcasting-fr-014)
  - [C. Track 2: Hybrid Vision AI 3D Sensing and Guided ArUco Fallback (FR-03 & FR-06)](#c-track-2-hybrid-vision-ai-3d-sensing-and-guided-aruco-fallback-fr-03--fr-06)
  - [D. Track 3: Uncertainty-Aware Adaptive Shading Control and Scenario Precomputation (FR-04)](#d-track-3-uncertainty-aware-adaptive-shading-control-and-scenario-precomputation-fr-04)
  - [E. Core Implementation Files](#e-core-implementation-files)
- [IV. Evaluation & Analysis](#iv-evaluation--analysis)
  - [A. FR-01 Solar Nowcasting: Validation, Frozen Final Test, and Regime Breakdown](#a-fr-01-solar-nowcasting-validation-frozen-final-test-and-regime-breakdown)
  - [B. FR-03 Vision AI 3D Estimation: Noise Sensitivity and Hybrid Justification](#b-fr-03-vision-ai-3d-estimation-noise-sensitivity-and-hybrid-justification)
  - [C. FR-04 Adaptive Shading Control: Synthetic Benchmark and Real-Weather Replay](#c-fr-04-adaptive-shading-control-synthetic-benchmark-and-real-weather-replay)
  - [D. Automated Verification](#d-automated-verification)
- [V. Related Work & Tools](#v-related-work--tools)
- [VI. Conclusion & Discussion](#vi-conclusion--discussion)

---

## I. Introduction

### A. Motivation

Libraries, archives, museums, and sunlit reading rooms face a direct conflict between material preservation and natural daylight. Ultraviolet and high-intensity direct solar radiation cause irreversible photodegradation on books, paper archives, textiles, and artwork. Conventional motorized blinds and uniform electrochromic glass fail along two axes: they react only after glare or direct exposure has already begun, and they darken the entire window pane even when sunlight hits only one bookshelf or reading desk.

Inspired by LG Chem's Switchable Glazing Film (SGF) and LG Electronics' ThinQ ON smart-home ecosystem, our team built **SunSync-AI**. Instead of darkening an entire window, SunSync models each window as a discrete grid of individually addressable film cells (up to $10 \times 10$ per module) and traces 3D solar rays from the window to protected interior surfaces. Only the cells whose ray paths intersect a protected region above its irradiance cap are darkened.

![SunSync 3D Selective Smart-Film Shading View](../images/readme-3d-view.png)
*Live SunSync 3D prototype view: only the specific south-window film cells blocking direct sunlight toward the Grade A display case are darkened while surrounding cells remain transparent.*

Deterministic ray tracing alone leaves three practical failure modes unsolved:

1. **Coarse weather forecasts miss sub-hourly cloud transients.** Hourly Numerical Weather Prediction (NWP) feeds such as Open-Meteo lag rapid cumulus passages, producing either unshaded UV spikes or unnecessary darkening.
2. **Manual 3D room and furniture entry creates high setup friction.** Entering 3D East-North-Up (ENU) coordinates by hand is slow, while single-photo monocular depth models suffer from scale ambiguity and 20 to 40 cm localization error.
3. **Rule-based cell switching flickers under intermittent clouds.** Switching binary film cells every time forecasted Direct Normal Irradiance (DNI) crosses a threshold creates visual flicker and accelerates hardware wear.

### B. System Capabilities

SunSync-AI integrates three AIML tracks directly into our live FastAPI, SQLite, and React/Three.js web and mobile application. Table 1 summarizes the implemented capabilities on `main` (`882521e`).

| AIML / System Track | Implemented Capabilities on `main` | Key Code & Evaluation Artifacts |
| --- | --- | --- |
| **Track 1: Solar Nowcasting (`FR-01.4`)** | Frozen `fr01_kma_quantile_v1` trained on 28 days of KMA GK2A + Open-Meteo data; one-time 4-day holdout evaluated; operational shadow worker and UI daylight provenance bar | `server/backend/app/nowcast/`, `model_artifacts/fr01_kma_quantile_v1/`, `frontend/src/weatherProvenance.ts` |
| **Track 2: Vision AI 3D Sensing (`FR-03` / `FR-06`)** | In-app single-photo camera scan, EXIF & 1D `solvePnP` focal recovery, YOLOv8m (cluster `>=3` books + class-agnostic NMS + marker-host TV & outside-room FP gates) + Depth Anything V2 Metric Indoor Small (`<=1024 px` resize, proportional inner-60% ArUco calibration, 3D ENU point-cloud wall-aligned cuboid fitting, floor-referenced height, non-destructive clamping), 2D `VisionPreview` floorplan, Guided ArUco `P1~P4` fallback, strict `mode=experiment` diagnostics & 5-stage visual CLI | `server/engine/vision_ai/`, `server/backend/app/api/vision_ai.py`, `server/backend/app/services/film.py`, `frontend/src/VisionPreview.tsx`, `server/tools/ai_experiments/visualize_fr03_pipeline.py`, `docs/experiments/fr03_depthtest_experiment_report.md` |
| **Track 3: Adaptive Control (`FR-04`) & Precomputation** | Default `smooth_fr02` API stabilization, Viterbi DP `replace` mode (binary PDLC & 5-level LG SGF), ThinQ ON bridge contract, real KMA & 1-min NOAA SURFRAD replays, 10-min rolling scenario precomputation | `server/engine/fr04/`, `docs/experiments/fr04_*`, `server/backend/app/services/scenario_store.py`, `GET /api/scenes/{id}/scenario` |

---

## II. Datasets

We constructed and audited three dataset suites corresponding to our three AIML tracks. Every meteorological and replay input is tracked with explicit SHA-256 checksums, UTC timestamps, and strict temporal leakage boundaries.

### A. Solar Irradiance Nowcasting Dataset (`FR-01.4`)

To forecast sub-hourly direct solar irradiance in Seoul (`37.5714° N, 126.9658° E`), we paired 10-minute geostationary satellite observations from the Korea Meteorological Administration (KMA) GK2A API Hub with archived historical NWP runs from Open-Meteo (`ecmwf_ifs025`).

| Dataset Component | Source & Resolution | Period (UTC) | Rows / Runs | SHA-256 (Prefix) | Role in Experiment |
| --- | --- | --- | ---: | --- | --- |
| Development KMA | KMA GK2A point `DSR`, `CA` (10-min) | `2026-10-05` to `2026-11-01` (28 days) | 4,032 rows | `00e8259d5dbb` | Train, Validation, Diagnostic |
| Development Open-Meteo | `ecmwf_ifs025` single runs (6-h cadence, hourly valid time) | `2026-10-05` to `2026-11-01` | 3,192 rows (114 runs) | `484e7b97d0ba` | Forecast prior & NWP baseline |
| Final Test KMA | KMA GK2A point `DSR`, `CA` (10-min) | `2026-11-02` to `2026-11-05` (4 days) | 576 rows | `838bf1c4e0c2` | One-time frozen evaluation only |
| Final Test Open-Meteo | `ecmwf_ifs025` archived runs (6-h cadence) | `2026-11-01 12Z` to `2026-11-05 18Z` | 504 rows (18 runs) | `1f2a3f841237` | Frozen test baseline (`issued + 7h`) |

**Anti-Leakage Split and Availability Contract.** Open-Meteo forecast rows represent `preceding_hour_mean` values tied to a specific issuance run (`issued_at_utc`). To prevent look-ahead leakage, our dataset builder (`server/backend/app/nowcast/kma_model_dataset.py`) enforces `snapshot_available_at_utc = issued_at_utc + 7 hours` and requires `valid_time < next_split_start` across every partition:

- **Train (`2026-10-05` to `2026-10-28 UTC`):** 1,266 / 1,216 / 1,141 valid daylight rows for 10 / 30 / 60-minute horizons.
- **Validation (`2026-10-29` to `2026-10-30 UTC`):** 97 / 91 / 82 rows (used exclusively for model specification and bound selection).
- **Diagnostic (`2026-10-31` to `2026-11-01 UTC`):** 97 / 91 / 82 rows (report-only; excluded from model selection).
- **Frozen Final Test (`2026-11-02` to `2026-11-05 UTC`):** 180 / 168 / 151 valid daylight rows evaluated once after model freeze (`status: completed_no_retuning_allowed`).

**Data Quality Audit.** Our exploratory audit (`docs/fr01/kma_ai_analysis.md`) verified zero duplicate timestamps and zero 10-minute cadence violations. Provider missing sentinels (`nan`, `6553.5`, negative codes) and 4 out-of-range cloud-amount values (`1.1`) were normalized to null rather than imputed with zero. Nighttime rows (solar elevation below 5°) accounted for most DSR nulls (`2,512` input rows excluded across 28 days), alongside daytime satellite outages (`173` daytime nulls in the initial 14-day audit). Valid daytime DSR and cloud fraction (`CA`) exhibited a strong negative Pearson correlation of `-0.657`. Because KMA provides downward shortwave radiation (`DSR`) rather than DNI, we convert `DSR` to a solar-geometry-corrected DNI reference (`reference_type: dsr_proxy`) using `pvlib.irradiance.erbs()`.

### B. Real-Weather and 1-Minute Measured Irradiance Replay Datasets (`FR-04`)

To evaluate shading flicker suppression beyond idealized curves, we assembled three evaluation datasets for FR-04:

1. **Synthetic 4-Scenario Stress Suite (`server/tools/ai_experiments/run_fr04_adaptive_control_experiment.py`):** 5-minute steps from 08:00 to 18:00 across `Sunny`, `Partly Cloudy Rapid-Flicker`, `High Uncertainty Squall`, and `Overcast Low Irradiance` skies over a $4 \times 4$ south-facing window (1 Grade A cell, 3 Grade B cells capped at 30 W/m², 12 Grade D cells).
2. **Real KMA GK2A 10-Minute Replay (`docs/experiments/fr04_real_weather_replay.md`):** Four real autumn days from the Seoul development set (`2026-10-10` clear, mean DSR 529.2 W/m²; `2026-10-16` intermittent, 15 cloud flips; `2026-10-05` intermittent restless, 13 DSR jumps $\ge 150$ W/m²; `2026-10-18` overcast, mean DSR 112.7 W/m²; 206 weather records replayed at 5, 10, and 30-minute intervals).
3. **Real 1-Minute Measured DNI Replay (`docs/experiments/fr04_minute_replay.md`):** Because 10-minute satellite sampling smooths out sub-10-minute cloud flickers, we replayed four quality-flagged (`flag = 0`) 1-minute measured DNI days from **NOAA SURFRAD (`Penn_State_PA`)** mapped by solar time onto our Seoul scene (`2023-10-11` clear, mean DNI 829 W/m²; `2023-10-02` intermittent sunny, 32 crossings of 100 W/m² and 16 dips under 10 min; `2023-10-22` intermittent restless, 83 crossings of 100 W/m², 90 jumps $\ge 300$ W/m², and 39 dips under 10 min; `2023-10-06` overcast).

### C. Vision AI 3D Sensing Benchmark and Field Diagnostics Inputs (`FR-03` & `FR-06`)

Our Vision AI pipeline uses pretrained COCO-80 object detection weights (`yolov8m.pt`) mapped to SunSync indoor semantic classes (`bookshelf`, `desk`, `chair`, `other`, covering `chair`, `couch`, `sofa`, `bench`, `bed`, `dining table`, `desk`, `book`, `bookshelf`, `refrigerator`, `tv`, `microwave`, and `oven`) together with `depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf` (overridable via `SUNSYNC_DEPTH_MODEL`) for indoor metric depth estimation. Metric scale is anchored by a single window-mounted $6 \times 6$ `DICT_4X4_50` ArUco marker (`side_length_m = 0.6 m`, with measured physical marker size override supported in capture) paired via `POST /api/scenes/{scene_id}/marker-pairing`.

To benchmark 3D back-projection accuracy reproducibly across pixel/depth noise, `server/tools/ai_experiments/run_fr03_vision_3d_experiment.py` synthesizes a $6.0 \times 5.0 \times 2.8\text{ m}$ reading room containing a north window marker at $(3.0, 5.0, 1.6)\text{ m}$, a camera at $(3.0, 0.8, 1.4)\text{ m}$, and three ground-truth floor-standing objects: `Library Bookshelf` ($1.20 \times 0.40 \times 1.80\text{ m}$), `Oak Study Desk` ($1.40 \times 0.70 \times 0.75\text{ m}$), and `Ergonomic Chair` ($0.60 \times 0.60 \times 0.85\text{ m}$).

To validate real-world performance against tape-measured geometry, we collected the **`depthtest` real-room benchmark** (`docs/experiments/fr03_depthtest_experiment_report.md`): five smartphone photographs inside a tape-measured $3.30 \times 4.10 \times 2.50\text{ m}$ room (solid left wall at $x=0$, right corridor glass wall at $x=3.30\text{ m}$, north marker wall at $y=4.10\text{ m}$ with wall-normal depth $d_{\text{wall}} = 4.10 - y_{\text{ENU}}$). A `DICT_4X4_50` ArUco marker (`code_id = 31`, $0.60 \times 0.60\text{ m}$ black pattern inside a $1.0\text{ m}$ white-bordered film module) was displayed on a $1.95 \times 1.15\text{ m}$ TV (bottom $z = 0.85\text{ m}$) with its bottom-left black corner at $(u = 1.20\text{ m}, z = 1.20\text{ m})$ (center $(1.50, 4.10, 1.50)\text{ m}$, empirically confirmed via $z = 0.76\text{ m}$ ray-plane intersection). The five photos span four capture conditions (`original` 1x wide, `distortion` 0.5x ultrawide capturing all four desk corners, `otherside` opposite viewpoint facing the right glass wall, and `twochair` portrait 1x on Galaxy S23+ with `Orientation=6`) across `N = 6` primary tape-measured targets: the study desk ($1.20\text{ m}$ parallel $\times 1.78\text{ m}$ perpendicular $\times 0.76\text{ m}$ height, front face $1.17\text{ m}$ and center $2.06\text{ m}$ from the marker wall) across four desk views, plus the blue chair ($1.17\text{ m}$ outer face, $0.47 \times 0.46 \times 0.80\text{ m}$) and white chair ($1.80\text{ m}$ outer face, $0.47 \times 0.46 \times 0.80\text{ m}$) in `twochair`. Three of the five photos had EXIF camera tags stripped during privacy editing, enabling realistic evaluation of EXIF-free focal recovery. For ongoing field audits, `docs/examples/fr03_field_measurements_template.csv`, `server/tools/ai_experiments/compare_fr03_field_measurements.py`, and `server/tools/ai_experiments/visualize_fr03_pipeline.py` link photo identifiers, 5-stage visual overlays, dry-run API responses (`mode=experiment&apply_to_scene=false`), and tape-measured coordinates.

---

## III. Methodology

### A. End-to-End System Architecture

SunSync-AI connects three AIML modules into one live FastAPI + SQLite + React/Three.js application:

```mermaid
flowchart TD
    subgraph T2["Track 2: Hybrid Vision AI 3D Sensing (FR-03 & FR-06)"]
        P["In-App Single Photo + Window 6x6 ArUco Marker (s = 0.6 m)"]
        POSE["OpenCV solvePnP (IPPE_SQUARE): T_ENU_camera & Corner Depths"]
        DET["CompositeFurnitureDetector (YOLOv8m + book-cluster merge + cross-class NMS -> contour fallback)"]
        DEP["CompositeDepthEstimator (Depth Anything V2 Metric Indoor Small -> linear fallback)"]
        CUB["Calibrated 3D Cuboids + Quality & Diagnostics + VisionPreview 2D Plan"]
        ARUCO["Stage 2 Guided P1-P4 ArUco Corner Refinement (for uncertain items)"]
        P --> POSE
        P --> DET
        P --> DEP
        POSE --> CUB
        DET --> CUB
        DEP --> CUB
        CUB --> ARUCO
    end

    subgraph T1["Track 1: Sub-Hourly Probabilistic Solar Nowcasting (FR-01.4)"]
        KMA["KMA GK2A 10-min (DSR, CA)"]
        OM["Open-Meteo ECMWF IFS025 (+7h availability delay)"]
        NORM["pvlib Erbs DSR-to-DNI Prior & Clear-Sky Normalization"]
        LQR["Frozen Linear Quantile Regression (P10 / P50 / P90, 10 / 30 / 60 min)"]
        POL["Operational Policy (10 & 30 min AI V1, 60 min Open-Meteo, UI Provenance)"]
        KMA --> NORM
        OM --> NORM
        NORM --> LQR
        LQR --> POL
    end

    subgraph T3["Track 3: Adaptive Control & Rolling Precomputation (FR-02 / FR-04)"]
        SCENE["3D Scene Geometry (_GEOMETRY_CACHE) + weather_fingerprint (SHA-256)"]
        SNAP["10-min Rolling Worker & Local Edit Invalidation (SQLite scenario_snapshots)"]
        CTRL["FR-04 Control: smooth_fr02 (default) / replace Viterbi DP (PDLC & 5-lvl LG SGF)"]
        OUT["Read-First GET /api/scenes/:id/scenario + LG ThinQ ON (LG_THINQ_ON_SGF_V1) & 3D UI"]
        SCENE --> SNAP
        SNAP --> CTRL
        CTRL --> OUT
    end

    CUB --> SCENE
    ARUCO --> SCENE
    POL --> SCENE
```

### B. Track 1: Sub-Hourly Probabilistic Solar Nowcasting (`FR-01.4`)

**Algorithm Choice.** Auditing our collected 28-day dataset (1,141 to 1,266 daylight rows per horizon) showed that high-capacity nonlinear models risk overfitting a single season. We selected **physically bounded Linear Quantile Regression (`P10`, `P50`, `P90`)** trained in clear-sky-normalized space via `scipy.optimize` (`L-BFGS-B`) for frozen production model `fr01_kma_quantile_v1`. This formulation guarantees zero quantile crossings after monotone clipping, requires zero GPU resources, and serializes into 5 KB deterministic JSON artifacts (`maximum_dni_absolute_error_W_m2 = 0.0` in round-trip verification).

**Feature Engineering and Target Formulation.** Rather than regressing raw irradiance directly (which confounds solar geometry with cloud transmittance), we predict the dimensionless clear-sky ratio $k_q = \text{DNI}_q / \text{DNI}_{\text{clear}}$ from a fixed 5-feature vector:

1. `intercept`: constant `1.0`.
2. `direct_prior_factor`: lead-time weighted blend of the Erbs-converted KMA observation and the available Open-Meteo forecast:
   $$w_{\text{obs}}(h) = \exp\!\left(-\frac{h / 60}{1.5}\right), \quad x_{\text{prior}} = w_{\text{obs}} \cdot \frac{\text{KMA\_DNI}_{\text{prior}}}{\text{DNI}_{\text{clear}}} + (1 - w_{\text{obs}}) \cdot \frac{\text{OM\_DNI}}{\text{DNI}_{\text{clear}}}$$
3. `open_meteo_factor`: $\text{OM\_DNI} / \text{DNI}_{\text{clear}}$.
4. `cloud_transmittance`: $1 - \text{cloud\_cover\_pct} / 100$.
5. `advection_impact`: fixed to `0.0` in V1 (reserved in the frozen schema for future GK2A 2D cloud-motion patch advection).

For each quantile $\tau \in \{0.1, 0.5, 0.9\}$, `L-BFGS-B` minimizes pinball loss subject to physically motivated box constraints (`intercept` $\in [-0.5, 0.5]$, `direct_prior_factor` and `open_meteo_factor` $\in [0.0, 1.5]$, `cloud_transmittance` $\in [-0.5, 0.5]$). Postprocessing enforces physical bounds and monotonicity:

$$\hat{k}_{10} = \text{clip}(f_{10}(x), 0, 1.05), \quad \hat{k}_{50} = \max(\hat{k}_{10}, \text{clip}(f_{50}(x), 0, 1.05)), \quad \hat{k}_{90} = \max(\hat{k}_{50}, \text{clip}(f_{90}(x), 0, 1.05))$$

Table 2 reports the exact frozen weights stored in `server/backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1/horizon_*/model.json`.

| Horizon | Quantile | `intercept` | `direct_prior_factor` | `open_meteo_factor` | `cloud_transmittance` | `advection_impact` |
| :---: | :---: | ---: | ---: | ---: | ---: | ---: |
| **10 min** | P10 | -0.1318 | 0.5830 | 0.0000 | 0.3843 | 0.0000 |
| **10 min** | P50 | -0.0054 | 1.0085 | 0.0000 | 0.0158 | 0.0000 |
| **10 min** | P90 | 0.3639 | 0.7567 | 0.0000 | -0.0792 | 0.0000 |
| **30 min** | P10 | -0.1297 | 0.2774 | 0.1219 | 0.4631 | 0.0000 |
| **30 min** | P50 | -0.0022 | 0.8972 | 0.0172 | 0.1204 | 0.0000 |
| **30 min** | P90 | 0.4692 | 0.7096 | 0.0000 | -0.0898 | 0.0000 |
| **60 min** | P10 | -0.1768 | 0.0000 | 0.5084 | 0.3467 | 0.0000 |
| **60 min** | P50 | -0.0164 | 0.8245 | 0.0000 | 0.2141 | 0.0000 |
| **60 min** | P90 | 0.5000 | 0.6975 | 0.0000 | -0.0515 | 0.0000 |

At 10 minutes, `P50` places nearly unit weight (`1.0085`) on `direct_prior_factor` (recent KMA persistence), whereas at 60 minutes, `P10` shifts away from persistence (`0.0000`) onto `open_meteo_factor` (`0.5084`) and `cloud_transmittance` (`0.3467`). For 60-minute `P90`, we kept the `[-0.5, 0.5]` intercept bound after it beat `[-0.75, 0.75]` on validation P90 pinball loss (`42.14` vs. `42.51 W/m²`) and mean interval width (`452.96` vs. `493.81 W/m²`).

**Operational Serving Policy.** Informed by the final-test crossover at 60 minutes (Section IV.A), `server/backend/app/nowcast/kma_operational.py` serves frozen V1 quantiles for 10 and 30-minute horizons, defaults 60 minutes to Open-Meteo while recording V1 in shadow mode, falls back to Open-Meteo when KMA observations are missing or older than 20 minutes, and surfaces explicit daylight forecast provenance spans in the UI via `frontend/src/weatherProvenance.ts`.

```mermaid
flowchart LR
    IN["Latest KMA GK2A Obs + Open-Meteo Snapshot (issued_at + 7h)"] --> FRESH{"KMA valid & age <= 20 min?"}
    FRESH -- "Yes" --> HZ{"Forecast Horizon"}
    HZ -- "10 min / 30 min" --> V1["Serve Frozen V1 Quantiles (P10, P50, P90)"]
    HZ -- "60 min" --> OM60["Serve Open-Meteo DNI (Log V1 in Shadow)"]
    FRESH -- "No (Missing / Stale)" --> OMF{"Open-Meteo Available?"}
    OMF -- "Yes" --> OMFB["Fallback to Open-Meteo DNI"]
    OMF -- "No" --> UNAV["Return unavailable (No Arbitrary Interpolation)"]
    V1 --> PROV["UI Daylight Provenance Bar (weatherProvenance.ts)"]
    OM60 --> PROV
    OMFB --> PROV
```

### C. Track 2: Hybrid Vision AI 3D Sensing and Guided ArUco Fallback (`FR-03` & `FR-06`)

Single-photo monocular depth networks lack reliable absolute scale across varying smartphone focal lengths (`0.5x` ultrawide vs. `1x` wide). Following our real-room `depthtest` ablation findings (Section IV.B), SunSync anchors every photo to the paired $6 \times 6$ window ArUco marker (`s = 0.6 m`, `DICT_4X4_50`) through a four-stage calibrated 3D reconstruction pipeline:

1. **EXIF-Aware Intrinsics, 1D Focal Recovery, and Metric Camera Pose (`solvePnP`).** `decode_image_base64` and `POST /api/scenes/{scene_id}/vision-furniture-photo` apply `PIL.ImageOps.exif_transpose` and extract `FocalLengthIn35mmFilm` (or `FocalLength` with sensor crop, plus optional `fov_deg` / `focal_length_35mm` overrides) instead of hardcoding $f_x = 0.9 \max(w, h)$. When EXIF is stripped by an editor or browser, the pipeline initializes at a smartphone 1x prior ($0.68 \max(w, h)$) and, whenever `SOLVEPNP_IPPE_SQUARE` reprojection error exceeds `2.5 px` or the recovered camera lies outside `room_floor_poly.buffer(0.25)`, runs a fast 1D focal search over $f_x / \max(w, h) \in [0.35, 0.90]$ minimizing reprojection error subject to keeping the camera inside the room. Given corners $p_i = (u_i, v_i)$ and resolved intrinsics $K$, `solvePnP` solves $(R_{c,m}, t_{c,m})$ and recovers camera ENU pose:
   $$R_{\text{ENU}, c} = R_{\text{ENU}, m} R_{c, m}^T, \quad t_{\text{ENU}, c} = t_{\text{ENU}, m} - R_{\text{ENU}, c} t_{c, m}$$
2. **Proportional Single-Marker Depth Calibration.** Fitting a 2-parameter affine line ($\alpha d + \beta$) to four coplanar corners of a single `0.60 m` marker overfits marker-plane noise and produces unstable intercepts ($\beta \in [-2.12, +0.92]\text{ m}$) when extrapolated toward foreground furniture. `calibrate_monocular_depth` therefore defaults to proportional scaling ($\beta = 0$, $\alpha = Z_{\text{center}} / \text{median}(d_{\text{inner60}})$) sampled over the interior 60% of the marker quadrilateral, permitting 2-parameter affine fitting only when multi-depth anchors span $\text{ptp}(Z_{\text{true}}) \ge 1.5\text{ m}$.
3. **Deterministic FP Filtering, 3D ENU Point-Cloud Cuboid Fitting, and Non-Destructive Clamping.** `CompositeFurnitureDetector` runs `YoloV8DetectorAdapter` (`conf_thresh = 0.25`) with class-agnostic NMS (`IoU >= 0.65`), requires `>= 3` proximate `"book"` boxes in `_merge_book_clusters` before emitting `"bookshelf"` (eliminating lone laptop-as-book false positives), excludes `tv`/`other` boxes containing `>= 80%` of the ArUco marker box (eliminating TV marker-host detections), and discards any back-projected center falling outside `room_floor_poly.buffer(0.25)` before clamping (preventing hallway furniture seen through glass walls from being pulled inside). `CompositeDepthEstimator` runs `Depth-Anything-V2-Metric-Indoor-Small-hf` (resized to `max_side <= 1024 px` under `torch.inference_mode()` and upsampled to full resolution; depth now also runs when clients supply `request.detections`). Rather than unprojecting a single scalar depth with camera-locked yaw and fixed `0.70 m` desk thickness, `backproject_box_to_3d_cuboid` unprojects a strided 3D ENU point cloud (`enu_pointcloud_bbox`, inner 80% box, stride `4 px`), applies class-aware foreground filtering ($z_{\text{ENU}} \ge 0.10\text{ m}$, nearest 65% camera depth for sparse chairs, $z \in [0.20, 1.30]\text{ m}$ for desks), aligns yaw to the dominant wall of `room_floor_poly`, fits robust `p5..p95` width/depth extents, estimates floor-referenced height ($p_{\text{top,ENU},z}$ / `p90 Z`) for occluded floor-standing furniture, and applies non-destructive `clamp_cuboid_to_room` (minimal inward translation preserving dimensions unless exceeding room span).
4. **2-Stage Coarse-to-Fine Hybrid UX and Strict Diagnostics.** `_assess_estimation_quality` grades each item (`high`, `medium`, or `low_needs_aruco`), reports perpendicular window-wall distance (`d_window_m`) rather than raw optical depth in capture guidance, and records `resolved_fx_px`, `intrinsics_source`, reprojection error, calibration residuals, and dimension provenance in `diagnostics` (`mode=experiment` enforces real models with HTTP 503 on failure). On mobile and web, users inspect 2D footprints on `VisionPreview.tsx` and either accept the AI draft or transition directly into Stage-2 guided `P1~P4` ArUco corner measurement in `SpatialCapture.tsx`.

```mermaid
flowchart LR
    IMG["In-App Single Photo + Window ArUco Marker"] --> S1["Stage 1: EXIF / 1D-Search Intrinsics + YOLOv8m (FP Gates) + DA-V2 Metric Small (<=1024px)"]
    S1 --> CAL["solvePnP Pose + Proportional Inner-60% ArUco Calibration (beta=0)"]
    CAL --> PC["3D ENU Point-Cloud Wall-Aligned Cuboid Fitting + Floor Height + Non-Destructive Clamp"]
    PC --> PREV["VisionPreview 2D Room Plan + Diagnostics (d_window_m Guidance)"]
    PREV --> DEC{"Quality high & user accepts draft?"}
    DEC -- "Yes (Direct Draft)" --> SAVE["Apply AI Draft to Scene & Sync UI Space"]
    DEC -- "No (low_needs_aruco / Refine)" --> S2["Stage 2: Walk to Estimated ENU Position & Capture ArUco at P1-P4"]
    S2 --> SAVE
```

### D. Track 3: Uncertainty-Aware Adaptive Shading Control and Scenario Precomputation (`FR-04`)

**Two Explicit Control Modes.** We separate production stabilization from experimental continuous-dimming optimization (`docs/fr04/adaptive_control_lg_sgf.md`):

- **Default Production Mode (`mode: "smooth_fr02"`):** Applies deterministic stabilization directly to the FR-02 binary plan without ever making any cell brighter than FR-02:
  - `FR-04.1` Gap Filling: closes transparent gaps strictly shorter than `min_gap_seconds` (default `600 s`).
  - `FR-04.2` Minimum Holding Time: holds darkened states for `brightening_hold_seconds` (`600 s`) before brightening, while permitting immediate darkening.
  - `FR-04.3` Dynamic Hysteresis: widens the brightening margin in proportion to forecast spread $(I_{q90} - I_{q10}) / (I_{q50} + 1)$.
- **Experimental Optimization Mode (`mode: "replace"`):** Uses Viterbi Dynamic Programming per window cell to choose opacity trajectories $u_{1..T}$ (binary `{0, 1}` for PDLC or 5-level continuous dimming `{0, 0.25, 0.5, 0.75, 1.0}` for LG SGF) minimizing:
  $$\mathcal{L}(u_{1..T}) = \sum_{t=1}^T \left( w_{\text{damage}} \cdot \text{Risk}_t(u_t) + w_{\text{daylight}} \cdot u_t^2 + w_{\text{flicker}} \cdot |u_t - u_{t-1}| \right)$$
  Grade A artifacts enforce a hard constraint ($u_t \ge 0.99$ whenever illuminated), while Grades B and C evaluate transmitted irradiance against risk-averse effective DNI $I_{\text{eff}, t} = I_{q50, t} + \kappa_{\text{grade}} \max(0, I_{q90, t} - I_{q50, t})$ with $\kappa_B = 1.2 > \kappa_C = 0.8$. Cell opacity matrices serialize to our internal LG ThinQ ON bridge schema (`LG_THINQ_ON_SGF_V1`) via `POST /api/scenes/{scene_id}/thinq-hub-sync`.

**Rolling Time-Bucket & Event-Driven Scenario Precomputation.** Opening a room detail screen previously triggered three redundant 3D ray-tracing requests (`sunpath-predictions`, `shading-plans`, `adaptive-shading-plan`), causing 15-second frontend timeouts on a single-worker Uvicorn server. We refactored the serving architecture (`docs/overview/scenario-precomputation.md`) to decouple slow-changing 3D geometry (`_GEOMETRY_CACHE`) from weather updates (`weather_fingerprint`, a 16-character SHA-256 hash of scene weather, Open-Meteo snapshot IDs, and active AI nowcast runs). A 10-minute rolling UTC worker (`operational_nowcast_worker.py`) precomputes and persists combined bundles in SQLite (`scenario_snapshots`), space edits immediately invalidate stale snapshots, single-flight locks coalesce concurrent cold-cache reads, and clients fetch all three outputs in one read-first call (`GET /api/scenes/{scene_id}/scenario`).

```mermaid
flowchart LR
    LOC["Local Event: Space / Furniture / Region Edit"] --> INV["invalidate_scene_scenarios (Delete Stale Snapshots)"]
    GLOB["Global Event: 10-min UTC Rolling Worker"] --> FP["Compute weather_fingerprint (SHA-256)"]
    FP --> HIT{"Snapshot Key & Fingerprint Match?"}
    HIT -- "Yes" --> REUSE["Reuse Persisted SQLite scenario_snapshots"]
    HIT -- "No (or Cold Read)" --> LOCK["Single-Flight Lock + _GEOMETRY_CACHE Ray Trace"]
    LOCK --> COMP["Compute FR-01 + FR-02 + FR-04 Bundle & Persist"]
    COMP --> API["Read-First GET /api/scenes/:id/scenario"]
    REUSE --> API
    INV --> LOCK
    API --> UI["Web/Mobile 3D Preview & LG ThinQ ON Bridge"]
```

### E. Core Implementation Files

| Module | Repository Paths | Responsibility |
| --- | --- | --- |
| Solar ML Nowcasting | `server/backend/app/nowcast/kma_linear_model.py`, `kma_model_dataset.py`, `kma_operational.py`, `model_artifacts/fr01_kma_quantile_v1/` | Leak-free dataset construction, L-BFGS-B quantile training, frozen JSON serving, shadow worker |
| Vision AI 3D Engine | `server/engine/vision_ai/protocols.py`, `server/engine/vision_ai/pipeline.py`, `server/backend/app/api/vision_ai.py`, `server/backend/app/config.py` | YOLOv8 + Depth Anything V2 singletons, `SUNSYNC_VISION_AI`, `mode=experiment` diagnostics, IPPE_SQUARE pose, quality & guidance |
| Vision Capture UI | `frontend/src/SpatialCapture.tsx`, `frontend/src/VisionPreview.tsx`, `frontend/src/visionGeometry.ts`, `frontend/src/measureApi.ts` | In-app single-photo scan, 2D room-plan preview with protected faces, guided `P1~P4` ArUco handoff |
| Adaptive Control & Precomputation | `server/engine/fr04/controller.py`, `server/engine/fr04/stabilizer.py`, `server/backend/app/api/adaptive_control.py`, `server/backend/app/services/scenario_store.py` | `smooth_fr02`, Viterbi DP (`PDLC` / `LG SGF`), `LG_THINQ_ON_SGF_V1`, 10-min rolling snapshots |
| Experiment & Replay CLI | `server/tools/ai_experiments/evaluate_fr01_kma_final_test.py`, `run_fr03_vision_3d_experiment.py`, `visualize_fr03_pipeline.py`, `compare_fr03_field_measurements.py`, `run_fr04_real_weather_replay.py`, `run_fr04_minute_replay.py` | Frozen final-test evaluation, 3D noise sweep, stage-by-stage Vision AI PNG visualizer, field tape-measure comparison, real KMA & SURFRAD API replays |

---

## IV. Evaluation & Analysis

All figures below are generated programmatically from repository experiment artifacts (`docs/ai_applications/generate_figures.py`).

### A. FR-01 Solar Nowcasting: Validation, Frozen Final Test, and Regime Breakdown

![FR-01 Frozen Final Test Horizon Error and Coverage](figures/fig1_fr01_horizon_error.svg)
*Figure 1: Frozen V1 (`fr01_kma_quantile_v1`) one-time Final Test (`2026-11-02` to `2026-11-05 UTC`) point forecast MAE/RMSE versus Open-Meteo ECMWF IFS025 (a) and P10-P90 Prediction Interval Coverage Probability (PICP) with mean interval width (b).*

Table 3 compares our frozen V1 quantile model against the archived Open-Meteo baseline on identical valid timestamps across both the Validation split (`Oct 29-30`) and the one-time Frozen Final Test (`Nov 2-5`).

| Evaluation Split | Horizon | Valid Rows | KMA V1 P50 MAE (W/m²) | Open-Meteo MAE (W/m²) | MAE Change | KMA V1 P50 RMSE (W/m²) | Open-Meteo RMSE (W/m²) | P50 Pinball (W/m²) | PICP (P10-P90) | Mean Width (W/m²) |
| --- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Validation** (`Oct 29-30`) | 10 min | 97 | **62.29** | 163.94 | -62.0% | **122.23** | 208.71 | 31.14 | 82.5% | 272.72 |
| **Validation** (`Oct 29-30`) | 30 min | 91 | **83.62** | 159.20 | -47.5% | **133.15** | 205.58 | 41.81 | 81.3% | 381.67 |
| **Validation** (`Oct 29-30`) | 60 min | 82 | **117.43** | 157.34 | -25.4% | **181.84** | 205.94 | 58.71 | 81.7% | 452.96 |
| **Final Test** (`Nov 2-5`) | 10 min | 180 | **73.16** | 167.19 | **-56.2%** | **131.98** | 214.63 | 36.58 | **81.1%** | 244.41 |
| **Final Test** (`Nov 2-5`) | 30 min | 168 | **122.30** | 167.16 | **-26.8%** | **183.05** | 216.10 | 61.15 | 73.8% | 361.07 |
| **Final Test** (`Nov 2-5`) | 60 min | 151 | 177.54 | **173.25** | +2.5% | 231.59 | **224.98** | 88.77 | 71.5% | 453.31 |

**Key Findings from FR-01 Evaluation:**

1. **Strong sub-hourly advantage at 10 and 30 minutes, NWP parity by 60 minutes.** On the untouched Final Test, KMA V1 cuts MAE by **56.2%** at 10 minutes (`73.16` vs. `167.19 W/m²`, bias `-2.57` vs. `-90.16 W/m²`) and by **26.8%** at 30 minutes (`122.30` vs. `167.16 W/m²`), with 10-minute PICP hitting `81.1%` against the nominal `80%` target. By 60 minutes, point-observation persistence decays without 2D cloud-advection vectors and Open-Meteo slightly outperforms V1 (`173.25` vs. `177.54 W/m²`). This crossover directly determined our operational serving split (V1 at 10 and 30 minutes, Open-Meteo at 60 minutes).
2. **Effect of extending the training window.** Expanding the training set from 10 days (`503` rows, Oct 19 start) to 24 days (`1,266` rows, Oct 5 start) kept 10-minute validation PICP identical at `82.5%` while tightening mean interval width by **13.5%** (`315.23` down to `272.72 W/m²`) and lowering P10 pinball loss by **15.4%** (`14.84` down to `12.55 W/m²`).
3. **Regime breakdown.** At the 10-minute horizon, clear skies (`0-20%` cloud cover, `n = 37`) yield `20.37 W/m²` MAE vs. `186.06 W/m²` for Open-Meteo, and overcast skies (`70-100%`, `n = 53`) yield `80.68` vs. `150.53 W/m²` (`90.6%` PICP). Accuracy degrades most in broken/partly cloudy transitions (`20-70%`, `n = 7`: MAE `144.58 W/m²`, PICP `57.1%`) and low solar elevation (`5-20°`, `n = 21`: PICP `52.4%`), confirming that spatial cloud-motion patches are the main lever for V2.

![FR-01 10-Minute Quantile Nowcast Time Series on Holdout Day 2026-11-03](figures/fig2_fr01_timeseries_10min.svg)
*Figure 2: 10-minute probabilistic nowcast versus Open-Meteo ECMWF IFS025 on holdout Final Test day `2026-11-03` (`n = 49` valid daylight observations, KST). KMA V1 tracks the sharp midday cloud drop (13:20-14:10 KST) that the hourly NWP run misses completely (`77.7` vs. `169.4 W/m²` daily MAE).*

### B. FR-03 Vision AI 3D Estimation: Noise Sensitivity and Hybrid Justification

![FR-03 Vision AI 3D Estimation Error Across Noise Levels](figures/fig3_fr03_vision_3d_error.svg)
*Figure 3: Per-item 3D center localization error (a) and bounding-box dimension error (b) from `run_fr03_vision_3d_experiment.py` across 0%, 2%, and 5% simulated detection/depth noise.*

Table 4 summarizes our 3D estimation sweep across noise levels.

| Simulated Noise | ArUco Camera Pose Error | Depth Calibration | Bookshelf Center / Dim Err | Desk Center / Dim Err | Chair Center / Dim Err | Mean 3D Center Err | Mean 3D Dim Err | Yaw Err |
| :---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **0.0%** | 0.0 cm | $Z = 1.0 d + 0.0$ | 20.4 / 22.4 cm | 35.0 / 44.2 cm | 27.5 / 19.7 cm | **27.6 cm** | **28.8 cm** | 0.0° |
| **2.0%** | 0.0 cm | $Z = 1.0 d + 0.0$ | 22.8 / 23.7 cm | 30.4 / 40.3 cm | 29.6 / 20.3 cm | **27.6 cm** | **28.1 cm** | 0.0° |
| **5.0%** | 0.0 cm | $Z = 1.0 d + 0.0$ | 26.5 / 25.6 cm | 23.6 / 34.6 cm | 32.8 / 21.4 cm | **27.6 cm** | **27.2 cm** | 0.0° |

**Analysis.** Even at 0% pixel noise, single-view 2D bounding-box unprojection exhibits a structural error floor of **27.6 cm** in 3D center position and **28.8 cm** in dimensions (especially on wide horizontal furniture like the Oak Study Desk, `44.2 cm` dimension error), because a single frontal perspective box conflates front and rear depth extents and relies on class thickness priors (`0.40 m` bookshelf, `0.70 m` desk, `0.55 m` chair). Conversely, the window ArUco marker recovers camera pose with `0.0 cm` error. This gap directly justifies our **2-Stage Coarse-to-Fine Hybrid architecture**: single-photo Vision AI rapidly populates candidate furniture and protected faces on `VisionPreview.tsx`, while `_assess_estimation_quality` and `diagnostics` flag uncertain items and guide the user to refine exact `P1~P4` corners with the ArUco marker when centimeter-level shading boundaries are required.

**Real-Photo Stage-by-Stage Diagnostic Experiment & Depth Model Upgrade.** Running our local 5-stage visual inspection tool (`server/tools/ai_experiments/visualize_fr03_pipeline.py`) on two real indoor smartphone photographs containing a desk chair and an ArUco marker (`ID 27` rear-facing chair and `ID 4` front-facing chair, `--marker-side-m 0.13`) uncovered three practical failure modes that synthetic benchmarks alone did not expose:

1. **Disparity Direction & Affine-Shift Compression (`Depth-Anything-V2-Small-hf`):** The relative checkpoint predicts affine-invariant inverse depth (disparity: near pixels have larger values). Without inversion, the foreground chair mapped behind the background marker (`Z = 8.34 m`, `4.08 m` clamped width). Inverting float32 `predicted_depth` (`1 / disparity`) restored near-far ordering (`0.37 m` and `0.43 m`), but unknown affine shift under single-marker proportional calibration (`beta = 0`) still compressed near-chair distance by roughly 2.5x, shrinking pinhole width (`w_px / fx * Z`) to `0.20-0.22 m`.
2. **Cross-Class Duplicate Detections:** On the rear-facing chair photo, YOLOv8m emitted both `tv` (`48%`, mapped to `other`) and `chair` (`30%`) on nearly identical bounding boxes (`IoU = 0.97`). Adding class-agnostic NMS (`_deduplicate_overlapping_detections`, `IoU >= 0.65`) eliminated the duplicate appliance ghost while preserving genuine high-confidence detections.
3. **Marker Overlap False Exclusion:** On the front-facing chair photo, YOLOv8m detected the chair at `92.5%` confidence, but its top-left corner touched `0.75%` of the chair area (`53%` of the small marker area), causing the original `_overlaps_any_box` rule (`inter / min(b_area, e_area) > 0.3`) to discard the chair. Changing the exclusion criterion to candidate-box coverage (`inter / b_area > 0.4`) resolved the false drop.

Switching the default depth checkpoint (same `99.2 MB` Small backbone) to **`depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf`** removed the affine shift error directly, yielding consistent ArUco scale factors across both photos (`alpha = 0.644` vs. `0.641`, within `0.4%`) and realistic chair dimensions (`0.47 x 0.55 x 0.80 m` front-facing) while running **4 to 8x faster** on CPU:

| Real Indoor Photo | Metric (`--marker-side-m 0.13`) | `Depth-Anything-V2-Small-hf` (1/disp) | `Metric-Indoor-Small-hf` (New Default) | Change |
| --- | --- | ---: | ---: | ---: |
| **Rear-Facing Chair (`ID 27`)** | Depth inference latency (CPU) | `2,178 ms` | **`541 ms`** | **4.0x faster** |
| | ArUco calibration residual RMSE | `3.90 cm` | **`0.92 cm`** | **-76.4%** |
| | Chair distance `Z` / dims (`W x D x H`) | `0.37 m` / `0.20 x 0.55 x 1.44 m` | **`0.81 m` / `0.44 x 0.55 x 0.44 m`** | Width +2.2x |
| **Front-Facing Chair (`ID 4`)** | Depth inference latency (CPU) | `2,178 ms` | **`275 ms`** | **7.9x faster** |
| | ArUco calibration residual RMSE | `12.96 cm` | **`3.61 cm`** | **-72.1%** |
| | Chair distance `Z` / dims (`W x D x H`) | `0.43 m` / `0.22 x 0.55 x 1.19 m` | **`0.93 m` / `0.47 x 0.55 x 0.80 m`** | Realistic chair dims |

**Real-Room Tape-Measured Benchmark (`depthtest`, 11 Depth Model Variants Across DA-V2 and Non-DA Families).** To test generalization beyond two close-range chairs, we benchmarked six Depth Anything V2 variants and five non-Depth-Anything monocular depth families (`Apple Depth Pro`, `Intel DPT-Hybrid-MiDaS`, `Intel DPT-SwinV2-Tiny`, `Intel ZoeDepth-NYU`, and `GLPN-NYU`) on our tape-measured $3.30 \times 4.10 \times 2.50\text{ m}$ room (`docs/experiments/fr03_depthtest_experiment_report.md`). Table 4b summarizes apples-to-apples primary-target accuracy (EXIF `fx`, Oracle 2D boxes, `N = 6` primary instances: 4 desks + 2 chairs in `twochair`) alongside CPU/MPS latency and isolated peak RSS against our 1.9 GB AWS CPU server budget.

![FR-03 Real-Room Cross-Family Monocular Depth Benchmark and Ablation Waterfall](figures/fig5_fr03_depthtest_benchmark.png)
*Figure 5: (a) Cross-family monocular depth accuracy (wall-normal Front MAE under EXIF `fx` + Oracle 2D, `N = 6` primary targets) versus isolated peak RSS against the 1.9 GB AWS CPU server budget; (b) Step-by-step ablation waterfall from the pre-fix baseline (`S0`) to the live verified post-fix production pipeline (`-65.3%` front MAE, `+69.0%` desk 2D footprint IoU).*

| Model (Family / Output Type) | Params / Disk | Calibration Mode | Front MAE | Center MAE | Marker RMSE | Warm Latency | Peak RSS | 1.9 GB CPU Server |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `DA-V2 Metric-Base` (DA / Metric) | `97.5M` / `389.9 MB` | Prop (`beta = 0`) | **`0.216 m`** | `0.203 m` | `0.091 m` | `754 ms` (CPU) | `3,029 MB` | Infeasible (OOM) |
| **`DA-V2 Metric-Small` (Selected)** | `24.8M` / `99.2 MB` | **Prop (`beta = 0`)** | **`0.244 m`** | `0.205 m` | `0.075 m` | **`321 ms`** (CPU) | **`1,810 MB`** | **Standalone Feasible** |
| `Apple Depth Pro` (Non-DA / Metric+FOV) | `952.0M` / `1,904.2 MB` | Raw / ArUco Calib | `0.262 / 0.268 m` | **`0.195 / 0.185 m`** | **`0.016 m`** | `2,568 ms` (MPS; CPU `>195 s`) | `5,887 MB` | Infeasible (GPU req.) |
| `DA-V2 Metric-Large` (DA / Metric) | `335.3M` / `1,341.3 MB` | Prop (`beta = 0`) | `0.266 m` | `0.230 m` | `0.071 m` | `3,481 ms` (CPU) | `5,542 MB` | Infeasible (OOM) |
| `DA-V2 Base (1/disp)` (DA / Relative) | `97.5M` / `389.9 MB` | Prop (`beta = 0`) | `0.330 m` | `0.247 m` | `0.139 m` | `771 ms` (CPU) | `3,059 MB` | Infeasible (OOM) |
| `Intel DPT-Hybrid-MiDaS` (Non-DA / Rel.) | `122.4M` / `489.7 MB` | ArUco Affine | `0.332 m` | `0.290 m` | `0.101 m` | `509 ms` (CPU) | `2,274 MB` | Infeasible (OOM risk) |
| `DA-V2 Small (1/disp)` (DA / Relative) | `24.8M` / `99.2 MB` | Prop (`beta = 0`) | `0.348 m` | `0.253 m` | `0.156 m` | `324 ms` (CPU) | `1,806 MB` | Standalone Feasible |
| `GLPN-NYU` (Non-DA / Metric NYU) | `61.2M` / `245.0 MB` | Raw / Affine | `0.416 / 0.545 m` | `0.287 / 0.459 m` | `0.132 m` | `2,271 ms` (CPU) | `1,851 MB` | Marginal / Slow |
| `Intel DPT-SwinV2-Tiny` (Non-DA / Rel.) | `40.9M` / `163.8 MB` | ArUco Affine | `0.450 m` | `0.341 m` | `0.032 m` | **`167 ms`** (CPU) | **`1,336 MB`** | Feasible (Low acc.) |
| `Intel ZoeDepth-NYU` (Non-DA / Metric) | `343.8M` / `1,375.2 MB` | ArUco Affine | `0.469 m` | `0.325 m` | `0.046 m` | `1,173 ms` (CPU) | `4,186 MB` | Infeasible (OOM) |
| `DA-V2 Metric-Small` (Pre-Fix Affine) | `24.8M` / `99.2 MB` | Pre-Fix Affine | `0.562 m` | `0.384 m` | `0.039 m` | `321 ms` (CPU) | `1,810 MB` | Baseline (Buggy fit) |

Three decisive engineering lessons emerged from the cross-family benchmark and condition-by-condition ablation:

1. **Calibration and geometry bugs dominated neural model choice.** Upgrading `DA-V2 Metric-Small` to `Metric-Large` (10.8x slower, 3.1x RAM) under the pre-fix 4-corner affine fit saved only `0.093 m` (`0.562 -> 0.469 m`), whereas switching `Metric-Small` to single-marker proportional calibration (`beta = 0`) slashed front MAE by **56.6%** (`0.562 -> 0.244 m`, or `0.253 m` using the inner-60% marker quad median). No lightweight non-DA model beat `DA-V2 Metric-Small` under the 1.9 GB CPU budget (`DPT-SwinV2-Tiny` was fastest at `167 ms` and `1,336 MB` RSS but lost nearly 2x accuracy at `0.450 m`). `Apple Depth Pro` established the FOV-aware metric ceiling (`0.185 m` center MAE, `1.57 cm` marker residual, and **6.13% mean absolute focal-length error without EXIF** versus `72.67%` for hardcoded `0.9*max(w,h)`), confirming that FOV-aware depth solves 0.5x scale drift, though its `5,887 MB` RSS and `>195 s` CPU latency require GPU hosting.
2. **Capture-condition root causes (`1x`, `0.5x`, `otherside`, `twochair`).** On `0.5x` ultrawide (`distortion`), hardcoded `fx = 3628.8 px` vs. true `1568.0 px` (`2.31x`) caused `3.509 m` 3D camera drift and `22.0-23.5 deg` rotation error, pushing `0/2` cameras inside the room. A radial distortion sweep (`k1 in [-0.20, +0.10]`) proved `k1 = 0.0` minimizes IPPE reprojection (`0.31 px` and `0.19 px`), confirming modern smartphone ISPs already rectify barrel distortion; once EXIF `fx` and proportional calibration were restored, `0.5x` ultrawide outperformed `1x` (`0.088 m` vs. `0.350 m` front MAE under `S3`) because all four desk corners remained uncropped. On `otherside`, four hallway chairs visible through the right glass wall (`x = 3.63-8.10 m`) were previously dragged `1.02-5.26 m` inside the room by `clamp_cuboid_to_room`, while occluded chair backrests behind the desk yielded `0.421 m` height MAE under `p_top - p_bot` vs. `0.033 m` under floor-referenced `p_top_enu[2]`. Raising YOLO `conf_thresh` from `0.25` to `0.35` cut desk recall in half (`4/4 -> 2/4`) while leaving TV and hallway FPs intact, proving deterministic geometric/container filters are required.
3. **Live Post-Fix Production Verification (`post_fix_eval.json`).** Implementing all P0 and P1 fixes in `server/engine/vision_ai/` and `server/backend/app/` and running the actual modified `VisionRegistrationPipeline` across all five `depthtest` photos delivered **0 MB additional neural model weight** and major accuracy gains (Table 4c, Figures 6 and 7). Even on EXIF-stripped `0.5x` `distortion_otherside`, `pnp_1d_focal_search` recovered `fx = 1584.6 px` (`1.06%` from true `1568.0 px`, `reproj = 0.05 px`) completely automatically.

| Production Metric (`DA-V2 Metric-Small` + `YOLOv8m`, `depthtest`) | Pre-Fix Baseline (`S0`) | Post-Fix Live E2E (`YOLO`) | Change (E2E) | Post-Fix Live Oracle 2D (`N = 6`) |
| --- | ---: | ---: | ---: | ---: |
| Cameras recovered inside room (`solvePnP`) | `1 / 5 (20%)` | **`5 / 5 (100%)`** | `+80%p` | `5 / 5 (100%)` |
| Mean IPPE reprojection error | `5.75 px` | **`1.04 px`** | `-81.9%` | `1.04 px` |
| All-primary wall-normal front MAE | `0.557 m` | **`0.193 m`** (`0.1934 m`) | **`-65.3%`** | `0.216 m` (`0.2162 m`) |
| All-primary wall-normal center MAE | `0.490 m` | **`0.150 m`** (`0.1499 m`) | **`-69.4%`** | `0.181 m` (`0.1813 m`) |
| Desk-4 depth dimension MAE (`GT 1.78 m`) | `1.105 m` | **`0.413 m`** | **`-62.6%`** | `0.469 m` |
| Desk-4 2D footprint IoU | `0.289` (`0.2895`) | **`0.489`** (`0.4893`) | **`+69.0%`** | **`0.511`** (`0.5113`) |
| `twochair` front / center MAE | `1.216 / 0.952 m` | **`0.121 / 0.118 m`** (`1/2`) | **`-90.1% / -87.6%`** | `0.128 / 0.221 m` (`2/2`) |
| Primary clamped items / Confirmed FPs (TV, book, hallway) | `1` / `11 (4, 3, 4)` | **`0`** / **`1 (0, 0, 1)`** | **`-100%`** / **`-90.9%`** | `0` / `0` |

![FR-03 5-Stage Before vs After Pipeline Comparison on 0.5x Ultrawide Desk](figures/fig6_fr03_5stage_comparison_desk.png)
*Figure 6: Real-photo 5-stage Before (top) vs. After (bottom) comparison on `depthtest_distortion.jpeg` (0.5x ultrawide desk, `ID = 31`, `s = 0.60 m`). Post-fix production code recovers EXIF `fx = 1568 px` (`0.31 px` reprojection, camera inside room), removes TV and laptop-as-book false positives, applies proportional ArUco scaling (`alpha = 0.42`, `beta = 0`), and fits a wall-aligned 3D ENU point-cloud desk (`center 2.07 m` vs. GT `2.06 m`, `IoU = 0.53`).*

![FR-03 5-Stage Before vs After Pipeline Comparison on Portrait Two-Chair Scene](figures/fig7_fr03_5stage_comparison_twochair.png)
*Figure 7: Real-photo 5-stage Before (top) vs. After (bottom) comparison on `depthtest_twochair.jpeg` (`2252 x 4000`, `ID = 31`, `s = 0.60 m`). Replacing hardcoded `fx = 3600 px` and 4-corner affine extrapolation (`beta = +1.86 m`) with EXIF `fx = 2556 px`, proportional calibration, and 3D ENU point-cloud foreground filtering cuts chair front-face error by `90.1%` (`0.58 m -> 1.68 m` vs. GT `1.80 m`) and restores true footprint scale (`0.42 x 0.48 m` vs. GT `0.47 x 0.46 m`).*

**Unconstrained Colab GPU Optimal Upper Bound (NVIDIA Tesla T4, 91 Model Combinations).** To quantify how far our fixed P0+P1 geometry architecture scales once the 1.9 GB AWS CPU RAM ceiling is lifted, we ran a 91-combination benchmark on a Google Colab NVIDIA Tesla T4 GPU (`14,912.7 MB` VRAM, CUDA `13.0`, PyTorch `2.14.1+cu130`, Transformers `5.22.1`, Ultralytics `8.4.220`) across all five `depthtest` photos (`exp_colab_optimal/colab_optimal_summary.json`). Running our exact fixed CPU production stack (`DA-V2-Metric-Small + YOLOv8m`) on T4 verified numerical parity within `0.1-0.2 mm` (`0.1933 m` vs. `0.1934 m` front MAE, `0.1501 m` vs. `0.1499 m` center MAE, `0.4892` vs. `0.4893` desk IoU) while accelerating E2E warm latency **3.2x** (`1,182.5 ms -> 371.0 ms`). Three architecture-level discoveries emerged:

1. **COCO-80 Conference-Desk Domain Shift (`YOLO11x` vs. `RT-DETR-x`).** Upgrading from `YOLOv8m` to larger CNN/anchor-free YOLO checkpoints (`YOLO11x`, `YOLO11x-seg`, `YOLOv8x-seg`) cleanly separated the overlapping `twochair` chairs (`2/2`, IoU `0.738-0.969`), but severely degraded conference-desk recall (`1/4` desks for `YOLO11x` / `YOLO11x-seg`, `0/4` for `YOLOv8x-seg`; `3/6` and `2/6` total primary recall). Replacing YOLO with global-attention **`RT-DETR-x`** (`67.47M`, `109.8 ms`, `625.8 MB` VRAM) solved both bottlenecks simultaneously, achieving **100% recall (`6/6` primary and `15/15` all-visible targets)**.
2. **Pixel-Exact Instance-Mask 3D Cuboid Fitting (`RT-DETR-x + SAM 2.1 Base`).** Prompting **`SAM 2.1 Base`** (`sam2.1_b.pt`, `80.85M`, `690.8 MB` isolated VRAM) with `RT-DETR-x` boxes feeds pixel-exact masks directly into our 3D ENU point-cloud fitter, stripping floor and wall bleed from rectangular boxes. Paired with `Apple Depth Pro` (`fp16`, `1,734.9 ms`, `3,761.7 MB` allocated VRAM), our **Winning Optimal GPU Stack** lifts desk 2D footprint IoU from `0.489` to **`0.6763`**, cuts `twochair` front MAE to **`3.7 cm` (`0.0370 m`)**, and cuts desk height MAE to **`1.8 cm` (`0.0182 m`)** (`0.1032 m` overall front MAE, `0.0728 m` center MAE, `6/6` recall). Pairing `RT-DETR-x + SAM 2.1` with our lightweight `DA-V2-Metric-Small` (`Zero-FP Mask GPU`) eliminates every confirmed false positive (**`0` FPs**) at `0.1586 m` front MAE, `0.0978 m` center MAE, and `0.5967` desk IoU (`~1.5 GB` simultaneous VRAM).
3. **Pure EXIF-Free Optimal Operation (`Apple Depth Pro pred_fx`).** Removing both EXIF metadata and 1D focal search by passing `Apple Depth Pro`'s predicted `fx` (`0.69-9.98%` error, `5/5` cameras inside room) directly into `solvePnP` + `RT-DETR-x + SAM 2.1` achieves **`0.0945 m` (`9.5 cm`) front MAE, `0.0777 m` (`7.8 cm`) center MAE, `0.6612` desk IoU, and `3.2 cm` (`0.0323 m`) `twochair` front MAE**, confirming a robust EXIF-free upper bound for future cloud GPU tiers.

| Pipeline Stack (T4 GPU vs. CPU) | Recall (Prim / Vis) | Front MAE | Center MAE | Desk 2D IoU | `twochair` Front MAE | Confirmed FP | Warm Latency | Peak Memory (RAM / Alloc VRAM) | Deployment Tier |
| --- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `S0` Pre-Fix CPU Baseline (`DA-V2-M-S + YOLOv8m`) | 5/6 | `0.5572 m` | `0.4902 m` | `0.2895` | `1.2162 m` | 11 | `~1,180 ms` | CPU | Pre-fix baseline |
| `S5` Fixed CPU Prod (`DA-V2-M-S + YOLOv8m`) | 5/6 (14/15) | `0.1934 m` | `0.1499 m` | `0.4893` | `0.1209 m` | 1 | `1,182.5 ms` | `2,794 MB` RAM | **Tier 1: 1.9 GB CPU Prod** |
| `S5` T4 GPU Parity (`DA-V2-M-S + YOLOv8m`) | 5/6 (14/15) | `0.1933 m` | `0.1501 m` | `0.4892` | `0.1208 m` | 1 | **`371.0 ms`** | `549 MB` (sum) | Tier 2 parity |
| Fast Sub-1s GPU 6/6 (`DA-V2-M-S + RT-DETR-x`) | **6/6 (15/15)** | `0.1959 m` | `0.1748 m` | `0.4998` | `0.1211 m` | 1 | **`468.0 ms`** | **`788.8 MB`** (simul.) | **Tier 2: Sub-2 GB GPU** |
| Zero-FP Mask GPU (`DA-V2-M-S + RT-DETR-x + SAM 2.1`) | **6/6 (15/15)** | `0.1586 m` | `0.0978 m` | `0.5967` | `0.0880 m` | **`0`** | `1,566.8 ms` | `~1.5 GB` (simul.) | **Tier 2: Sub-2 GB GPU** |
| DepthPro Raw + `RT-DETR-x` (bbox, `alpha=1.0`) | **6/6 (15/15)** | `0.1554 m` | `0.1491 m` | `0.5159` | `0.0895 m` | 2 | `1,964.2 ms` | `4,387.5 MB` (sum) | Tier 3 ablation |
| EXIF-Free Optimal (`DepthPro pred_fx + RT-DETR-x + SAM 2.1`) | **6/6 (15/15)** | **`0.0945 m`** | `0.0777 m` | `0.6612` | **`0.0323 m`** | 2 | `2,960.8 ms` | `4,773.7 MB` (simul.) | **Tier 3: Optimal T4 GPU** |
| **Winning Optimal GPU** (`DepthPro + EXIF/1D + RT-DETR-x + SAM 2.1`) | **6/6 (15/15)** | **`0.1032 m`** | **`0.0728 m`** | **`0.6763`** | **`0.0370 m`** | 2 | `2,951.6 ms` | `4,773.7 MB` (`6,952 MB` resv.) | **Tier 3: Optimal T4 GPU** |

![FR-03 Unconstrained Colab Tesla T4 GPU Optimal Benchmark](figures/fig8_fr03_colab_gpu_optimal.png)
*Figure 8: (a) End-to-end accuracy and 2D desk IoU progression from pre-fix CPU baseline (`S0`) through fixed CPU production (`S5`) to Colab Tesla T4 GPU optimal stacks (`Zero-FP Mask GPU`, `EXIF-Free Optimal`, and `Winning Optimal GPU`: `6/6` recall, `0.095-0.103 m` front MAE, `0.676` desk IoU); (b) Accuracy versus warm E2E latency and allocated VRAM across our three deployment tiers.*

![FR-03 5-Stage Comparison on Two-Chair Scene: Fixed CPU Prod vs Optimal GPU Stack](figures/fig9_fr03_5stage_gpu_optimal_twochair.png)
*Figure 9: Live 5-stage visual comparison on `depthtest_twochair.jpeg` between Tier-1 Fixed CPU Production (`YOLOv8m + DA-V2-Metric-S`, top: `1/2` merged chairs, `12.1 cm` front MAE) and Tier-3 Optimal GPU Stack (`RT-DETR-x + SAM 2.1 Base + Apple Depth Pro`, bottom: `2/2` chairs cleanly separated with pixel-exact SAM 2.1 masks `IoU 0.94 / 0.97`, recovering blue `1.22 m` vs. GT `1.17 m` and white `1.83 m` vs. GT `1.80 m` for a `3.7 cm` mean front MAE).*

### C. FR-04 Adaptive Shading Control: Synthetic Benchmark and Real-Weather Replay

![FR-04 Adaptive Shading Control Synthetic Benchmark and Real 1-Minute Replay](figures/fig4_fr04_control_tradeoff.svg)
*Figure 4: (a) Total opacity-change (flicker) reduction and LG SGF sun-weighted Grade B daylight gain on the synthetic stress scenarios; (b) Live API cell switch counts on real measured 1-minute NOAA SURFRAD irradiance days (`FR-02` vs. default `FR-04 smooth_fr02`).*

Table 5 presents the synthetic 4-scenario benchmark (`docs/experiments/fr04_adaptive_control_experiment_benchmark.md`) comparing all three FR-04 control configurations against the rule baseline (positive percentages indicate improvement over baseline).

| Control Mode | Flicker Reduction (Total Opacity Change) | Switch Count Change | Grade B Daylight Gain (Mean Opacity) | Grade B Sun-Weighted Transmittance Gain | Cap Exceedances at `q90` (Baseline / Mode) |
| --- | ---: | ---: | ---: | ---: | :---: |
| `FR-04 smooth_fr02` (API default, binary) | +0.0% to **+42.9%** | +0.0% to +42.9% | -2.7% to +0.0% | -3.9% to +0.0% | 9 / **0** |
| `FR-04 replace`, PDLC (binary DP) | +62.5% to **+78.6%** | +62.5% to +78.6% | -3.5% to +0.0% | -3.9% to +0.8% | 9 / **0** |
| `FR-04 replace`, LG SGF (5-level DP) | +62.5% to **+78.6%** | -12.5% to +35.7% | -2.0% to +2.1% | **+1.3% to +20.6%** | 9 / **0** |

All three FR-04 modes eliminated every `q90` Grade B cap exceedance observed under the rule baseline (6 in `Partly Cloudy Rapid-Flicker` and 3 in `High Uncertainty Squall`). Five-level LG SGF dimming achieved the same `+78.6%` total opacity-change reduction as binary PDLC while returning **+1.3% to +20.6%** more sun-weighted daylight on Grade B cells by stepping to intermediate opacity levels (`0.25`, `0.50`, `0.75`) instead of snapping fully opaque.

Table 6 summarizes our real KMA 10-minute replay and measured 1-minute NOAA SURFRAD replay through the full application API (`docs/experiments/fr04_real_weather_replay.md`, `docs/experiments/fr04_minute_replay.md`).

| Real Replay Dataset & Day | Step | Switches (`FR-02` / `FR-04`) | Switch Reduction | Daylight Gain (Same Step) | Daylight vs. 30-min `FR-02` | Never Brighter than `FR-02` |
| --- | :---: | :---: | ---: | ---: | ---: | :---: |
| KMA Seoul `2026-10-05` (restless) | 5 min | 159 / 115 | **+27.7%** | -7.2% | n/a | yes |
| KMA Seoul `2026-10-05` (restless) | 10 min | 108 / 108 | +0.0% | +0.0% | n/a | yes |
| SURFRAD `2023-10-11` (clear) | 1 min | 164 / 164 | +0.0% | -0.3% | **+31.5%** | yes |
| SURFRAD `2023-10-02` (intermittent sunny) | 1 min | 155 / 125 | **+19.4%** | -0.7% | **+31.5%** | yes |
| SURFRAD `2023-10-22` (intermittent restless) | 1 min | 604 / 206 | **+65.9%** | -15.3% | **+33.9%** | yes |
| SURFRAD `2023-10-22` (intermittent restless) | 5 / 30 min | 206/206, 140/140 | +0.0% | +0.0% | baseline | yes |

**Key Insight from Real-Weather Replays.** Default `FR-04 smooth_fr02` produces large stability gains when control runs at sub-10-minute cadence: on the restless 1-minute SURFRAD day (`2023-10-22`), switches drop **65.9%** (`604` to `206`) with zero cap violations. Comparing fine-grained `FR-04` (1-min step) against coarse `FR-02` (30-min step, the naive way to reduce switching) reveals why adaptive stabilization matters: 1-minute `FR-04` admits **+31.5% to +33.9% more daylight** than 30-minute `FR-02` (`0.0805` vs. `0.1217` mean opacity on Oct 22) because coarse 30-minute blocks hold cells dark long after brief clouds or moving sunbeams pass. Conversely, when evaluated at 10-minute or 30-minute steps, single steps already equal or exceed the `600 s` gap-fill and holding-time thresholds, leaving zero sub-step flicker for `smooth_fr02` to suppress.

### D. Automated Verification

On `main` (`882521e`), our automated test suite verifies every AIML contract, leakage check, runtime lock, diagnostic schema, replay pipeline, and frontend integration flow:

- **Backend & Engine (`pytest server/tests`):** `856 passed, 0 failed` baseline tests on `main` (`882521e`), plus **8 new Vision AI P0/P1 regression tests** (`102 passed, 0 failed` across the Vision AI, window-marker, and film-clamping suites, with `ruff check`, `ruff format --check`, `mypy` across 78 files, and `app.scene_cli schema --check` all reporting 0 errors) covering solar geometry (`pvlib`), KMA/Open-Meteo anti-leakage dataset builders, frozen V1 artifact round-trip fidelity, EXIF/1D-focal recovery, proportional single-marker calibration, 3D ENU point-cloud fitting, non-destructive clamping, `mode=experiment` diagnostics, FR-04 smoothing/DP/real-weather replays, and rolling scenario precomputation.
- **Frontend (`npm --prefix frontend test`):** `207 passed, 0 failed` unit tests covering in-app Vision camera flow, `VisionPreview` geometry, `estimateVisionFurniture`, `weatherProvenance` segment merging, ArUco burst capture verification, and offline-first sync.

---

## V. Related Work & Tools

### Academic and Domain Foundations

1. **Solar Decomposition and Clear-Sky Modeling:** Erbs, Klein, and Duffie (1982), *"Estimation of the diffuse radiation fraction for hourly, daily and monthly-average global radiation,"* Solar Energy 28(4), underpins our `pvlib.irradiance.erbs()` conversion of KMA GK2A DSR to solar-geometry-corrected DNI priors.
2. **Probabilistic Solar Nowcasting:** Quantile regression evaluated via pinball loss and Prediction Interval Coverage Probability (PICP) follows standard probabilistic solar forecasting practice (van der Meer et al., 2018; Lauret et al., 2019), allowing downstream controllers to hedge against upper-tail irradiance (`P90`).
3. **Planar Marker Pose and Monocular Depth Estimation:** Infinitesimal Plane-Based Pose Estimation for square markers (Collins & Bartoli, 2014, implemented in OpenCV `SOLVEPNP_IPPE_SQUARE`) provides exact metric camera pose from the paired window ArUco tag. Dense indoor metric depth uses **Depth Anything V2** (Yang et al., 2024, `depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf`), benchmarked against **Depth Pro** (Bochkovskii et al., 2024), **DPT / MiDaS** (Ranftl et al., 2021), **ZoeDepth** (Bhat et al., 2023), and **GLPN** (Kim et al., 2022). 2D indoor furniture detection uses **Ultralytics YOLOv8** (Jocher et al., 2023) on CPU and was further evaluated on GPU against **RT-DETR** (Zhao et al., 2024) and **SAM 2** (Ravi et al., 2024) for pixel-exact instance-mask 3D cuboid fitting.
4. **Smart Glazing Control and Hysteresis Stabilization:** Balancing photodegradation limits, visual daylight autonomy, and switching frequency builds on receding-horizon electrochromic glazing control literature, adapted here to cell-level PDLC and 5-level LG Switchable Glazing Film dimming.

### Software Libraries, APIs, and Hardware Ecosystem

| Category | Tools, Libraries, and Data Sources Used in SunSync-AI |
| --- | --- |
| **Solar & Numerical ML** | `pvlib` (solar position, clear-sky Ineichen, Erbs decomposition), `NumPy`, `SciPy` (`scipy.optimize.minimize` L-BFGS-B), `Shapely` (2D/3D polygon clamping & ray-window clipping) |
| **Computer Vision** | `OpenCV` (`opencv-contrib-python`, `cv2.aruco` `DICT_4X4_50`, `SOLVEPNP_IPPE_SQUARE`), `Ultralytics` (`yolov8m.pt`), `PyTorch`, `HuggingFace Transformers` (`Depth-Anything-V2-Metric-Indoor-Small-hf`), `Pillow` |
| **Backend & Precomputation** | `FastAPI`, `Pydantic v2`, `SQLModel`, `SQLite` (WAL mode), `Uvicorn`, `systemd` timers (`sunsync-ai-nowcast-worker.timer`, `sunsync-kma-worker.timer`) |
| **Frontend & 3D Visualization** | `React`, `TypeScript`, `Vite`, `Three.js` (interactive 3D room & sunlight projection), `VisionPreview` SVG floorplan |
| **Meteorological & Hardware Sources** | KMA API Hub (GK2A `DSR` & `CA`), Open-Meteo Historical Forecast API (`ecmwf_ifs025`), NOAA GML SURFRAD (`Penn_State_PA`), LG Chem Switchable Glazing Film (SGF) & LG ThinQ ON / Matter integration design |

---

## VI. Conclusion & Discussion

### Key Findings

SunSync-AI integrates three empirical AIML pipelines into a single smart-film control prototype. First, our frozen linear quantile solar nowcaster (`fr01_kma_quantile_v1`) achieved a **56.2% MAE reduction at 10 minutes** and **26.8% at 30 minutes** on an untouched 4-day holdout test while handing 60-minute horizons back to Open-Meteo based on observed crossover behavior. Second, our real-room `depthtest` evaluation across 11 monocular depth variants and four capture conditions exposed critical calibration, focal-length, and back-projection bottlenecks; implementing the resulting P0/P1 production fixes reduced live E2E wall-normal front MAE by **65.3%** (`0.557 m -> 0.193 m`), center MAE by **69.4%** (`0.490 m -> 0.150 m`), and confirmed YOLO false positives by **90.9%** (`11 -> 1`) at zero extra model weight, while our unconstrained Google Colab Tesla T4 GPU benchmark (`RT-DETR-x + SAM 2.1 Base + Apple Depth Pro`) further established a **100% recall (`6/6`)**, **`9.5-10.3 cm` front MAE (`3.2-3.7 cm` on `twochair`)**, and **`0.676` desk IoU** accuracy ceiling across three clear deployment tiers (1.9 GB CPU Prod, Sub-2 GB GPU, Optimal T4 GPU). Third, our FR-04 adaptive controller suppressed up to **65.9%** of switches on real restless 1-minute skies while admitting **+31.5% to +33.9%** more daylight than 30-minute schedules, backed by 10-minute rolling scenario precomputation that eliminated live UI calculation timeouts.

### Current Limitations and Future Directions

1. **Single-Station, Single-Season Solar Data and DSR Proxy:** Our FR-01 dataset covers one Seoul coordinate in October-November 2026 and uses Erbs-converted KMA DSR (`dsr_proxy`) rather than collocated pyrheliometer DNI measurements. Multi-season and multi-site validation remains future work.
2. **2D Cloud-Motion Advection:** `advection_impact` remains `0.0` in V1, which limits 60-minute nowcast skill and lowers PICP (`57.1%`) during partly cloudy transitions. Incorporating GK2A cloud-motion patches is the primary lever for V2.
3. **Overlapping 2D Furniture Merging, Glass-Wall Margin, and Co-Resident Server Memory:** While our real-room `depthtest` fixes cut E2E depth error to `19.3 cm` across a single room (`5` photos), line-of-sight occlusion in `twochair` still merges two overlapping chairs into one YOLOv8m box (`blue_chair` missed until interactive user splitting or instance segmentation such as `yolov8-seg` is added), one hallway chair within `25 cm` of the glass wall still passed the room-buffer gate, and co-loading `YOLOv8m` with `DA-V2-Metric-Small` reached `2,794 MB` peak RSS on macOS, motivating ONNX INT8 quantization or sequential execution on 1.9 GB CPU hosts alongside multi-room field validation.
4. **Borrowed 1-Minute Sky, Assumed Film Optics, and UI Step Cadence:** The 1-minute FR-04 replay maps NOAA SURFRAD data from Pennsylvania onto Seoul solar time, and LG SGF optical transmittance values (`0.03` opaque to `0.72` clear, `0.85` glazing) are engineering assumptions rather than a vendor datasheet. Aligning the dashboard adaptive badge from 30-minute preview steps to finer control intervals will expose `smooth_fr02` gains directly in the UI.
