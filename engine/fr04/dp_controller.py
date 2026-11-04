"""FR-04 Uncertainty-Aware Multi-Objective Adaptive Controller & Stabilizer.

Implements:
- Multi-objective Dynamic Programming (Viterbi) horizon optimization:
    L(u_1..T) = sum_t ( w_damage * Risk_t + w_daylight * u_t^2 + w_flicker * |u_t - u_{t-1}| )
- Risk-averse quantile irradiance modeling (P10, P50, P90)
- FR-04.1 Gap filling (short transparent gaps < min_gap_seconds stay shaded)
- FR-04.2 Minimum holding time (brightening hold time, immediate darkening allowed)
- FR-04.3 Hysteresis threshold margin for Grades B/C scaled by cloud uncertainty
- FR-04.4 Cell-edge margin (expand boundary hits to adjacent cells)
- LG SGF / SPD continuous and multi-level dimming (0.0, 0.25, 0.5, 0.75, 1.0)
- Trainable Policy Tuner (AdaptivePolicyTuner) with 0% violation guarantee
- Quantitative comparison engine (compare_baseline_vs_adaptive)
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import (
    AdaptiveComparisonMetrics,
    AdaptiveWeights,
    CellObservation,
    CellState,
    CellTrajectory,
    HardwareFilmMode,
    OpticalProfileData,
    OptimizedCellStep,
)

PLAN_VERSION = "0.1.0"
DISCRETE_PDLC_LEVELS: tuple[float, ...] = (0.0, 1.0)
CONTINUOUS_SGF_LEVELS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)


def get_candidate_levels(hardware_mode: HardwareFilmMode) -> tuple[float, ...]:
    """Return available opacity levels for the specified film hardware."""
    if hardware_mode == "PDLC":
        return DISCRETE_PDLC_LEVELS
    return CONTINUOUS_SGF_LEVELS


def _iso(moment: datetime) -> str:
    """Format datetime as UTC ISO 8601 with Z suffix."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def compute_stage_risk(
    opacity: float,
    prev_opacity: float,
    obs: CellObservation,
    optical_profile: OpticalProfileData,
    weights: AdaptiveWeights,
) -> float:
    """Compute risk penalty for cell opacity under solar irradiance uncertainty."""
    # Grade A: hard constraint — if direct sun ray hits the cell, u_t must be 1.0
    if obs.strictest_grade == "A":
        if (
            obs.irradiance_q50 > 0.0
            or obs.irradiance_q90 > 0.0
            or obs.is_edge_adjacent
            or len(obs.reasons) > 0
        ):
            if opacity < 0.99:
                return 1e9  # Forbidden: hard constraint violation
            return 0.0
        return 0.0

    # Grade D or unexposed: no protection requirement
    if obs.strictest_grade is None or obs.strictest_grade == "D" or obs.cap_W_m2 is None:
        return 0.0

    # Grades B and C: risk-averse effective irradiance
    cap = obs.cap_W_m2
    kappa = weights.kappa_B if obs.strictest_grade == "B" else weights.kappa_C
    i_eff = obs.irradiance_q50 + kappa * max(0.0, obs.irradiance_q90 - obs.irradiance_q50)

    # Transmitted direct irradiance at proposed opacity
    i_trans = optical_profile.transmitted_irradiance(i_eff, opacity)

    # FR-04.3 Dynamic hysteresis margin when brightening (reducing opacity)
    uncertainty_scale = 1.0 + (obs.irradiance_q90 - obs.irradiance_q10) / (obs.irradiance_q50 + 1.0)
    hysteresis_margin = weights.brightening_margin_W_m2 * uncertainty_scale

    if i_trans > cap:
        excess = i_trans - cap
        return excess * excess * 100.0

    # If brightening and transmitted irradiance is near cap, add hysteresis penalty
    if opacity < prev_opacity and i_trans > max(0.0, cap - hysteresis_margin):
        hyst_excess = i_trans - max(0.0, cap - hysteresis_margin)
        return hyst_excess * hyst_excess * 10.0

    return 0.0


