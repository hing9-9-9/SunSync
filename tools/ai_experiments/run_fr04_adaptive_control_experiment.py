"""FR-04 Uncertainty-Aware Adaptive Smart Film Control Experiment.

Simulates a full day of intermittent clouds (Sunny, Partly Cloudy Rapid-Flicker, High Uncertainty Squall)
on a library reading room scene, comparing:
  1. Rule-based FR-02 baseline (binary PDLC)
  2. FR-04 Adaptive PDLC (discrete 0/1 with DP + stabilization)
  3. FR-04 Adaptive LG SGF Continuous Dimming (multi-level DP + stabilization)

Outputs a comprehensive Markdown and JSON benchmark report for teammate 0nsetofspring.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

# Ensure root workspace directory is on sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from engine.fr04 import (
    AdaptivePolicyTuner,
    AdaptiveWeights,
    CellObservation,
    CellState,
    OpticalProfileData,
    ProtectionGrade,
    apply_fr04_stabilization,
    compare_baseline_vs_adaptive,
    count_cap_violations,
    optimize_cell_dp,
    smooth_fr02_plan,
)


def generate_solar_profile(
    scenario: str,
    start_time: datetime,
    duration_minutes: int = 600,  # 10 hours (08:00 to 18:00)
    interval_seconds: int = 300,  # 5-minute steps (120 steps)
    band_scale: float = 1.0,  # widens (>1) or narrows (<1) the q10..q90 band around q50
) -> list[tuple[datetime, float, float, float]]:
    """Generate time series of (timestamp, q10, q50, q90) direct irradiance."""
    n_steps = duration_minutes * 60 // interval_seconds
    timeline: list[tuple[datetime, float, float, float]] = []

    for step in range(n_steps):
        t = start_time + timedelta(seconds=step * interval_seconds)
        # Solar diurnal arch: zenith at middle of the day
        fraction_of_day = step / max(1, n_steps - 1)
        solar_altitude = math.sin(math.pi * fraction_of_day)
        base_clear_sky = max(0.0, 850.0 * solar_altitude)

        if scenario == "Sunny":
            # Clear sky with gentle micro-haze
            q50 = base_clear_sky
            q10 = 0.95 * q50
            q90 = 1.05 * q50

        elif scenario == "Partly Cloudy Rapid-Flicker":
            # Alternating cloud pass-overs every 15-25 minutes
            cycle = math.sin(step * 0.45) * math.cos(step * 0.15)
            cloud_attenuation = 0.20 if cycle < -0.15 else 1.0
            q50 = base_clear_sky * cloud_attenuation
            # High uncertainty around cloud edges
            q10 = 0.60 * q50
            q90 = min(base_clear_sky, 1.40 * q50 + 50.0)

        elif scenario == "High Uncertainty Squall":
            # Convective squall with huge quantile divergence
            noise = (math.sin(step * 0.8) + math.cos(step * 1.3)) * 0.5
            cloud_attenuation = max(0.1, min(1.0, 0.5 + noise * 0.4))
            q50 = base_clear_sky * cloud_attenuation
            q10 = 0.35 * q50
            q90 = min(base_clear_sky, 1.80 * q50 + 100.0)
        elif scenario == "Overcast Low Irradiance":
            # Thick cloud all day: direct irradiance stays in 100-300 W/m2, where partial
            # dimming can already meet a low cap. Added to show where continuous dimming helps.
            wobble = 1.0 + 0.15 * math.sin(step * 0.6)
            q50 = (100.0 + 200.0 * solar_altitude) * wobble
            q10 = 0.70 * q50
            q90 = 1.30 * q50

        else:
            raise ValueError(f"Unknown scenario: {scenario}")

        q10 = max(0.0, q50 - band_scale * (q50 - q10))
        q90 = q50 + band_scale * (q90 - q50)
        timeline.append((t, round(q10, 2), round(q50, 2), round(q90, 2)))

    return timeline


SWITCH_STEP = 0.01  # an opacity change larger than this counts as one switch


def _cells_of(
    observations_by_cell: Mapping[tuple[str, int, int], Sequence[CellObservation]],
    grades: tuple[str, ...] | None,
) -> list[tuple[str, int, int]]:
    return [
        key
        for key, obs in observations_by_cell.items()
        if grades is None or obs[0].strictest_grade in grades
    ]


def _series_stats(
    opacities: Mapping[tuple[str, int, int], Sequence[float]],
    keys: Sequence[tuple[str, int, int]],
) -> tuple[int, float, float]:
    """(switch count, total opacity change, mean opacity) over the given cells."""
    switches, variation, total, steps = 0, 0.0, 0.0, 0
    for key in keys:
        series = opacities[key]
        total += sum(series)
        steps += len(series)
        for before, after in pairwise(series):
            change = abs(after - before)
            variation += change
            switches += change > SWITCH_STEP
    return switches, variation, total / max(1, steps)


def _sun_weighted_transmittance(
    opacities: Mapping[tuple[str, int, int], Sequence[float]],
    observations_by_cell: Mapping[tuple[str, int, int], Sequence[CellObservation]],
    keys: Sequence[tuple[str, int, int]],
    profile: OpticalProfileData,
) -> float:
    """Film transmittance averaged over the cells and steps, weighted by the median irradiance.

    A film that is clear when the sun is strong scores higher than one that is clear only when
    the sun is weak. It is a better stand-in for daylight than the mean opacity.
    """
    weighted, total = 0.0, 0.0
    for key in keys:
        for obs, opacity in zip(observations_by_cell[key], opacities[key], strict=False):
            weighted += profile.film_transmittance(opacity) * obs.irradiance_q50
            total += obs.irradiance_q50
    return weighted / total if total > 0 else 0.0


def _change_pct(before: float, after: float) -> float | None:
    """Reduction of `after` against `before` in percent; negative means it got worse."""
    return None if before <= 0 else (before - after) / before * 100.0


def evaluate_mode(
    baseline: Mapping[tuple[str, int, int], Sequence[float]],
    adaptive: Mapping[tuple[str, int, int], Sequence[float]],
    observations_by_cell: Mapping[tuple[str, int, int], Sequence[CellObservation]],
    profile: OpticalProfileData,
) -> dict[str, Any]:
    """Signed metrics of one adaptive mode against the rule baseline (nothing is clipped)."""
    out: dict[str, Any] = {}
    for scope, grades in (("all_cells", None), ("grade_b_cells", ("B",))):
        keys = _cells_of(observations_by_cell, grades)
        base_sw, base_var, base_mean = _series_stats(baseline, keys)
        ad_sw, ad_var, ad_mean = _series_stats(adaptive, keys)
        base_sun = _sun_weighted_transmittance(baseline, observations_by_cell, keys, profile)
        ad_sun = _sun_weighted_transmittance(adaptive, observations_by_cell, keys, profile)
        out[scope] = {
            "baseline_switches": base_sw,
            "adaptive_switches": ad_sw,
            "baseline_total_change": round(base_var, 3),
            "adaptive_total_change": round(ad_var, 3),
            "switch_reduction_pct": _change_pct(base_sw, ad_sw),
            "flicker_reduction_pct": _change_pct(base_var, ad_var),
            "baseline_mean_opacity": round(base_mean, 4),
            "adaptive_mean_opacity": round(ad_mean, 4),
            "daylight_gain_pct": _change_pct(base_mean, ad_mean),
            "baseline_sun_weighted_transmittance": round(base_sun, 4),
            "adaptive_sun_weighted_transmittance": round(ad_sun, 4),
            "sun_weighted_gain_pct": _change_pct(ad_sun, base_sun),
        }
    out["baseline_violations_q50"] = count_cap_violations(
        baseline, observations_by_cell, profile, "q50"
    )
    out["baseline_violations_q90"] = count_cap_violations(
        baseline, observations_by_cell, profile, "q90"
    )
    out["adaptive_violations_q50"] = count_cap_violations(
        adaptive, observations_by_cell, profile, "q50"
    )
    out["adaptive_violations_q90"] = count_cap_violations(
        adaptive, observations_by_cell, profile, "q90"
    )
    return out


CAP_B_W_M2 = 30.0  # Grade B cap of the app policy (backend/app/ui_scene.py)


def build_inputs(
    timeline: Sequence[tuple[datetime, float, float, float]],
    cap_b: float = CAP_B_W_M2,
) -> tuple[
    dict[tuple[str, int, int], list[CellObservation]],
    dict[tuple[str, int, int], list[float]],
]:
    """Library reading room: 4x4 film grid with one Grade A cell, three Grade B cells, rest D.

    Returns the per-cell observations and the rule-based baseline opacities.
    """
    rows, cols = 4, 4

    # Assign cell zones:
    # Cell (1, 1): Rare Manuscript Archive (Grade A, hard constraint 0.0 W/m2)
    # Cells (1, 2), (2, 1), (2, 2): Study Desks (Grade B, cap `cap_b`)
    # Remaining cells: General Walkway (Grade D, no cap)
    observations_by_cell: dict[tuple[str, int, int], list[CellObservation]] = defaultdict(list)
    baseline_opacities: dict[tuple[str, int, int], list[float]] = defaultdict(list)

    for r in range(rows):
        for c in range(cols):
            cell_key = ("window_south", r, c)
            grade: ProtectionGrade | None
            cap: float | None

            if r == 1 and c == 1:
                grade = "A"
                cap = 0.0
            elif (r in (1, 2)) and (c in (1, 2)):
                grade = "B"
                cap = cap_b
            else:
                grade = "D"
                cap = None

            for t, q10, q50, q90 in timeline:
                reasons = (
                    (("desk_region" if grade == "B" else "archive_region"), grade)
                    if grade in ("A", "B")
                    else ()
                )
                obs = CellObservation(
                    timestamp=t,
                    window_id="window_south",
                    row=r,
                    col=c,
                    strictest_grade=grade,
                    cap_W_m2=cap,
                    irradiance_q10=q10,
                    irradiance_q50=q50,
                    irradiance_q90=q90,
                    reasons=(reasons,) if reasons else (),
                )
                observations_by_cell[cell_key].append(obs)

                # Rule-based FR-02 baseline decision:
                # If Grade A: opaque if irradiance > 0
                # If Grade B: opaque if irradiance > cap
                # Else: transparent
                if grade == "A" and q50 > 0.0 or grade == "B" and q50 > cap_b:
                    b_op = 1.0
                else:
                    b_op = 0.0
                baseline_opacities[cell_key].append(b_op)
    return observations_by_cell, baseline_opacities


def simulate_scene(
    scenario_name: str,
    timeline: Sequence[tuple[datetime, float, float, float]],
    optical_profile: OpticalProfileData,
    weights: AdaptiveWeights,
    interval_seconds: int = 300,
    cap_b: float = CAP_B_W_M2,
) -> dict[str, Any]:
    """Simulate library reading room: 4x4 film grid with Desk (Grade B) and Art (Grade A)."""
    observations_by_cell, baseline_opacities = build_inputs(timeline, cap_b)

    # 1. Run FR-04 Adaptive PDLC (Discrete) with auto-tuner
    tuner = AdaptivePolicyTuner()
    best_weights_pdlc, _ = tuner.tune(
        observations_by_cell=observations_by_cell,
        baseline_opacities_by_cell=baseline_opacities,
        optical_profile=optical_profile,
        interval_seconds=interval_seconds,
        hardware_mode="PDLC",
    )
    pdlc_adaptive_opacities: dict[tuple[str, int, int], list[float]] = {}
    for cell_key, obs_seq in observations_by_cell.items():
        dp_steps = optimize_cell_dp(
            obs_seq, optical_profile, best_weights_pdlc, hardware_mode="PDLC"
        )
        stabilized = apply_fr04_stabilization(
            dp_steps, interval_seconds, best_weights_pdlc, optical_profile
        )
        pdlc_adaptive_opacities[cell_key] = [s.opacity for s in stabilized]

    metrics_pdlc = compare_baseline_vs_adaptive(
        baseline_opacities_by_cell=baseline_opacities,
        adaptive_opacities_by_cell=pdlc_adaptive_opacities,
        observations_by_cell=observations_by_cell,
        optical_profile=optical_profile,
        tuned_weights={
            "w_flicker": best_weights_pdlc.w_flicker,
            "w_daylight": best_weights_pdlc.w_daylight,
            "kappa_B": best_weights_pdlc.kappa_B,
        },
    )

    # 2. Run FR-04 Adaptive LG SGF Continuous Dimming with auto-tuner
    best_weights_sgf, _ = tuner.tune(
        observations_by_cell=observations_by_cell,
        baseline_opacities_by_cell=baseline_opacities,
        optical_profile=optical_profile,
        interval_seconds=interval_seconds,
        hardware_mode="LG_SGF",
    )
    sgf_adaptive_opacities: dict[tuple[str, int, int], list[float]] = {}
    for cell_key, obs_seq in observations_by_cell.items():
        dp_steps = optimize_cell_dp(
            obs_seq, optical_profile, best_weights_sgf, hardware_mode="LG_SGF"
        )
        stabilized = apply_fr04_stabilization(
            dp_steps, interval_seconds, best_weights_sgf, optical_profile
        )
        sgf_adaptive_opacities[cell_key] = [s.opacity for s in stabilized]

    metrics_sgf = compare_baseline_vs_adaptive(
        baseline_opacities_by_cell=baseline_opacities,
        adaptive_opacities_by_cell=sgf_adaptive_opacities,
        observations_by_cell=observations_by_cell,
        optical_profile=optical_profile,
        tuned_weights={
            "w_flicker": best_weights_sgf.w_flicker,
            "w_daylight": best_weights_sgf.w_daylight,
            "kappa_B": best_weights_sgf.kappa_B,
        },
    )

    # 3. FR-04 as a smoothing step on the FR-02 plan (the default of the API)
    smooth_opacities: dict[tuple[str, int, int], list[float]] = {}
    for cell_key, obs_seq in observations_by_cell.items():
        base = baseline_opacities[cell_key]
        states: list[CellState] = ["opaque" if value > 0 else "transparent" for value in base]
        smoothed = smooth_fr02_plan(
            base, states, obs_seq, optical_profile, weights, interval_seconds
        )
        smooth_opacities[cell_key] = [step.opacity for step in smoothed]

    return {
        "scenario": scenario_name,
        "baseline_switches": metrics_pdlc.baseline_switch_count,
        "baseline_mean_opacity": metrics_pdlc.baseline_mean_opacity,
        "pdlc": asdict(metrics_pdlc),
        "lg_sgf": asdict(metrics_sgf),
        "signed_metrics": {
            "smooth": evaluate_mode(
                baseline_opacities, smooth_opacities, observations_by_cell, optical_profile
            ),
            "pdlc": evaluate_mode(
                baseline_opacities, pdlc_adaptive_opacities, observations_by_cell, optical_profile
            ),
            "lg_sgf": evaluate_mode(
                baseline_opacities, sgf_adaptive_opacities, observations_by_cell, optical_profile
            ),
        },
        "tuned_weights": {
            "pdlc": metrics_pdlc.tuned_weights,
            "lg_sgf": metrics_sgf.tuned_weights,
        },
    }


SWEEP_W_FLICKER = (0.0, 5.0, 20.0, 60.0)
SWEEP_KAPPA_B = (0.8, 1.0, 1.2, 1.6)  # kappa_B must stay >= kappa_C (0.8)


def _mean(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def sweep_weights(
    scenario_inputs: Sequence[
        tuple[
            str,
            Mapping[tuple[str, int, int], Sequence[CellObservation]],
            Mapping[tuple[str, int, int], Sequence[float]],
        ]
    ],
    profile: OpticalProfileData,
    weights: AdaptiveWeights,
    interval_seconds: int,
) -> list[dict[str, Any]]:
    """Run every (mode, w_flicker, kappa_B) pair on every scenario and summarise each pair."""
    rows: list[dict[str, Any]] = []
    for mode, hardware in (("pdlc", "PDLC"), ("lg_sgf", "LG_SGF")):
        for w_flicker in SWEEP_W_FLICKER:
            for kappa_b in SWEEP_KAPPA_B:
                tried = replace(weights, w_flicker=w_flicker, kappa_B=kappa_b)
                per_scenario = []
                for _, observations, baseline in scenario_inputs:
                    adaptive = {
                        key: [
                            step.opacity
                            for step in apply_fr04_stabilization(
                                optimize_cell_dp(seq, profile, tried, hardware_mode=hardware),
                                interval_seconds,
                                tried,
                                profile,
                            )
                        ]
                        for key, seq in observations.items()
                    }
                    per_scenario.append(evaluate_mode(baseline, adaptive, observations, profile))
                rows.append(
                    {
                        "mode": mode,
                        "w_flicker": w_flicker,
                        "kappa_B": kappa_b,
                        "mean_flicker_reduction_pct": _mean(
                            [m["all_cells"]["flicker_reduction_pct"] for m in per_scenario]
                        ),
                        "mean_daylight_gain_grade_b_pct": _mean(
                            [m["grade_b_cells"]["daylight_gain_pct"] for m in per_scenario]
                        ),
                        "max_violations_q50": max(
                            m["adaptive_violations_q50"] for m in per_scenario
                        ),
                        "max_violations_q90": max(
                            m["adaptive_violations_q90"] for m in per_scenario
                        ),
                    }
                )
    return rows


def recommend(rows: Sequence[dict[str, Any]], mode: str) -> dict[str, Any] | None:
    """Rule used in the report: no cap exceeded at q50 or q90 in any scenario, then the
    largest mean flicker reduction, then the largest Grade B daylight gain."""
    safe = [
        r
        for r in rows
        if r["mode"] == mode and r["max_violations_q50"] == 0 and r["max_violations_q90"] == 0
    ]
    if not safe:
        return None
    return max(
        safe,
        key=lambda r: (
            r["mean_flicker_reduction_pct"] or 0.0,
            r["mean_daylight_gain_grade_b_pct"] or 0.0,
        ),
    )


REPLACE_MODES = (
    ("pdlc", "FR-04 replace, PDLC (on/off)"),
    ("lg_sgf", "FR-04 replace, SGF (5 levels)"),
)
MODES = (("smooth", "FR-04 smoothing of FR-02 (default)"), *REPLACE_MODES)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _range(values: Sequence[float | None]) -> str:
    known = [v for v in values if v is not None]
    return "n/a" if not known else f"{min(known):+.1f}% to {max(known):+.1f}%"


def build_report(
    results: Sequence[dict[str, Any]],
    profile: OpticalProfileData,
    weights: AdaptiveWeights,
    sweep: Sequence[dict[str, Any]],
    sensitivity: Sequence[dict[str, Any]],
    cap_b: float,
) -> str:
    """Markdown report; every number comes from the simulation results (nothing is hard-coded)."""

    def column(mode: str, scope: str, key: str) -> list[float | None]:
        return [r["signed_metrics"][mode][scope][key] for r in results]

    lines = [
        "# SunSync FR-04 Adaptive Smart Film Control Benchmark Report",
        "",
        (
            "Generated by `tools/ai_experiments/run_fr04_adaptive_control_experiment.py`. "
            "Every number below is computed by that run; re-run it to refresh this file."
        ),
        "",
        (
            "**Test scene**: one 4x4 south window; 1 Grade A cell, 3 Grade B cells (cap "
            + f"{cap_b:g}"
            + " W/m2, the Grade B cap of the app policy), "
            "12 Grade D cells (never shaded). Synthetic irradiance, 5-minute steps, 08:00-18:00."
        ),
        (
            f"**Optical profile used**: glazing {profile.glazing_direct_transmittance}, film "
            f"{profile.film_min_direct_transmittance} (opaque) to {profile.film_max_direct_transmittance} "
            "(clear). These are assumed values, not measured ones (see Limitations)."
        ),
        "",
        "**Modes compared**",
        "",
        "- *FR-02 rule baseline*: the plan FR-02 would make (here a three-line rule).",
        (
            "- *FR-04 smoothing of FR-02* (default of the API): keeps the FR-02 plan steady "
            "(FR-04.1 gap filling, FR-04.2 holding time, FR-04.3 hysteresis) and decides nothing "
            "new. It is never brighter than the FR-02 plan. On/off film only."
        ),
        (
            "- *FR-04 replace*: the experimental mode that decides from the irradiance band with "
            "dynamic programming; PDLC is on/off, SGF has five levels."
        ),
        "",
        "## Summary (range over the scenarios; positive is better than the rule baseline)",
        "",
        (
            "| Mode | Flicker change, total opacity change | Switch count change | "
            "Daylight gain, all 16 cells | Daylight gain, Grade B cells | "
            "Sun-weighted gain, Grade B cells |"
        ),
        "|---|---|---|---|---|---|",
    ]
    for mode, label in MODES:
        lines.append(
            f"| {label} | {_range(column(mode, 'all_cells', 'flicker_reduction_pct'))} | "
            f"{_range(column(mode, 'all_cells', 'switch_reduction_pct'))} | "
            f"{_range(column(mode, 'all_cells', 'daylight_gain_pct'))} | "
            f"{_range(column(mode, 'grade_b_cells', 'daylight_gain_pct'))} | "
            f"{_range(column(mode, 'grade_b_cells', 'sun_weighted_gain_pct'))} |"
        )
    lines += [
        "",
        "## Flicker (all 16 cells)",
        "",
        (
            "`Total change` is the sum of |opacity change| between steps; it grows with the size of "
            "each change, not only with their number. A negative reduction means the mode is worse than "
            "the baseline."
        ),
        "",
        "| Scenario | Mode | Switches | Switch change | Total change | Flicker change |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        first = r["signed_metrics"]["pdlc"]["all_cells"]
        lines.append(
            f"| **{r['scenario']}** | FR-02 rule baseline | {first['baseline_switches']} | - | "
            f"{first['baseline_total_change']:.2f} | - |"
        )
        for mode, label in MODES:
            m = r["signed_metrics"][mode]["all_cells"]
            lines.append(
                f"| | {label} | {m['adaptive_switches']} | {_pct(m['switch_reduction_pct'])} | "
                f"{m['adaptive_total_change']:.2f} | {_pct(m['flicker_reduction_pct'])} |"
            )
    lines += [
        "",
        "## Daylight (mean opacity; lower opacity means more daylight)",
        "",
        (
            "Opacity is a stand-in for daylight: it ignores diffuse light and the sun's strength at "
            "each moment. The all-cell value is diluted by the 12 Grade D cells that are never shaded; "
            "the Grade B value looks only at the cells that are actually controlled."
        ),
        "",
        (
            "| Scenario | Mode | Mean opacity, all | Gain, all | Mean opacity, Grade B | "
            "Gain, Grade B | Sun-weighted gain, Grade B |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        base_all = r["signed_metrics"]["pdlc"]["all_cells"]["baseline_mean_opacity"]
        base_b = r["signed_metrics"]["pdlc"]["grade_b_cells"]["baseline_mean_opacity"]
        lines.append(
            f"| **{r['scenario']}** | FR-02 rule baseline | {base_all:.3f} | - | {base_b:.3f} | - | - |"
        )
        for mode, label in MODES:
            a = r["signed_metrics"][mode]["all_cells"]
            b = r["signed_metrics"][mode]["grade_b_cells"]
            lines.append(
                f"| | {label} | {a['adaptive_mean_opacity']:.3f} | {_pct(a['daylight_gain_pct'])} | "
                f"{b['adaptive_mean_opacity']:.3f} | {_pct(b['daylight_gain_pct'])} | "
                f"{_pct(b['sun_weighted_gain_pct'])} |"
            )
    lines += [
        "",
        "## Protection (cell-steps above the cap)",
        "",
        (
            "Counted against the cap directly, not against the baseline. `q50` assumes the median "
            "forecast is true; `q90` assumes the sunny end of the forecast band is true. The simulation "
            "has no separate 'real' irradiance, so these are the only two checks it can make."
        ),
        "",
        "| Scenario | Mode | Above cap at q50 | Above cap at q90 |",
        "|---|---|---|---|",
    ]
    for r in results:
        sm = r["signed_metrics"]["pdlc"]
        lines.append(
            f"| **{r['scenario']}** | FR-02 rule baseline | {sm['baseline_violations_q50']} | "
            f"{sm['baseline_violations_q90']} |"
        )
        for mode, label in MODES:
            m = r["signed_metrics"][mode]
            lines.append(
                f"| | {label} | {m['adaptive_violations_q50']} | {m['adaptive_violations_q90']} |"
            )
    lines += [
        "",
        "## Weights",
        "",
        "Two sources of weights are shown.",
        "",
        (
            "1. **`AdaptivePolicyTuner`** (used for the tables above) picks, per scenario and "
            "mode, the candidate with the best score among those that do not exceed the cap more "
            "often than the baseline, at q50 and at q90. The score is the reduction of the total "
            "opacity change plus half the daylight gain, both signed. Its candidate list is short "
            "(five), so treat its choice as a default."
        ),
        "",
        "| Scenario | Mode | w_flicker | w_daylight | kappa_B |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        for mode, label in REPLACE_MODES:
            w = r["tuned_weights"][mode]
            lines.append(
                f"| {r['scenario']} | {label} | {w.get('w_flicker')} | {w.get('w_daylight')} | "
                f"{w.get('kappa_B')} |"
            )
    lines += [
        "",
        (
            f"2. **Sweep** over w_flicker and kappa_B with the other weights fixed (w_damage="
            f"{weights.w_damage}, w_daylight={weights.w_daylight}). Each row is averaged over the "
            "scenarios (flicker change is total opacity change, daylight gain is Grade B cells; a "
            "scenario where the baseline has no change is left out of the average). `Max above cap` "
            "is the worst scenario."
        ),
        "",
        (
            "| Mode | w_flicker | kappa_B | Mean flicker change | Mean Grade B daylight gain | "
            "Max above cap q50 | Max above cap q90 |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for r in sweep:
        label = dict(MODES)[r["mode"]]
        lines.append(
            f"| {label} | {r['w_flicker']} | {r['kappa_B']} | "
            f"{_pct(r['mean_flicker_reduction_pct'])} | "
            f"{_pct(r['mean_daylight_gain_grade_b_pct'])} | {r['max_violations_q50']} | "
            f"{r['max_violations_q90']} |"
        )
    lines += [
        "",
        (
            "Recommendation rule (chosen for this report, open to review): no cap exceeded at q50 or "
            "q90 in any scenario, then the largest mean flicker change, then the largest Grade B "
            "daylight gain."
        ),
        "",
    ]
    for mode, label in REPLACE_MODES:
        pick = recommend(sweep, mode)
        if pick is None:
            lines.append(f"- {label}: no tried pair meets the rule.")
        else:
            lines.append(
                f"- {label}: w_flicker={pick['w_flicker']}, kappa_B={pick['kappa_B']} "
                f"(flicker {_pct(pick['mean_flicker_reduction_pct'])}, "
                f"Grade B daylight {_pct(pick['mean_daylight_gain_grade_b_pct'])})."
            )
    lines += [
        "",
        "## Sensitivity",
        "",
        (
            "The same four scenarios run with one setting changed at a time. Each cell is the "
            "range over the scenarios, positive is better than the rule baseline. `Above cap q90` "
            "is the total over the scenarios, for the rule baseline and for the mode in the row."
        ),
        "",
        (
            "| Variant | Mode | Flicker change | Grade B daylight gain | Grade B sun-weighted gain | "
            "Above cap at q90 (baseline / adaptive) |"
        ),
        "|---|---|---|---|---|---|",
    ]
    for variant in sensitivity:
        rs = variant["results"]
        for mode, label in MODES:
            sm = [r["signed_metrics"][mode] for r in rs]
            lines.append(
                f"| {variant['name']} | {label} | "
                f"{_range([m['all_cells']['flicker_reduction_pct'] for m in sm])} | "
                f"{_range([m['grade_b_cells']['daylight_gain_pct'] for m in sm])} | "
                f"{_range([m['grade_b_cells']['sun_weighted_gain_pct'] for m in sm])} | "
                f"{sum(m['baseline_violations_q90'] for m in sm)} / "
                f"{sum(m['adaptive_violations_q90'] for m in sm)} |"
            )
    lines += [
        "",
        "## Limitations",
        "",
        (
            "- The irradiance is synthetic (sine-based), not measured, and the forecast is also "
            "taken as the truth. It shows how the controllers behave on a made-up sky, not how "
            "much they would help on a real day."
        ),
        (
            "- The 'FR-02 rule baseline' here is a three-line rule (opaque above the cap), not the "
            "real FR-02 planner. The API in `backend/app/api/adaptive_control.py` does compare "
            "against the real FR-02 plan."
        ),
        (
            "- The forecast band (q10, q50, q90) is made up; the sensitivity table shows what a "
            "narrower or wider band does. The real band comes from the FR-01.4 model."
        ),
        ("- Only a Grade B cap is simulated (30 W/m2, the app policy). Grade C (70 W/m2) is not."),
        (
            "- The film values (glazing 0.85, film 0.03 to 0.72) are assumptions. No SGF datasheet "
            "with transmittance, haze or response time was found (see "
            "`docs/fr04_adaptive_control_lg_sgf.md`)."
        ),
        (
            "- The model treats a film as lowering direct irradiance. A scattering film (PDLC) "
            "turns direct light into diffuse light and may block much less total energy than this "
            "assumes."
        ),
        (
            "- Daylight is judged by opacity and by the sun-weighted film transmittance. Neither "
            "is measured illuminance."
        ),
        "- Neighbouring cells are controlled one at a time; the edge margin is off.",
        "",
    ]
    return "\n".join(lines)


SCENARIOS = (
    "Sunny",
    "Partly Cloudy Rapid-Flicker",
    "High Uncertainty Squall",
    "Overcast Low Irradiance",
)
START_TIME = datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)
BASE_STEP_SECONDS = 300


def suite_timelines(
    interval_seconds: int = BASE_STEP_SECONDS, band_scale: float = 1.0
) -> list[tuple[str, list[tuple[datetime, float, float, float]]]]:
    """Each scenario's timeline. A longer step samples the same 5-minute sky less often."""
    stride = max(1, interval_seconds // BASE_STEP_SECONDS)
    return [
        (
            name,
            generate_solar_profile(
                name,
                START_TIME,
                duration_minutes=600,
                interval_seconds=BASE_STEP_SECONDS,
                band_scale=band_scale,
            )[::stride],
        )
        for name in SCENARIOS
    ]


def run_suite(
    optical_profile: OpticalProfileData,
    weights: AdaptiveWeights,
    cap_b: float = CAP_B_W_M2,
    interval_seconds: int = BASE_STEP_SECONDS,
    band_scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Run all scenarios with one setting; returns one result per scenario."""
    return [
        simulate_scene(
            name, timeline, optical_profile, weights, interval_seconds=interval_seconds, cap_b=cap_b
        )
        for name, timeline in suite_timelines(interval_seconds, band_scale)
    ]


def main() -> None:
    print("=" * 80)
    print("SunSync FR-04 Uncertainty-Aware Adaptive Smart Film Control Experiment")
    print("Comparing: FR-02 Rule Baseline vs FR-04 Adaptive PDLC vs FR-04 LG SGF Dimming")
    print("=" * 80)

    optical_profile = OpticalProfileData(
        glazing_direct_transmittance=0.85,
        film_min_direct_transmittance=0.03,
        film_max_direct_transmittance=0.72,
    )

    weights = AdaptiveWeights(
        w_damage=1000.0,
        w_daylight=1.0,
        w_flicker=20.0,
        kappa_B=1.2,
        kappa_C=0.8,
        brightening_margin_W_m2=15.0,
        min_gap_seconds=600,
        brightening_hold_seconds=600,
    )

    primary = run_suite(optical_profile, weights, cap_b=CAP_B_W_M2)
    scenario_inputs = [
        (name, *build_inputs(timeline, CAP_B_W_M2))
        for name, timeline in suite_timelines(interval_seconds=300, band_scale=1.0)
    ]
    sweep = sweep_weights(scenario_inputs, optical_profile, weights, interval_seconds=300)
    variants = [
        ("Grade B cap 50 W/m2 (earlier setting)", {"cap_b": 50.0}),
        ("10-minute steps (every second step)", {"interval_seconds": 600}),
        ("Narrow band (half the width)", {"band_scale": 0.5}),
        ("Wide band (one and a half times)", {"band_scale": 1.5}),
    ]
    sensitivity = [
        {
            "name": name,
            "results": run_suite(optical_profile, weights, **{"cap_b": CAP_B_W_M2, **kw}),
        }
        for name, kw in variants
    ]
    report_md = build_report(primary, optical_profile, weights, sweep, sensitivity, CAP_B_W_M2)
    print(report_md)
    results = primary

    # Save artifacts
    reports_dir = WORKSPACE_ROOT / "docs" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / "fr04_adaptive_control_experiment_benchmark.md"
    report_file.write_text(report_md, encoding="utf-8")
    print(f"\n[Artifact Saved] Benchmark Markdown report written to {report_file}")

    json_file = reports_dir / "fr04_adaptive_control_experiment_results.json"
    json_file.write_text(
        json.dumps(
            {"scenarios": results, "weight_sweep": sweep, "sensitivity": sensitivity}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"[Artifact Saved] Benchmark JSON data written to {json_file}")


if __name__ == "__main__":
    main()
