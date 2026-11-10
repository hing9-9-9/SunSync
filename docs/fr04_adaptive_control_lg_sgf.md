# FR-04 Uncertainty-Aware Adaptive Smart Film Control & LG SGF / ThinQ ON Integration Guide

**Author**: Track 3 Subagent (Adaptive Control & LG SGF / ThinQ ON Bridge Developer)  
**Target Teammate**: `0nsetofspring` (FR-02 & FR-04 Selective Shading & Stabilization Specialist)  
**Branch**: `feature/ai-core-modules`  
**Status**: Prototype. Checked only on synthetic irradiance (section 7); not connected to the web app or to FR-05.  

---

> **Which mode runs (decision of 2026-11-10).** The API default is `mode: "smooth_fr02"`: FR-04 keeps the FR-02 plan steady (FR-04.1 gap filling, FR-04.2 holding time, FR-04.3 hysteresis) and decides nothing new, as `docs/features.md` describes. The dynamic-programming controller described in sections 2 and 4 is the experimental `mode: "replace"`. Film type (PDLC or SGF) only matters in `replace`. See section 11.

## 1. Overview and Core Objectives

SunSync's rule-based selective shading (FR-02) operates deterministically on forecasted DNI. However, real-world sky conditions exhibit high cloud intermittency, where brief cumulus passages cause smart film cells to oscillate rapidly between transparent and opaque. This creates severe visual flicker and premature wear on switching electronics.

**FR-04 Adaptive Control** (the experimental `replace` mode; the default smooths the FR-02 plan, see section 11) is an uncertainty-aware multi-objective controller that:
1. **Suppresses Flicker**: Formulates horizon control as a **Dynamic Programming (Viterbi)** optimization balancing damage risk, daylight quality, and switching costs.
2. **Quantile Risk-Aversion**: Ingests DNI prediction quantiles $(P_{10}, P_{50}, P_{90})$ to hedge against cloud edge spikes.
3. **Applies the stabilization rules**: `FR-04.1` (Gap Filling), `FR-04.2` (Holding Time) and `FR-04.3` (Dynamic Hysteresis). `FR-04.4` (edge margin) is met by FR-02.1 (section 3.4). Grade A cells are required to be opaque whenever the sun reaches them; the checks in this document count cell-steps above the cap and do not prove compliance.
4. **LG SGF Continuous Dimming**: Replaces binary $0/1$ PDLC switching with multi-level continuous dimming ($0.0, 0.25, 0.5, 0.75, 1.0$), maximizing interior natural lighting while respecting strict protection caps ($I_{\max}$).
5. **LG ThinQ ON Bridge**: Synchronizes real-time cell opacity and transmittance matrices to LG Electronics' ThinQ ON smart home hub.

---

## 2. Mathematical Formulation: Multi-Objective Horizon Optimization

For each window cell $(r, c)$, the controller computes the optimal opacity trajectory $u_{1..T} \in [0, 1]^T$ minimizing the cumulative horizon loss $\mathcal{L}$:

$$\mathcal{L}(u_{1..T}) = \sum_{t=1}^T \Big( w_{\text{damage}} \cdot \text{Risk}_t(u_t) + w_{\text{daylight}} \cdot u_t^2 + w_{\text{flicker}} \cdot |u_t - u_{t-1}| \Big)$$

### 2.1 Component Definitions