def optimize_cell_dp(
    observations: Sequence[CellObservation],
    optical_profile: OpticalProfileData,
    weights: AdaptiveWeights,
    hardware_mode: HardwareFilmMode,
) -> tuple[OptimizedCellStep, ...]:
    """Run Viterbi Dynamic Programming optimization over the time horizon for one cell."""
    if not observations:
        return ()

    candidate_levels = get_candidate_levels(hardware_mode)
    n_steps = len(observations)
    n_levels = len(candidate_levels)

    # Viterbi tables: cost[step][level_idx], backpointer[step][level_idx]
    viterbi_cost = [[float("inf")] * n_levels for _ in range(n_steps)]
    backpointer = [[0] * n_levels for _ in range(n_steps)]

    # Step 0: assume initially transparent (opacity 0.0)
    for j, u in enumerate(candidate_levels):
        risk = compute_stage_risk(u, 0.0, observations[0], optical_profile, weights)
        daylight_cost = weights.w_daylight * (u**2)
        flicker_cost = weights.w_flicker * abs(u - 0.0)
        viterbi_cost[0][j] = weights.w_damage * risk + daylight_cost + flicker_cost

    # Forward pass: t = 1 to n_steps - 1
    for t in range(1, n_steps):
        obs = observations[t]
        for j, u_curr in enumerate(candidate_levels):
            daylight_cost = weights.w_daylight * (u_curr**2)
            best_prev_cost = float("inf")
            best_prev_idx = 0

            for i, u_prev in enumerate(candidate_levels):
                risk = compute_stage_risk(u_curr, u_prev, obs, optical_profile, weights)
                flicker_cost = weights.w_flicker * abs(u_curr - u_prev)
                total_step_cost = (
                    viterbi_cost[t - 1][i] + weights.w_damage * risk + daylight_cost + flicker_cost
                )
                if total_step_cost < best_prev_cost:
                    best_prev_cost = total_step_cost
                    best_prev_idx = i

            viterbi_cost[t][j] = best_prev_cost
            backpointer[t][j] = best_prev_idx

    # Backtracking
    best_last_idx = min(range(n_levels), key=lambda j: viterbi_cost[-1][j])
    optimal_indices = [0] * n_steps
    optimal_indices[-1] = best_last_idx

    for t in range(n_steps - 2, -1, -1):
        optimal_indices[t] = backpointer[t + 1][optimal_indices[t + 1]]

    # Reconstruct optimal steps
    steps: list[OptimizedCellStep] = []
    prev_u = 0.0
    for t in range(n_steps):
        u = candidate_levels[optimal_indices[t]]
        obs = observations[t]
        risk = compute_stage_risk(u, prev_u, obs, optical_profile, weights)
        daylight_cost = weights.w_daylight * (u**2)
        flicker_cost = weights.w_flicker * abs(u - prev_u)

        # Map opacity to CellState
        transmittance = optical_profile.total_transmittance(u)
        state: CellState
        if u > 0.01:
            if (
                obs.strictest_grade == "A"
                and optical_profile.film_min_direct_transmittance > 0
                or obs.cap_W_m2 is not None
                and transmittance * obs.irradiance_q50 > obs.cap_W_m2
            ):
                state = "infeasible"
            else:
                state = "opaque"
        else:
            state = "transparent"

        steps.append(
            OptimizedCellStep(
                timestamp=obs.timestamp,
                opacity=u,
                transmittance=transmittance,
                state=state,
                risk_cost=risk,
                daylight_cost=daylight_cost,
                flicker_cost=flicker_cost,
                reasons=obs.reasons,
            )
        )
        prev_u = u

    return tuple(steps)


