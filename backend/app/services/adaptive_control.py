"""Backend service coordinating FR-04 uncertainty-aware adaptive control and ThinQ ON hub sync."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlmodel import Session

from app.models.film import FilmApplicationLogRecord, FilmStateRecord
from app.models.scene import SceneRecord
from app.nowcast.repository import utc_z
from app.schemas.adaptive_control import (
    AdaptiveComparisonMetrics as SchemaAdaptiveMetrics,
)
from app.schemas.adaptive_control import (
    AdaptiveShadingPlanRequest,
    AdaptiveShadingPlanResponse,
    CellDimmingTrajectory,
    ThinQHubDevicePayload,
    ThinQHubSyncRequest,
    ThinQHubSyncResponse,
    UncertaintyTimelinePoint,
)
from app.schemas.scene import Scene
from app.schemas.shading_plan import ShadingPlan, ShadingPlanRequest
from app.schemas.sunpath import SunpathPredictionRequest, SunpathPredictionResponse
from app.services.film import (
    apply_schedule_to_scene,
    normalize_iso_timestamp,
    opacity_to_applied_state,
)
from app.services.shading import _caps, _exposed_tiles, _film_windows, shading_plan_for_scene
from app.services.sunpath import SunpathValidationError, predict_scene
from engine.fr02 import map_tiles_to_cells
from engine.fr04 import (
    AdaptiveComparisonMetrics,
    AdaptivePolicyTuner,
    AdaptiveWeights,
    CellObservation,
    CellState,
    CellTrajectory,
    OpticalProfileData,
    ProtectionGrade,
    apply_cell_edge_margin,
    apply_fr04_stabilization,
    build_shading_plan_from_trajectories,
    compare_baseline_vs_adaptive,
    optimize_cell_dp,
    smooth_fr02_plan,
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _grade_rank(g: str | None) -> int:
    ranks = {"A": 4, "B": 3, "C": 2, "D": 1}
    return ranks.get(g, 0) if g else 0


def _build_optical_profiles(scene: Scene) -> dict[str, OpticalProfileData]:
    result: dict[str, OpticalProfileData] = {}
    for p in scene.optical_profiles:
        result[p.optical_profile_id] = OpticalProfileData(
            glazing_direct_transmittance=float(p.glazing_direct_transmittance),
            film_min_direct_transmittance=float(p.film_min_direct_transmittance),
            film_max_direct_transmittance=float(p.film_max_direct_transmittance),
        )
    return result


def _match_uncertainty(
    target_dt: datetime,
    timeline: Sequence[Any],
) -> tuple[float, float, float] | None:
    """Find closest uncertainty quantiles within a 1-hour window."""
    if not timeline:
        return None
    best_diff = timedelta(hours=1)
    best_val: tuple[float, float, float] | None = None
    for pt in timeline:
        pt_dt = datetime.fromisoformat(pt.timestamp).astimezone(UTC)
        diff = abs(target_dt - pt_dt)
        if diff < best_diff:
            best_diff = diff
            best_val = (pt.dni_p10_W_m2, pt.dni_p50_W_m2, pt.dni_p90_W_m2)
    return best_val


def _load_nowcast_uncertainty_timeline(
    session: Session,
    scene: Scene,
    nowcast_engine: Engine | None = None,
) -> list[UncertaintyTimelinePoint]:
    """Read active, site-scoped operational quantiles when available."""
    target_engine = nowcast_engine if nowcast_engine is not None else session.get_bind()
    site_key = f"{scene.site.latitude_deg:.6f},{scene.site.longitude_deg:.6f}"

    try:
        with Session(target_engine) as query_session:
            row = query_session.execute(
                text("""
                SELECT nr.run_id
                FROM ai_nowcast_run nr
                JOIN ai_nowcast_point np ON nr.run_id = np.run_id
                WHERE nr.site_key = :site_key
                  AND nr.rollout_mode = 'active'
                  AND nr.expires_at >= :now
                  AND np.prediction_source = 'ai_model'
                  AND np.dni_p10_W_m2 IS NOT NULL
                  AND np.dni_p50_W_m2 IS NOT NULL
                  AND np.dni_p90_W_m2 IS NOT NULL
                ORDER BY nr.created_at DESC
                LIMIT 1
                """),
                {"site_key": site_key, "now": utc_z(datetime.now(UTC))},
            ).first()

            if row is None:
                return []

            run_id = row[0]
            points_rows = query_session.execute(
                text("""
                SELECT valid_time, dni_p10_W_m2, dni_p50_W_m2, dni_p90_W_m2
                FROM ai_nowcast_point
                WHERE run_id = :run_id
                  AND dni_p10_W_m2 IS NOT NULL
                  AND dni_p50_W_m2 IS NOT NULL
                  AND dni_p90_W_m2 IS NOT NULL
                ORDER BY valid_time ASC
                """),
                {"run_id": run_id},
            ).fetchall()

            results: list[UncertaintyTimelinePoint] = []
            for vtime, p10, p50, p90 in points_rows:
                v10 = max(0.0, float(p10))
                v50 = max(v10, float(p50))
                v90 = max(v50, float(p90))
                results.append(
                    UncertaintyTimelinePoint(
                        timestamp=vtime,
                        dni_p10_W_m2=v10,
                        dni_p50_W_m2=v50,
                        dni_p90_W_m2=v90,
                    )
                )
            return results
    except Exception:  # noqa: BLE001
        return []


def _change_phrase(label: str, gain_pct: float) -> str:
    """Wording for a signed change against the baseline (positive is better)."""
    if abs(gain_pct) < 0.05:
        return f"{label}은 기준과 같습니다"
    if gain_pct > 0:
        return f"{label}은 기준보다 {gain_pct:.1f}% 개선됐습니다"
    return f"{label}은 기준보다 {-gain_pct:.1f}% 나빠졌습니다"


def compute_adaptive_shading_plan(
    session: Session,
    scene_id: str,
    request: AdaptiveShadingPlanRequest,
    nowcast_engine: Engine | None = None,
) -> AdaptiveShadingPlanResponse:
    """Compute uncertainty-aware adaptive shading plan and comparison metrics for a scene."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    scene = Scene.model_validate_json(scene_record.scene_json)
    if not scene.windows:
        raise HTTPException(status_code=422, detail="Scene has no windows")

    try:
        caps = _caps(scene)
    except SunpathValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err

    # 1. Run FR-01 sunpath prediction and FR-02 baseline plan
    shading_req = ShadingPlanRequest(
        start_time=request.start_time,
        duration_minutes=request.duration_minutes,
        interval_seconds=request.interval_seconds,
    )
    prediction: SunpathPredictionResponse = predict_scene(
        scene,
        SunpathPredictionRequest(
            start_time=request.start_time,
            duration_minutes=request.duration_minutes,
            interval_seconds=request.interval_seconds,
            # The exposed samples are only returned in diagnostic detail. Without them no cell has
            # an observation and FR-04 sees no sun at all.
            detail_level="diagnostic",
        ),
        nowcast_engine=nowcast_engine,
    )
    baseline_plan: ShadingPlan = shading_plan_for_scene(
        scene, shading_req, nowcast_engine=nowcast_engine
    )

    optical_profiles = _build_optical_profiles(scene)
    windows_map = {w.window_id: w for w in scene.windows}
    windows_grid = {w.window_id: (w.film_rows, w.film_cols) for w in scene.windows}
    grades_map = {r.region_id: r.grade for r in scene.protected_regions}

    # 2.5 Determine effective uncertainty timeline (passed or auto-fetched from database)
    effective_timeline: Sequence[Any] = request.uncertainty_timeline
    if not effective_timeline:
        effective_timeline = _load_nowcast_uncertainty_timeline(
            session=session, scene=scene, nowcast_engine=nowcast_engine
        )

    # 2. Map tiles to window cells across timeline
    film_win_objs = _film_windows(scene)
    tiles = _exposed_tiles(prediction)
    candidates = map_tiles_to_cells(tiles, film_win_objs)

    candidate_lookup: dict[tuple[datetime, str, int, int], Any] = {
        (cand.timestamp, cand.window_id, cand.row, cand.col): cand for cand in candidates
    }

    # Generate sequence of timestamps
    dt_start = datetime.fromisoformat(normalize_iso_timestamp(request.start_time)).astimezone(UTC)
    dt_end = dt_start + timedelta(minutes=request.duration_minutes)
    interval = timedelta(seconds=request.interval_seconds)

    time_steps: list[datetime] = []
    curr = dt_start
    while curr < dt_end:
        time_steps.append(curr)
        curr += interval

    # 3. Construct CellObservation matrix for each window cell across all time steps
    raw_obs_by_cell: dict[tuple[str, int, int], list[CellObservation]] = defaultdict(list)
    for win_id, (rows, cols) in windows_grid.items():
        for r in range(rows):
            for c in range(cols):
                cell_key = (win_id, r, c)
                for t in time_steps:
                    cand = candidate_lookup.get((t, win_id, r, c))
                    if cand is not None:
                        # Determine strictest grade
                        causes = cand.causes
                        strictest_g: ProtectionGrade | None = None
                        best_rank = 0
                        for cause in causes:
                            cg = grades_map.get(cause.region_id)
                            rank = _grade_rank(cg)
                            if rank > best_rank and cg in ("A", "B", "C", "D"):
                                best_rank = rank
                                strictest_g = cg  # type: ignore[assignment]

                        # Max direct irradiance among causes
                        irradiances = [
                            cause.direct_irradiance_W_m2
                            for cause in causes
                            if cause.direct_irradiance_W_m2 is not None
                        ]
                        i_val = max(irradiances) if irradiances else 0.0

                        # Cap
                        cap_val: float | None = None
                        if strictest_g == "A":
                            cap_val = 0.0
                        elif strictest_g in ("B", "C") and strictest_g is not None:
                            cap_val = caps.get(strictest_g)

                        # Quantiles
                        matched_q = _match_uncertainty(t, effective_timeline)
                        if matched_q is not None:
                            p10, p50, p90 = matched_q
                            scale = i_val / max(1.0, p50) if p50 > 0 else 1.0
                            q10, q50, q90 = p10 * scale, i_val, p90 * scale
                        else:
                            q10, q50, q90 = 0.80 * i_val, i_val, 1.20 * i_val

                        reasons = tuple(
                            (cause.region_id, grades_map[cause.region_id])
                            for cause in causes
                            if cause.region_id in grades_map
                        )

                        raw_obs_by_cell[cell_key].append(
                            CellObservation(
                                timestamp=t,
                                window_id=win_id,
                                row=r,
                                col=c,
                                strictest_grade=strictest_g,
                                cap_W_m2=cap_val,
                                irradiance_q10=q10,
                                irradiance_q50=q50,
                                irradiance_q90=q90,
                                reasons=reasons,
                            )
                        )
                    else:
                        raw_obs_by_cell[cell_key].append(
                            CellObservation(
                                timestamp=t,
                                window_id=win_id,
                                row=r,
                                col=c,
                                strictest_grade=None,
                                cap_W_m2=None,
                                irradiance_q10=0.0,
                                irradiance_q50=0.0,
                                irradiance_q90=0.0,
                                reasons=(),
                            )
                        )

    # 4. Optional FR-04.4 Cell-edge margin expansion
    obs_by_cell = raw_obs_by_cell
    if request.enable_edge_margin and request.mode == "replace":
        for win_id, (rows, cols) in windows_grid.items():
            win_obs = {k: v for k, v in obs_by_cell.items() if k[0] == win_id}
            expanded = apply_cell_edge_margin(win_obs, film_rows=rows, film_cols=cols, enabled=True)
            obs_by_cell.update(expanded)

    # 5. Extract baseline opacities
    baseline_opacities: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    baseline_states: dict[tuple[str, int, int], list[CellState]] = defaultdict(list)
    # Build baseline segment lookup
    baseline_segments: dict[tuple[str, int, int], list[Any]] = defaultdict(list)
    for win_plan in baseline_plan.windows:
        for cell_plan in win_plan.cells:
            key = (win_plan.window_id, cell_plan.row, cell_plan.col)
            baseline_segments[key] = cell_plan.segments

    for cell_key, obs_seq in obs_by_cell.items():
        segments = baseline_segments.get(cell_key, [])
        for obs in obs_seq:
            op = 0.0
            state: CellState = "transparent"
            for seg in segments:
                s_start = datetime.fromisoformat(seg.start_time).astimezone(UTC)
                s_end = datetime.fromisoformat(seg.end_time).astimezone(UTC)
                if s_start <= obs.timestamp < s_end:
                    if seg.state in ("opaque", "infeasible", "unknown"):
                        op, state = 1.0, seg.state
                    break
            baseline_opacities[cell_key].append(op)
            baseline_states[cell_key].append(state)

    # Default optical profile for scene
    default_profile = next(iter(optical_profiles.values()))

    # 6. Weights: FR-04 smoothing uses the defaults; the experimental replace mode may tune them
    tuned_weights_dict: dict[str, float] = {}
    if request.mode == "smooth_fr02":
        best_weights = AdaptiveWeights()
    elif request.auto_tune_weights:
        tuner = AdaptivePolicyTuner()
        best_weights, _ = tuner.tune(
            observations_by_cell=obs_by_cell,
            baseline_opacities_by_cell=baseline_opacities,
            optical_profile=default_profile,
            interval_seconds=request.interval_seconds,
            hardware_mode=request.hardware_mode,
        )
    else:
        best_weights = AdaptiveWeights(enable_edge_margin=request.enable_edge_margin)
    tuned_weights_dict = {
        "w_damage": best_weights.w_damage,
        "w_daylight": best_weights.w_daylight,
        "w_flicker": best_weights.w_flicker,
        "kappa_B": best_weights.kappa_B,
        "kappa_C": best_weights.kappa_C,
        "brightening_margin_W_m2": best_weights.brightening_margin_W_m2,
    }

    # 7. Smooth the FR-02 plan (default) or run the DP optimization, across all cells
    cell_trajectories: list[CellTrajectory] = []
    adaptive_opacities: dict[tuple[str, int, int], list[float]] = {}

    for cell_key, obs_seq in sorted(obs_by_cell.items()):
        win_id, row, col = cell_key
        win_obj = windows_map[win_id]
        profile = optical_profiles.get(win_obj.optical_profile_id, default_profile)

        if request.mode == "smooth_fr02":
            stabilized_steps = smooth_fr02_plan(
                baseline_opacities[cell_key],
                baseline_states[cell_key],
                obs_seq,
                profile,
                best_weights,
                request.interval_seconds,
            )
        else:
            dp_steps = optimize_cell_dp(obs_seq, profile, best_weights, request.hardware_mode)
            stabilized_steps = apply_fr04_stabilization(
                dp_steps, request.interval_seconds, best_weights, profile
            )

        traj = CellTrajectory(window_id=win_id, row=row, col=col, steps=stabilized_steps)
        cell_trajectories.append(traj)
        adaptive_opacities[cell_key] = [s.opacity for s in stabilized_steps]

    # 8. Compute comparison metrics
    raw_metrics: AdaptiveComparisonMetrics = compare_baseline_vs_adaptive(
        baseline_opacities_by_cell=baseline_opacities,
        adaptive_opacities_by_cell=adaptive_opacities,
        observations_by_cell=obs_by_cell,
        optical_profile=default_profile,
        tuned_weights=tuned_weights_dict,
    )

    metrics_model = SchemaAdaptiveMetrics(
        baseline_switch_count=raw_metrics.baseline_switch_count,
        adaptive_switch_count=raw_metrics.adaptive_switch_count,
        flicker_reduction_pct=raw_metrics.flicker_reduction_pct,
        baseline_mean_opacity=raw_metrics.baseline_mean_opacity,
        adaptive_mean_opacity=raw_metrics.adaptive_mean_opacity,
        daylight_gain_pct=raw_metrics.daylight_gain_pct,
        protection_violation_count=raw_metrics.protection_violation_count,
        baseline_violation_count=raw_metrics.baseline_violation_count,
        protection_violation_count_q90=raw_metrics.protection_violation_count_q90,
        baseline_violation_count_q90=raw_metrics.baseline_violation_count_q90,
        baseline_total_change=raw_metrics.baseline_total_change,
        adaptive_total_change=raw_metrics.adaptive_total_change,
        total_change_reduction_pct=raw_metrics.total_change_reduction_pct,
        tuned_weights=raw_metrics.tuned_weights,
    )

    # 9. Build ShadingPlan and dimming trajectories
    plan_dict = build_shading_plan_from_trajectories(
        scene_id=scene.scene_id,
        scene_revision=prediction.scene_revision,
        windows_grid=windows_grid,
        trajectories=cell_trajectories,
        start_time=dt_start,
        end_time=dt_end,
        interval_seconds=request.interval_seconds,
    )
    stabilized_shading_plan = ShadingPlan.model_validate(plan_dict)

    # Only include active dimming trajectories to keep payload compact
    dimming_trajectories: list[CellDimmingTrajectory] = []
    for traj in cell_trajectories:
        if any(s.opacity > 0.01 for s in traj.steps):
            dimming_trajectories.append(
                CellDimmingTrajectory(
                    window_id=traj.window_id,
                    row=traj.row,
                    col=traj.col,
                    timestamps=[_iso(s.timestamp) for s in traj.steps],
                    opacities=[round(s.opacity, 4) for s in traj.steps],
                    transmittances=[round(s.transmittance, 4) for s in traj.steps],
                )
            )

    # 10. Optional apply to virtual film
    if request.apply_to_virtual_film:
        apply_schedule_to_scene(
            session=session,
            scene_id=scene_id,
            plan=stabilized_shading_plan,
            at=request.start_time,
        )
        # Update FilmStateRecord if continuous
        if request.mode == "replace" and request.hardware_mode in ("LG_SGF", "SPD"):
            for win_id, (rows, cols) in windows_grid.items():
                state_key = f"{scene_id}:{win_id}"
                rec = session.get(FilmStateRecord, state_key)
                if rec:
                    grid = json.loads(rec.cells_json)
                    for traj in cell_trajectories:
                        if traj.window_id == win_id and traj.steps:
                            grid[traj.row][traj.col] = traj.steps[0].opacity
                    rec.cells_json = json.dumps(grid)
                    rec.film_type = "SPD"
                    session.add(rec)
            session.commit()

    return AdaptiveShadingPlanResponse(
        scene_id=scene.scene_id,
        scene_revision=prediction.scene_revision,
        mode=request.mode,
        hardware_mode=request.hardware_mode,
        baseline_plan=baseline_plan,
        stabilized_plan=stabilized_shading_plan,
        dimming_trajectories=dimming_trajectories,
        metrics=metrics_model,
    )