1. **Damage Risk Penalty ($\text{Risk}_t(u_t)$)**:
   - **Grade A (Artifacts & Sensitive Exhibits)**: Hard constraint ($I_{\max} = 0 \text{ W/m}^2$). If direct sunlight reaches the cell:
     $$\text{Risk}_t(u_t) = \begin{cases} 0.0 & \text{if } u_t \ge 0.99 \\ 10^9 & \text{if } u_t < 0.99 \text{ (strictly forbidden)} \end{cases}$$
   - **Grades B and C (Reading Desks & Office Zones)**:
     Evaluates risk-averse effective irradiance using quantile DNI predictions:
     $$I_{\text{eff}, t} = I_{q50, t} + \kappa_{\text{grade}} \cdot \max(0, I_{q90, t} - I_{q50, t})$$
     where $\kappa_B = 1.2 > \kappa_C = 0.8$.
     The transmitted irradiance at opacity $u_t$ is:
     $$I_{\text{trans}, t}(u_t) = I_{\text{eff}, t} \cdot \tau_{\text{glazing}} \cdot \tau(u_t)$$
     If $I_{\text{trans}, t}(u_t) > I_{\max}$, the quadratic excess penalty applies:
     $$\text{Risk}_t(u_t) = 100 \cdot (I_{\text{trans}, t}(u_t) - I_{\max})^2$$
   - **Grade D & Unexposed Cells**: $\text{Risk}_t(u_t) = 0.0$.

2. **Daylight Quality Term ($w_{\text{daylight}} \cdot u_t^2$)**:
   Penalizes unnecessary darkening. Because daylight illuminance benefits room occupants and reduces artificial lighting energy, lower opacity is favored unless protection requires shading.

3. **Flicker Reduction Term ($w_{\text{flicker}} \cdot |u_t - u_{t-1}|$)**:
   Penalizes state changes between consecutive time steps. Under cloud fluctuations, this term penalizes toggling, keeping the film stable.

---

## 3. FR-04 Stabilization Rules (`FR-04.1` ~ `FR-04.4`)

In addition to Viterbi DP, deterministic post-stabilization rules guarantee physical safety and regulatory compliance:

```mermaid
flowchart TD
    RawHits["Ray-Tracing Exposed Tiles (FR-01)"] --> EdgeMargin["FR-04.4 Cell-Edge Margin Expansion"]
    EdgeMargin --> DPOpt["Uncertainty-Aware DP Viterbi Optimizer"]
    DPOpt --> HoldCheck["FR-04.2 Minimum Holding Time (Darkening Immediate / Brightening Held)"]
    HoldCheck --> GapFill["FR-04.1 Gap Filling (Short Gaps < 600s Kept Shaded)"]
    GapFill --> Output["Stabilized Plan & LG ThinQ ON Matrix"]
```

### 3.1 FR-04.1 Gap Filling
- **Rule**: If a cell is shaded ($u > 0$), drops to transparent ($u = 0$) for a brief gap $\Delta t < \text{min\_gap\_seconds}$ (default $600\text{s} = 10\text{ min}$), and is shaded again, the gap is filled:
  $$u_t = \min(u_{\text{left}}, u_{\text{right}})$$
- **Effect**: Eliminates brief flickers when clouds temporarily drift across the sun.

### 3.2 FR-04.2 Minimum Holding Time
- **Rule**: When a cell darkens ($u_t > u_{t-1}$), it must maintain at least that opacity for $\text{brightening\_hold\_seconds}$ (default $600\text{s}$) before it is permitted to brighten.
- **Critical Exception**: Immediate darkening to strengthen protection against rising solar irradiance is **always allowed without delay**.

### 3.3 FR-04.3 Dynamic Hysteresis Threshold
- **Rule**: When considering brightening ($u_t < u_{t-1}$), the effective threshold incorporates a dynamic margin scaled by cloud uncertainty:
  $$\text{margin}_{\text{hysteresis}} = \text{brightening\_margin\_W\_m2} \cdot \left(1.0 + \frac{I_{q90} - I_{q10}}{I_{q50} + 1.0}\right)$$
- Brightening is discouraged unless transmitted irradiance falls safely below $I_{\max} - \text{margin}_{\text{hysteresis}}$.

### 3.4 FR-04.4 Cell-Edge Margin
- **Requirement**: the neighbouring cell is shaded when a light path crosses a cell boundary.
- **Where it is met**: in FR-02.1. `cells_for_hit` (`engine/fr02/cell_mapping.py`) widens each window crossing to one tile and shades every cell that tile overlaps.
- **The `enable_edge_margin` option** (replace mode only, off by default) is a second, cruder margin: it also shades the four orthogonal neighbours of every cell that is hit, whether or not the path is near a boundary (`edge_margin_threshold_m` is defined but not used). Turning it on shades neighbours twice. It is ignored in smoothing mode.