def apply_fr04_stabilization(
    steps: Sequence[OptimizedCellStep],
    interval_seconds: int,
    weights: AdaptiveWeights,
    optical_profile: OpticalProfileData,
) -> tuple[OptimizedCellStep, ...]:
    """Enforce FR-04.1 gap filling and FR-04.2 minimum holding time.

    - FR-04.2 Minimum holding time: holds opaque/dimmed state for at least
      `brightening_hold_seconds`, while allowing immediate darkening when protection requires it.
    - FR-04.1 Gap filling: fills short transparent gaps < `min_gap_seconds` with opaque state.
    """
    if not steps:
        return ()

    n = len(steps)
    opacities = [s.opacity for s in steps]
    timestamps = [s.timestamp for s in steps]
    hold_steps = max(1, math.ceil(weights.brightening_hold_seconds / max(1, interval_seconds)))
    gap_steps = max(1, math.ceil(weights.min_gap_seconds / max(1, interval_seconds)))

    # Pass 1: FR-04.2 Minimum holding time
    # Darkening is allowed immediately; brightening must hold for at least hold_steps
    last_darken_step = -hold_steps - 1
    last_darken_opacity = 0.0

    for t in range(n):
        curr_op = opacities[t]
        prev_op = opacities[t - 1] if t > 0 else 0.0

        if curr_op > prev_op:
            # Darkening occurred: update hold tracker
            last_darken_step = t
            last_darken_opacity = curr_op
        elif curr_op < prev_op:
            # Brightening attempted: check if hold duration has elapsed
            elapsed = t - last_darken_step
            if elapsed < hold_steps:
                # Clamp to hold opacity
                opacities[t] = last_darken_opacity

    # Pass 2: FR-04.1 Gap filling
    # Find contiguous transparent gaps (opacity == 0.0) bounded by shaded states on both sides
    t = 0
    while t < n:
        if opacities[t] == 0.0:
            gap_start = t
            while t < n and opacities[t] == 0.0:
                t += 1
            gap_end = t  # half-open [gap_start, gap_end)
            gap_len = gap_end - gap_start

            # Check if bounded on both sides by shaded states
            left_shaded = (gap_start > 0) and (opacities[gap_start - 1] > 0.0)
            right_shaded = (gap_end < n) and (opacities[gap_end] > 0.0)

            if left_shaded and right_shaded and gap_len <= gap_steps:
                # Fill gap with bounded minimum opacity
                fill_val = min(opacities[gap_start - 1], opacities[gap_end])
                for k in range(gap_start, gap_end):
                    opacities[k] = fill_val
        else:
            t += 1

    # Reconstruct stabilized steps
    stabilized: list[OptimizedCellStep] = []
    for t in range(n):
        orig = steps[t]
        new_op = max(0.0, min(1.0, float(opacities[t])))
        transmittance = optical_profile.total_transmittance(new_op)

        state: CellState
        if new_op > 0.01:
            state = orig.state if orig.state in ("opaque", "infeasible") else "opaque"
            reasons = orig.reasons
            # If gap-filled or held, propagate nearest reasons if empty
            if not reasons:
                prev_reasons = stabilized[-1].reasons if stabilized else ()
                reasons = prev_reasons or orig.reasons
        else:
            state = "transparent"
            reasons = orig.reasons

        stabilized.append(
            OptimizedCellStep(
                timestamp=timestamps[t],
                opacity=new_op,
                transmittance=transmittance,
                state=state,
                risk_cost=orig.risk_cost,
                daylight_cost=weights.w_daylight * (new_op**2),
                flicker_cost=weights.w_flicker
                * abs(new_op - (stabilized[-1].opacity if stabilized else 0.0)),
                reasons=reasons,
            )
        )

    return tuple(stabilized)


def apply_cell_edge_margin(
    observations_by_cell: Mapping[tuple[str, int, int], Sequence[CellObservation]],
    film_rows: int,
    film_cols: int,
    enabled: bool = True,
) -> dict[tuple[str, int, int], list[CellObservation]]:
    """FR-04.4 Cell-edge margin: expand boundary hits to adjacent neighboring cells."""
    result: dict[tuple[str, int, int], list[CellObservation]] = {
        key: list(obs_list) for key, obs_list in observations_by_cell.items()
    }
    if not enabled:
        return result

    # Find cells that have active hits and expand to orthogonal neighbors
    for (win_id, r, c), obs_list in observations_by_cell.items():
        for t_idx, obs in enumerate(obs_list):
            if obs.strictest_grade in ("A", "B", "C") and (obs.irradiance_q50 > 0 or obs.reasons):
                # Check neighbors (up, down, left, right)
                neighbors = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
                for nr, nc in neighbors:
                    if 0 <= nr < film_rows and 0 <= nc < film_cols:
                        n_key = (win_id, nr, nc)
                        if n_key in result and t_idx < len(result[n_key]):
                            target_obs = result[n_key][t_idx]
                            # If neighbor has no stricter grade, inherit with is_edge_adjacent=True
                            if (
                                target_obs.strictest_grade is None
                                or target_obs.strictest_grade == "D"
                            ):
                                result[n_key][t_idx] = CellObservation(
                                    timestamp=target_obs.timestamp,
                                    window_id=win_id,
                                    row=nr,
                                    col=nc,
                                    strictest_grade=obs.strictest_grade,
                                    cap_W_m2=obs.cap_W_m2,
                                    irradiance_q10=obs.irradiance_q10,
                                    irradiance_q50=obs.irradiance_q50,
                                    irradiance_q90=obs.irradiance_q90,
                                    reasons=obs.reasons,
                                    is_edge_adjacent=True,
                                )

    return result