def sync_to_thinq_hub(
    session: Session,
    scene_id: str,
    request: ThinQHubSyncRequest,
    nowcast_engine: Engine | None = None,
) -> ThinQHubSyncResponse:
    """Compute adaptive plan and synchronize continuous dimming to LG ThinQ ON hub."""
    plan_resp = compute_adaptive_shading_plan(
        session=session,
        scene_id=scene_id,
        request=request.plan_request,
        nowcast_engine=nowcast_engine,
    )

    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")
    scene = Scene.model_validate_json(scene_record.scene_json)
    optical_profiles = _build_optical_profiles(scene)
    default_profile = next(iter(optical_profiles.values()))

    target_dt = datetime.fromisoformat(
        normalize_iso_timestamp(request.target_timestamp)
    ).astimezone(UTC)
    synced_at = _iso(datetime.now(UTC))

    devices: list[ThinQHubDevicePayload] = []

    for win in scene.windows:
        rows, cols = win.film_rows, win.film_cols
        profile = optical_profiles.get(win.optical_profile_id, default_profile)

        # Build grid matrices
        opacity_matrix = [[0.0 for _ in range(cols)] for _ in range(rows)]
        trans_matrix = [
            [round(profile.total_transmittance(0.0), 4) for _ in range(cols)] for _ in range(rows)
        ]

        # Populate from dimming trajectories
        for traj in plan_resp.dimming_trajectories:
            if traj.window_id == win.window_id:
                # Find closest timestamp
                best_idx = 0
                min_diff = timedelta(days=999)
                for idx, t_str in enumerate(traj.timestamps):
                    t_dt = datetime.fromisoformat(t_str).astimezone(UTC)
                    diff = abs(target_dt - t_dt)
                    if diff < min_diff:
                        min_diff = diff
                        best_idx = idx

                op = traj.opacities[best_idx]
                opacity_matrix[traj.row][traj.col] = op
                trans_matrix[traj.row][traj.col] = traj.transmittances[best_idx]

        active_count = sum(
            1 for r in range(rows) for c in range(cols) if opacity_matrix[r][c] > 0.01
        )
        dimmed_count = sum(
            1 for r in range(rows) for c in range(cols) if 0.01 < opacity_matrix[r][c] < 0.99
        )

        total_trans = sum(trans_matrix[r][c] for r in range(rows) for c in range(cols))
        max_trans = rows * cols * max(0.01, profile.glazing_direct_transmittance)
        daylight_score = round(total_trans / max_trans, 4)

        decision_summary = (
            f"LG ThinQ ON SGF 제어: 조도 불확실성(P90)을 반영하여 {active_count}개 셀 차광"
            f"(디밍 {dimmed_count}개)을 최적화했습니다. "
            f"{_change_phrase('플리커(불투명도 변화량)', plan_resp.metrics.total_change_reduction_pct)}, "
            f"{_change_phrase('자연채광', plan_resp.metrics.daylight_gain_pct)}."
        )

        # Update SQLite FilmStateRecord
        state_key = f"{scene_id}:{win.window_id}"
        rec = session.get(FilmStateRecord, state_key)
        film_type_str = "SPD" if request.hardware_mode in ("LG_SGF", "SPD") else "PDLC"

        if rec is None:
            rec = FilmStateRecord(
                state_key=state_key,
                scene_id=scene_id,
                window_id=win.window_id,
                film_type=film_type_str,
                film_rows=rows,
                film_cols=cols,
                cells_json=json.dumps(opacity_matrix),
                updated_at=synced_at,
            )
            session.add(rec)
        else:
            prev_grid = json.loads(rec.cells_json)
            rec.film_type = film_type_str
            rec.cells_json = json.dumps(opacity_matrix)
            rec.updated_at = synced_at
            session.add(rec)

            # Record transition log
            for r in range(rows):
                for c in range(cols):
                    prev_op = prev_grid[r][c]
                    new_op = opacity_matrix[r][c]
                    if abs(prev_op - new_op) > 0.01:
                        session.add(
                            FilmApplicationLogRecord(
                                scene_id=scene_id,
                                window_id=win.window_id,
                                timestamp=_iso(target_dt),
                                row=r,
                                col=c,
                                previous_opacity=prev_op,
                                new_opacity=new_op,
                                previous_state=opacity_to_applied_state(prev_op),
                                new_state=opacity_to_applied_state(new_op),
                                plan_id=f"thinq_sync_{int(datetime.now(UTC).timestamp())}",
                                status="applied",
                                note="LG ThinQ ON SGF sync",
                                reasons_json="[]",
                                created_at=synced_at,
                            )
                        )

        devices.append(
            ThinQHubDevicePayload(
                hub_device_id=request.hub_device_id,
                protocol_version="LG_THINQ_ON_SGF_V1",
                scene_id=scene_id,
                window_id=win.window_id,
                timestamp=_iso(target_dt),
                hardware_mode=request.hardware_mode,
                cell_opacity_matrix=opacity_matrix,
                cell_transmittance_matrix=trans_matrix,
                active_shaded_cells=active_count,
                dimmed_cells=dimmed_count,
                energy_daylight_score=daylight_score,
                decision_summary_ko=decision_summary,
            )
        )

    session.commit()

    return ThinQHubSyncResponse(
        synced_at=synced_at,
        metrics=plan_resp.metrics,
        devices=devices,
    )