---

## 4. LG SGF Continuous Dimming vs. Binary PDLC

Traditional PDLC (Polymer Dispersed Liquid Crystal) films only support discrete binary states:
$$u \in \{0.0, 1.0\} \implies \tau \in \{\tau_{\max}, \tau_{\min}\}$$

**LG SGF (Smart Glass Film) / SPD (Suspended Particle Device)** allows continuous or multi-level dimming:
$$u \in [0.0, 1.0], \quad \tau(u) = \tau_{\max} - u \cdot (\tau_{\max} - \tau_{\min})$$

### Comparison in Practice

| Feature | Binary PDLC (FR-02) | Multi-level SGF (FR-04) |
|---|---|---|
| **States** | `{0.0, 1.0}` (`transparent`, `opaque`) | Continuous `[0.0, 1.0]` or 5 levels `{0, 0.25, 0.5, 0.75, 1.0}` |
| **When the cap is exceeded a little** | Must go fully opaque | Can dim just enough |
| **Switch count** | Low | Higher: every step between levels counts as a switch |
| **Size of each change** | Always 0 to 1 | Smaller steps |

Worked example from the formulas above (glazing 0.85, film 0.72 clear and 0.03 opaque, cap 50 W/m2, no risk margin). The FR-02 rule compares the incident irradiance with the cap; the FR-04 optimizer compares the transmitted irradiance. These are formula results, not experiment results.

| Incident | Mode | Opacity | Film transmittance | Transmitted |
|---|---|---|---|---|
| 200 W/m2 | FR-02 rule | 1.00 | 0.030 | 5.1 W/m2 |
| 200 W/m2 | FR-04 PDLC | 1.00 | 0.030 | 5.1 W/m2 |
| 200 W/m2 | FR-04 5-level SGF | 0.75 (the level above the 0.62 needed) | 0.203 | 34.4 W/m2 |
| 200 W/m2 | FR-04 continuous | 0.62 | 0.294 | 50.0 W/m2 |
| 80 W/m2 | FR-02 rule | 1.00 (80 is above 50) | 0.030 | 2.0 W/m2 |
| 80 W/m2 | FR-04 (any mode) | 0.00 (a clear film already gives 49.0, below 50) | 0.720 | 49.0 W/m2 |

The second case is where FR-04 can return daylight, with or without SGF. In the experiments the hedge against q90 (`kappa_B`) and the 10-minute hold take most of it back.

How much daylight this gives back on a real day is in section 7. An earlier version of this section quoted "+25% to +45% natural daylight"; that figure had no source (section 9).

---

## 5. LG ThinQ ON Protocol Contract (`LG_THINQ_ON_SGF_V1`)

SunSync bridges directly to LG ThinQ ON smart home hubs via `POST /api/scenes/{scene_id}/thinq-hub-sync`.

### 5.1 Device Payload Structure
```json
{
  "hub_device_id": "thinq_on_hub_01",
  "protocol_version": "LG_THINQ_ON_SGF_V1",
  "scene_id": "scene_library_01",
  "window_id": "window_south",
  "timestamp": "2026-10-14T05:00:00Z",
  "hardware_mode": "LG_SGF",
  "cell_opacity_matrix": [
    [0.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.5, 0.0],
    [0.0, 0.5, 0.5, 0.0],
    [0.0, 0.0, 0.0, 0.0]
  ],
  "cell_transmittance_matrix": [
    [0.612, 0.612, 0.612, 0.612],
    [0.612, 0.026, 0.319, 0.612],
    [0.612, 0.319, 0.319, 0.612],
    [0.612, 0.612, 0.612, 0.612]
  ],
  "active_shaded_cells": 3,
  "dimmed_cells": 2,
  "energy_daylight_score": 0.8142,
  "decision_summary_ko": "LG ThinQ ON SGF 제어: 조도 불확실성(P90)을 반영하여 3개 셀 차광(디밍 2개)을 최적화했습니다. 플리커(불투명도 변화량)은 기준보다 75.0% 개선됐습니다, 자연채광은 기준보다 1.2% 나빠졌습니다."
}
```