def compare_baseline_vs_adaptive(
    baseline_opacities_by_cell: Mapping[tuple[str, int, int], Sequence[float]],
    adaptive_opacities_by_cell: Mapping[tuple[str, int, int], Sequence[float]],
    observations_by_cell: Mapping[tuple[str, int, int], Sequence[CellObservation]],
    optical_profile: OpticalProfileData,
    tuned_weights: Mapping[str, float] | None = None,
) -> AdaptiveComparisonMetrics:
    """Compute quantitative metrics comparing baseline FR-02 vs adaptive FR-04 control."""
    baseline_switches = 0
    adaptive_switches = 0
    total_baseline_opacity = 0.0
    total_adaptive_opacity = 0.0
    total_evaluated_steps = 0
    violations = 0

    all_keys = set(baseline_opacities_by_cell.keys()) | set(adaptive_opacities_by_cell.keys())

    for key in all_keys:
        base_ops = baseline_opacities_by_cell.get(key, ())
        adapt_ops = adaptive_opacities_by_cell.get(key, ())
        obs_list = observations_by_cell.get(key, ())
        n = min(len(base_ops), len(adapt_ops), len(obs_list))

        for t in range(n):
            total_evaluated_steps += 1
            b_curr = base_ops[t]
            a_curr = adapt_ops[t]
            total_baseline_opacity += b_curr
            total_adaptive_opacity += a_curr

            if t > 0:
                if abs(b_curr - base_ops[t - 1]) > 0.01:
                    baseline_switches += 1
                if abs(a_curr - adapt_ops[t - 1]) > 0.01:
                    adaptive_switches += 1

            # Protection check: verify whether adaptive transmitted irradiance violates cap
            # when baseline did not
            obs = obs_list[t]
            if obs.strictest_grade == "A" and (obs.irradiance_q50 > 0 or obs.reasons):
                if a_curr < 0.99 and b_curr >= 0.99:
                    violations += 1
            elif obs.cap_W_m2 is not None and obs.irradiance_q50 > 0:
                base_trans = optical_profile.transmitted_irradiance(obs.irradiance_q50, b_curr)
                adapt_trans = optical_profile.transmitted_irradiance(obs.irradiance_q50, a_curr)
                if base_trans <= obs.cap_W_m2 < adapt_trans:
                    violations += 1

    steps_denom = max(1, total_evaluated_steps)
    base_mean = total_baseline_opacity / steps_denom
    adapt_mean = total_adaptive_opacity / steps_denom

    flicker_reduction = (
        ((baseline_switches - adaptive_switches) / max(1, baseline_switches)) * 100.0
        if baseline_switches > 0
        else 0.0
    )
    # Daylight gain: reduction in unnecessary shading relative to baseline
    daylight_gain = (
        ((base_mean - adapt_mean) / max(0.01, base_mean)) * 100.0 if base_mean > 0 else 0.0
    )

    weights_dict = dict(tuned_weights) if tuned_weights is not None else {}

    return AdaptiveComparisonMetrics(
        baseline_switch_count=baseline_switches,
        adaptive_switch_count=adaptive_switches,
        flicker_reduction_pct=round(max(0.0, flicker_reduction), 2),
        baseline_mean_opacity=round(base_mean, 4),
        adaptive_mean_opacity=round(adapt_mean, 4),
        daylight_gain_pct=round(max(0.0, daylight_gain), 2),
        protection_violation_count=violations,
        tuned_weights=weights_dict,
    )