The numbers in this payload (for example "75.0%" and "1.2%" in `decision_summary_ko`) are an illustrative example, not measured results. `LG_THINQ_ON_SGF_V1` is a contract defined inside SunSync; no LG-published ThinQ ON interface was checked.

---

## 6. Teammate Guide for `0nsetofspring`

### 6.1 Running the Experiment CLI
Run the standalone experiment simulator from repository root:
```bash
.venv/bin/python tools/ai_experiments/run_fr04_adaptive_control_experiment.py
```
This script generates:
- Terminal formatted benchmark tables
- `docs/reports/fr04_adaptive_control_experiment_benchmark.md`
- `docs/reports/fr04_adaptive_control_experiment_results.json`

### 6.2 Experiment results

See section 7. The full tables are in `docs/reports/fr04_adaptive_control_experiment_benchmark.md`, written by the CLI on every run.

### 6.3 Interfacing in Backend Code
- **Engine Package**:
  ```python
  from engine.fr04 import (
      AdaptivePolicyTuner,
      AdaptiveWeights,
      OpticalProfileData,
      optimize_cell_dp,
      apply_fr04_stabilization,
      compare_baseline_vs_adaptive,
  )
  ```
- **REST Endpoints**:
  - `POST /api/scenes/{scene_id}/adaptive-shading-plan`: Returns both `baseline_plan` and `stabilized_plan`, along with `dimming_trajectories` and `metrics`. `mode` is `smooth_fr02` (default) or `replace` (section 11).
  - `POST /api/scenes/{scene_id}/thinq-hub-sync`: Evaluates current dimming state, persists it to virtual film, and returns the LG ThinQ ON device payload.

---

## 7. Experiment results (run of 2026-11-10)

Source: `tools/ai_experiments/run_fr04_adaptive_control_experiment.py`; full tables in `docs/reports/fr04_adaptive_control_experiment_benchmark.md`. Four synthetic days (Sunny, Partly Cloudy Rapid-Flicker, High Uncertainty Squall, Overcast Low Irradiance), one 4x4 window (1 Grade A cell, 3 Grade B cells with a 30 W/m2 cap as in the app policy, 12 Grade D cells), 5-minute steps. Ranges are over the four scenarios. Positive means better than the rule baseline; nothing is clipped at zero.

| Mode | Flicker (total opacity change) | Switch count | Daylight, all 16 cells (opacity) | Daylight, Grade B cells (opacity) | Daylight, Grade B cells (sun-weighted) |
|---|---|---|---|---|---|
| FR-04 smoothing of FR-02 (default) | +0.0% to +42.9% | +0.0% to +42.9% | -2.0% to +0.0% | -2.7% to +0.0% | -3.9% to +0.0% |
| FR-04 replace, PDLC | +62.5% to +78.6% | +62.5% to +78.6% | -3.1% to +0.0% | -3.5% to +0.0% | -3.9% to +0.8% |
| FR-04 replace, SGF (5 levels) | +62.5% to +78.6% | -12.5% to +35.7% | -1.9% to +1.6% | -2.0% to +2.1% | +1.3% to +20.6% |

| Mode | Cell-steps above the cap if q90 is true (Sunny, Partly Cloudy, Squall, Overcast) |
|---|---|
| FR-02 rule baseline | 0, 6, 3, 0 |
| FR-04 smoothing of FR-02 | 0, 0, 0, 0 |
| FR-04 replace, PDLC | 0, 0, 0, 0 |
| FR-04 replace, SGF | 0, 0, 0, 0 |

Nobody exceeds the cap if the median forecast (q50) is true.

How to read it:

1. **Smoothing helps only when FR-02 itself flickers.** On the Sunny day FR-02 switches only at dawn and dusk, so smoothing changes nothing (0.0%). On the two cloudy days it removes 42.9% of the total change (14 to 8). The replace modes remove 62.5% to 78.6%, because they also decide for themselves.
2. **Smoothing keeps the protection of FR-02 and adds the hysteresis hedge.** It is never brighter than the FR-02 plan, and the hysteresis keeps B and C cells dark until the clear film is well below the cap, so the cap is not exceeded at q90 either (FR-02 alone exceeds it 6 and 3 times in the cloudy scenarios).
3. **Smoothing costs a little daylight, and gives none back.** It only ever darkens: -2.7% to 0.0% on Grade B cells (opacity), -3.9% to 0.0% sun-weighted.
4. **Only the replace modes can return daylight.** SGF with five levels is clear more often when the sun is strong (+1.3% to +20.6% sun-weighted), because it can dim partially. That needs film that dims in steps, which is not confirmed (section 8).
5. **Switch count is the wrong measure for SGF.** It changes by small steps, so it can switch more often (9 against 8 on the Sunny day) while its total change is much lower (3.0 against 8.0).
6. **Gain on all 16 cells is small by construction.** 12 cells are never shaded and the Grade A cell is always opaque in sun.
7. **Metrics.** The engine function `compare_baseline_vs_adaptive`, the tuner and the API `metrics` block used to clip negative results to zero and to count a violation only when the baseline had kept the cap. They now report signed values and count against the cap itself (section 10). The first report of this experiment had printed worse-than-baseline results as 0.0%.

### 7.1 Sensitivity

Same scenarios, one setting changed at a time (ranges over the scenarios; Grade B cells).

| Variant | Mode | Flicker | Daylight (opacity) | Daylight (sun-weighted) | Above cap at q90, baseline / mode (total) |
|---|---|---|---|---|---|
| Cap 50 W/m2 (earlier setting) | smoothing | +0.0% to +60.0% | -8.5% to +0.0% | -19.4% to +0.0% | 27 / 0 |
| | replace, PDLC | +62.5% to +85.0% | -9.4% to +0.0% | -19.4% to +0.0% | 27 / 0 |
| | replace, SGF | +62.5% to +85.0% | -5.9% to +6.5% | -0.2% to +48.2% | 27 / 0 |
| 10-minute steps | smoothing | +42.9% to +69.2% | -3.5% to +0.0% | -4.6% to +0.0% | 3 / 0 |
| | replace, PDLC | +57.1% to +76.9% | -1.8% to +0.0% | -1.1% to +1.6% | 3 / 0 |
| | replace, SGF | +57.1% to +76.9% | -0.4% to +2.1% | +3.7% to +20.3% | 3 / 0 |
| Band half as wide | smoothing | +0.0% to +42.9% | -2.7% to +0.0% | -3.9% to +0.0% | 9 / 0 |
| | replace, PDLC | +62.5% to +78.6% | -3.5% to +0.0% | -3.9% to +0.8% | 9 / 0 |
| | replace, SGF | +62.5% to +78.6% | -0.9% to +2.3% | +5.0% to +22.5% | 9 / 0 |
| Band 1.5 times as wide | smoothing | +0.0% to +42.9% | -2.7% to +0.0% | -3.9% to +0.0% | 15 / 3 |
| | replace, PDLC | +62.5% to +78.6% | -4.4% to +0.0% | -5.6% to +0.8% | 15 / 0 |
| | replace, SGF | +62.5% to +78.6% | -2.9% to +0.4% | -1.2% to +7.7% | 15 / 0 |