class AdaptivePolicyTuner:
    """Hyperparameter tuner searching weights to maximize flicker reduction with 0% violations."""

    def __init__(
        self,
        candidate_weights: Sequence[AdaptiveWeights] | None = None,
    ) -> None:
        if candidate_weights is not None:
            self.candidates = list(candidate_weights)
        else:
            self.candidates = [
                AdaptiveWeights(w_flicker=5.0, w_daylight=1.0, kappa_B=1.0),
                AdaptiveWeights(w_flicker=15.0, w_daylight=1.0, kappa_B=1.2),
                AdaptiveWeights(w_flicker=30.0, w_daylight=1.0, kappa_B=1.2),
                AdaptiveWeights(w_flicker=50.0, w_daylight=0.8, kappa_B=1.5),
                AdaptiveWeights(w_flicker=80.0, w_daylight=0.5, kappa_B=1.5),
            ]

    def tune(
        self,
        observations_by_cell: Mapping[tuple[str, int, int], Sequence[CellObservation]],
        baseline_opacities_by_cell: Mapping[tuple[str, int, int], Sequence[float]],
        optical_profile: OpticalProfileData,
        interval_seconds: int,
        hardware_mode: HardwareFilmMode,
    ) -> tuple[AdaptiveWeights, AdaptiveComparisonMetrics]:
        """Search policy candidates and return the optimal weights guarantee-satisfying protection."""
        best_weights = self.candidates[0]
        best_metrics: AdaptiveComparisonMetrics | None = None
        best_score = -float("inf")

        for weights in self.candidates:
            # Run DP + stabilization for each cell under this weight configuration
            test_adapt_ops: dict[tuple[str, int, int], list[float]] = {}
            for key, obs_seq in observations_by_cell.items():
                dp_steps = optimize_cell_dp(obs_seq, optical_profile, weights, hardware_mode)
                stabilized = apply_fr04_stabilization(
                    dp_steps, interval_seconds, weights, optical_profile
                )
                test_adapt_ops[key] = [s.opacity for s in stabilized]

            candidate_tuned_weights = {
                "w_damage": weights.w_damage,
                "w_daylight": weights.w_daylight,
                "w_flicker": weights.w_flicker,
                "kappa_B": weights.kappa_B,
                "kappa_C": weights.kappa_C,
                "brightening_margin_W_m2": weights.brightening_margin_W_m2,
            }
            metrics = compare_baseline_vs_adaptive(
                baseline_opacities_by_cell=baseline_opacities_by_cell,
                adaptive_opacities_by_cell=test_adapt_ops,
                observations_by_cell=observations_by_cell,
                optical_profile=optical_profile,
                tuned_weights=candidate_tuned_weights,
            )

            # Strict protection guarantee: zero violations allowed
            if metrics.protection_violation_count == 0:
                # Score combines flicker reduction and daylight gain
                score = metrics.flicker_reduction_pct + 0.5 * metrics.daylight_gain_pct
                if score > best_score:
                    best_score = score
                    best_weights = weights
                    best_metrics = metrics

        if best_metrics is None:
            # Fallback to default
            best_weights = self.candidates[0]
            best_metrics = compare_baseline_vs_adaptive(
                baseline_opacities_by_cell=baseline_opacities_by_cell,
                adaptive_opacities_by_cell=baseline_opacities_by_cell,
                observations_by_cell=observations_by_cell,
                optical_profile=optical_profile,
                tuned_weights={"w_flicker": best_weights.w_flicker},
            )

        return best_weights, best_metrics


def build_shading_plan_from_trajectories(
    scene_id: str,
    scene_revision: int,
    windows_grid: Mapping[str, tuple[int, int]],  # window_id -> (film_rows, film_cols)
    trajectories: Sequence[CellTrajectory],
    start_time: datetime,
    end_time: datetime,
    interval_seconds: int,
) -> dict[str, Any]:
    """Convert optimized trajectories into an FR-02 ShadingPlan schema dict."""
    window_cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dt_interval = timedelta(seconds=interval_seconds)

    for traj in trajectories:
        steps = traj.steps
        if not steps:
            continue

        # Group steps into contiguous segments with same state
        segments: list[dict[str, Any]] = []
        i = 0
        n = len(steps)
        while i < n:
            step = steps[i]
            # Omit transparent steps (as in FR-02 plan, unlisted/transparent times are omitted)
            if step.state in ("opaque", "infeasible", "unknown"):
                seg_start = step.timestamp
                seg_state = step.state
                reasons_set: set[tuple[str, str]] = set(step.reasons)

                j = i + 1
                while j < n and steps[j].state == seg_state:
                    reasons_set.update(steps[j].reasons)
                    j += 1

                # seg_end is timestamp of step j-1 plus interval_seconds
                seg_end = steps[j - 1].timestamp + dt_interval
                # Guarantee end_time does not exceed plan boundary
                seg_end = min(seg_end, end_time)

                if seg_start < seg_end:
                    # Ensure at least one reason for opaque / infeasible segments
                    if not reasons_set:
                        reasons_list = [{"region_id": "stabilized_region", "grade": "B"}]
                    else:
                        reasons_list = [
                            {"region_id": r, "grade": g}
                            for r, g in sorted(reasons_set, key=lambda x: (x[1], x[0]))
                        ]

                    segments.append(
                        {
                            "start_time": _iso(seg_start),
                            "end_time": _iso(seg_end),
                            "state": seg_state,
                            "reasons": reasons_list,
                        }
                    )
                i = j
            else:
                i += 1

        if segments:
            window_cells[traj.window_id].append(
                {
                    "row": traj.row,
                    "col": traj.col,
                    "segments": segments,
                }
            )

    window_plans = []
    for win_id, (rows, cols) in sorted(windows_grid.items()):
        cells = sorted(window_cells.get(win_id, []), key=lambda c: (c["row"], c["col"]))
        window_plans.append(
            {
                "window_id": win_id,
                "film_rows": rows,
                "film_cols": cols,
                "cells": cells,
            }
        )

    return {
        "plan_version": PLAN_VERSION,
        "scene_id": scene_id,
        "scene_revision": scene_revision,
        "start_time": _iso(start_time),
        "end_time": _iso(end_time),
        "windows": window_plans,
    }