- The conclusions hold in every variant, with one exception: with a band 1.5 times as wide, smoothing leaves 3 cell-steps above the cap at q90 (FR-02 alone leaves 15). The replace modes hedge for the band itself and keep 0. Smoothing never does worse than FR-02.
- A wider forecast band costs daylight in the replace modes; a narrower one gives some back. The real band of the FR-01.4 model should be used before quoting a number.
- Sampling every 10 minutes (what the nowcast refresh allows) changes the flicker reduction of the replace modes a little (57.1% to 76.9%); smoothing gains more on the cloudy days (up to +69.2%).

### 7.2 Recommended weights (replace mode only)

Smoothing does not use `w_flicker`, `w_daylight` or `kappa_B`; it uses `brightening_margin_W_m2`, `min_gap_seconds`, `brightening_hold_seconds` and `kappa_B` / `kappa_C` for the hysteresis margin, all at their defaults. These were not tuned.

Sweep of `w_flicker` (0, 5, 20, 60) and `kappa_B` (0.8, 1.0, 1.2, 1.6), averaged over the four scenarios (table in the benchmark report).

| Finding | Evidence |
|---|---|
| `w_flicker` has a ceiling | PDLC +73.2% from 5 upward; SGF +73.2% from 20 upward |
| `kappa_B` is the protection lever for SGF | SGF with `kappa_B` 0.8 leaves 6 cell-steps above the cap at q90; 1.0 and above leave none |
| `kappa_B` barely changes flicker | Same flicker for 0.8 to 1.6 at a given `w_flicker` |
| Cost of a higher `kappa_B` | SGF Grade B daylight falls from +1.1% at 1.0 to +0.4% at 1.6 (`w_flicker` 0) |

Recommended starting point, by the rule "no cap exceeded at q50 or q90 in any scenario, then the largest flicker change, then the largest Grade B daylight gain":

| Mode | w_flicker | kappa_B | Flicker | Grade B daylight (opacity) |
|---|---|---|---|---|
| PDLC | 5 | 1.0 (0.8 gives the same result; 1.0 shares one value with SGF) | +73.2% | -1.8% |
| SGF | 20 | 1.0 | +73.2% | +0.0% |

The rule is ours and open to review. `w_damage` (1000) and `w_daylight` (1.0) were not varied. `AdaptivePolicyTuner` has only five candidates and picks per scenario; its choice is a default.

---

## 8. Film material research (LG SGF, PDLC, SPD)

What could be confirmed and what could not. Search results were read as summaries unless marked "page read".

| Question | Finding | Source |
|---|---|---|
| What is LG Chem SGF? | A switchable glazing film for vehicles (sunroofs, glass roofs, side windows). Transmittance is adjusted "freely" with voltage. Two grades: PDLC ("Haze Black", scattering-type shading) and NCLC ("Clear Black", "uniform dimming control"). Page read. | [LG Chem SGF](https://www.lgchem.com/product-detail/sgf?lang=en_US) |
| Numeric specs of SGF (visible transmittance range, haze, response time, voltage, power, size) | Not on the page. Datasheets only by inquiry. Not found elsewhere. | same page |
| Is SGF continuous or on/off? | Not stated. "Dimming control" is listed; NCLC is described as uniform dimming. | same page |
| What does an opaque PDLC do to light? | It scatters light instead of absorbing it; one study says PDLC windows modulate scattering, not transmission. | [ISES paper](https://proceedings.ises.org/scholar/paper/100062) |
| Visible light through opaque PDLC | One government technical note says 2% to 65% depending on dye. Vendor SHGC values for the off state are 0.40 and 0.32 (on: 0.49 and 0.38) for two products. | [Alberta technical note](https://www.alberta.ca/system/files/tec-dtseries21pdlcglass.pdf), [BenQ Materials](https://www.benqmaterials.com/product-detail/60/) |
| SPD transmission range, switching time, power | A search summary reports a typical range of 20% to 60%, switching from hundreds of milliseconds to about a second, and about 5 W/m2 in one model. The source page was not identified and no manufacturer datasheet was found, so these are not usable as values. | none |

Consequences for this project:

- **No verified SGF preset can be written.** The film values in the experiment (0.03 opaque, 0.72 clear, glazing 0.85) and the engine defaults (0.05, 0.70) are assumptions and are labeled as such. Ask LG Chem for a datasheet, or measure a sample, before using them in a claim.
- **PDLC does not block energy the way the model assumes.** The model lowers "direct irradiance" by the film transmittance. A scattering film turns the direct beam into diffuse light, so the energy that reaches a protected object may be much higher than the model says.
- **SGF is an automotive product.** Window-sized architectural use was not confirmed.

---

## 9. Review of the assumptions behind this document

| # | Assumption | Problem | What to do |
|---|---|---|---|
| 1 | "+25% to +45% daylight" and "50% to 80% flicker removed" were targets | Hypothetical targets from the first draft, with no source (confirmed by the author). The experiment did not support the daylight figure | Replaced by computed results (section 7) |
| 2 | The forecast is also the truth | The simulation has no separate real irradiance. Protection can only be checked at q50 and q90 | A fair test needs real days: forecast and measured DNI side by side |
| 3 | The "FR-02 baseline" is FR-02 | In the CLI it is a three-line rule. The API compares with the real FR-02 plan | Run the comparison on real scenes through the API |
| 4 | FR-04 is a smoothing step after FR-02 | The requirement (`docs/features.md`) says FR-04 smooths FR-02's plan; the DP controller decides from irradiance itself | Decided on 2026-11-10: the default is smoothing (`mode: "smooth_fr02"`); the DP controller stays as the experimental `replace` mode (section 11) |
| 5 | Cap of 50 W/m2 for Grade B | The scene policy uses 0, 30 and 70 W/m2 (`ui_scene.py`) | Done: the main run uses 30, 50 is a sensitivity run. Grade C (70) is not simulated |
| 6 | The film lowers direct irradiance by its transmittance | See section 8: scattering | Use total solar transmittance of a real film |
| 7 | Opacity measures daylight | It ignores diffuse light and the sun's strength | Partly done: a sun-weighted transmittance was added. Measured illuminance is still missing |
| 8 | Quantile band widths (0.6 and 1.4 of q50, and so on) | Made up; the real band comes from the FR-01.4 model | Partly done: half and 1.5 times the width were run. The real band is still needed |
| 9 | Time step | FR-01 uses 300 s steps and FR-04.2 holds for 600 s; the nowcast refreshes every 10 minutes. Clouds shorter than that are invisible to the controller | Partly done: a 10-minute-step run was added. Say it when presenting |
| 10 | FR-04 improves the app | FR-04 is only an API. The web app and FR-05 do not call it | Wire it in, or present it as a simulation result |
| 11 | Metrics are honest | The engine metric and the tuner clipped negative values and counted a violation only relative to the baseline | Done in this change (section 10) |
| 12 | Cells are independent | FR-04.4 (neighbour cell when a path crosses a cell boundary) is already met by FR-02.1: `cells_for_hit` widens each hit to one tile and shades every cell it overlaps. The FR-04 `enable_edge_margin` option is a cruder second margin (it shades the four neighbours of every hit cell; `edge_margin_threshold_m` is not used) | Keep it off by default and in smoothing mode, where it is ignored. Turning it on would shade neighbours twice |
| 13 | The API shows the sun to FR-04 | `adaptive-shading-plan` asked FR-01 for the prediction without diagnostic detail, so no exposed samples came back: in `replace` mode FR-04 saw no sun and shaded nothing (FR-02 shaded 68 cells in the same test scene), smoothing had no irradiance for its hysteresis, and every violation count was a meaningless 0 | Fixed (`detail_level="diagnostic"`), with a regression test. Results of the API before the fix must not be quoted |
| 14 | The experiment tests the API | It calls the engine directly and never the API, so it could not catch item 13 | There is no end-to-end experiment through the API with a real Scene yet |
| 15 | "Switch change" is always a ratio | When FR-02 has no switch at all (for example a day that is overcast from morning to night) there is no percentage; the engine and the API give 0.0 and the report prints n/a | Read `baseline_total_change` and `adaptive_total_change` in that case |

Still open: 2 (real forecast and measured irradiance together), 3 (the experiment's FR-02 baseline is still a simple rule; the API uses the real plan), 6 (real film data), 10 (connection to the app and FR-05). Not checked at all: real hardware behaviour, flicker as people experience it, energy use and cost.

---

## 10. Comparison metrics: what changed

| Before | Now |
|---|---|
| `flicker_reduction_pct` and `daylight_gain_pct` were clipped at 0 | Signed: positive is better than the baseline, negative is worse. The daylight percentage keeps the old floor: a baseline mean opacity below 0.01 counts as 0.01, so a nearly unshaded baseline does not give a huge percentage |
| A violation counted only if the baseline kept the cap and the adaptive plan did not | Counted against the cap itself, for the adaptive plan and for the baseline, at q50 and at q90 |
| Switch count was the only flicker measure | `total_change_reduction_pct` (sum of the opacity changes) added; the switch count stays |
| The tuner scored the clipped switch reduction and required no violation relative to the baseline | The tuner scores the total-change reduction plus half the daylight gain (signed) and requires no more violations than the baseline at q50 and at q90. If no candidate qualifies it returns the one with the fewest |
| The Korean summary text said "improved" for any value | The text says "improved" or "got worse" by the sign |

New fields in `AdaptiveComparisonMetrics` (engine and API): `baseline_violation_count`, `protection_violation_count_q90`, `baseline_violation_count_q90`, `baseline_total_change`, `adaptive_total_change`, `total_change_reduction_pct`. They have defaults, so existing readers keep working. `protection_violation_count` now means "above the cap if q50 is true". The web app does not read these fields.

---

## 11. Modes of the adaptive-shading-plan API

`POST /api/scenes/{scene_id}/adaptive-shading-plan` and `POST /api/scenes/{scene_id}/thinq-hub-sync` take `mode` in the plan request. The response repeats it.

| | `smooth_fr02` (default) | `replace` (experimental) |
|---|---|---|
| What it does | Takes the FR-02 plan of each cell and keeps it steady | Decides each cell from the irradiance band with dynamic programming |
| FR-04.1 gap filling, FR-04.2 holding time | Yes | Yes |
| FR-04.3 hysteresis | Yes, as a hard rule: a B or C cell that FR-02 would brighten stays dark until the clear film is below the cap by the margin | Yes, as a cost inside the optimization |
| Film levels | On/off only (FR-02 gives on/off) | PDLC on/off, SGF five levels |
| Weights | Defaults, not tuned | Tuned when `auto_tune_weights` is true |
| `hardware_mode`, `enable_edge_margin`, `auto_tune_weights` | Ignored | Used |
| States `infeasible` and `unknown` of FR-02 | Kept | Not produced |
| Safety | Never brighter than the FR-02 plan at any step (checked by a randomized test) | Can be brighter than FR-02 where the optimizer decides so |

Notes:

- The response structure is the same in both modes (`baseline_plan`, `stabilized_plan`, `dimming_trajectories`, `metrics`). In smoothing mode `dimming_trajectories` has opacities 0 and 1 only.
- `hardware_mode` names how the film behaves: `PDLC` is on/off, `LG_SGF` and `SPD` have steps. It is not a user choice; the film is a fact of the window. LG Chem sells SGF as a product family in which PDLC is one grade (section 8), so the label "PDLC vs SGF" really means "on/off versus steps". A screen that offers this choice makes sense only for `replace`, as a comparison.
- Edge margin: FR-04.4 is met by FR-02.1 (section 9, item 12). The option is ignored in smoothing mode.
- `thinq-hub-sync` has its own top-level `hardware_mode`. It describes the film that receives the payload and is used in both modes; only the `hardware_mode` inside `plan_request` is ignored in smoothing.
- The metrics of section 10 are computed in both modes against the FR-02 plan.
