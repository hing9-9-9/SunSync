"""FR-02 shading plan of one stored Scene, built on the FR-01 prediction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine

from app.schemas.scene import Scene
from app.schemas.shading_plan import ShadingPlan, ShadingPlanRequest
from app.schemas.sunpath import SunpathPredictionRequest, SunpathPredictionResponse
from app.services.sunpath import SunpathValidationError, predict_scene
from engine.fr02 import (
    ExposedTile,
    FilmWindow,
    build_plan,
    grade_a_needs,
    grade_bc_needs,
    map_tiles_to_cells,
)


def _film_windows(scene: Scene) -> list[FilmWindow]:
    profiles = {profile.optical_profile_id: profile for profile in scene.optical_profiles}
    return [
        FilmWindow(
            window.window_id,
            window.vertices_m[0],
            window.u_axis,
            window.v_axis,
            window.width_m,
            window.height_m,
            window.film_rows,
            window.film_cols,
            profiles[window.optical_profile_id].film_min_direct_transmittance,
            u_edges_m=tuple(
                [0.0] + [window.cell_to_uv_bounds(0, c)[0][1] for c in range(window.film_cols)]
            ),
            v_edges_m=tuple(
                [0.0]
                + [window.cell_to_uv_bounds(r, 0)[1][1] for r in reversed(range(window.film_rows))]
            ),
        )
        for window in scene.windows
    ]


def _caps(scene: Scene) -> dict[str, float]:
    if scene.policy is None:
        raise SunpathValidationError(
            "INVALID_SCENE_GEOMETRY",
            "공간 배치를 계산할 수 없습니다.",
            target_type="scene",
        )
    b = scene.policy.grades.B.max_direct_irradiance_W_m2
    c = scene.policy.grades.C.max_direct_irradiance_W_m2
    if b is None or c is None:
        raise SunpathValidationError(
            "INVALID_SCENE_GEOMETRY",
            "공간 배치를 계산할 수 없습니다.",
            target_type="scene",
        )
    return {"B": b, "C": c}


def _exposed_tiles(prediction: SunpathPredictionResponse) -> list[ExposedTile]:
    return [
        ExposedTile(
            entry.timestamp,
            region.region_id,
            sample.sample_index,
            sample.candidate_path.window_id,
            sample.candidate_path.window_hit_m,
            sample.direct_irradiance_W_m2,
        )
        for region in prediction.regions
        for entry in region.timeline
        for sample in entry.samples or []
        if sample.status == "exposed"
        and sample.reason == "clear_path"
        and sample.candidate_path is not None
    ]


def shading_plan_for_scene(
    scene: Scene,
    request: ShadingPlanRequest,
    request_received_at: datetime | None = None,
    nowcast_engine: Engine | None = None,
) -> ShadingPlan:
    """Return the shading plan of one validated Scene snapshot; nothing is stored."""

    prediction = predict_scene(
        scene,
        SunpathPredictionRequest(
            start_time=request.start_time,
            duration_minutes=request.duration_minutes,
            interval_seconds=request.interval_seconds,
            detail_level="diagnostic",
        ),
        request_received_at=request_received_at,
        nowcast_engine=nowcast_engine,
    )
    return shading_plan_from_prediction(scene, prediction)


def shading_plan_from_prediction(
    scene: Scene, prediction: SunpathPredictionResponse
) -> ShadingPlan:
    """Return the shading plan of a diagnostic prediction of the same Scene (no new prediction).

    A caller that needs the prediction as well (FR-04) passes it in instead of predicting twice.
    """

    windows = _film_windows(scene)
    candidates = map_tiles_to_cells(_exposed_tiles(prediction), windows)
    grades = {region.region_id: region.grade for region in scene.protected_regions}
    start, end, interval = prediction.start_time, prediction.end_time, prediction.interval_seconds
    needs = [
        *grade_a_needs(
            windows=windows,
            candidates=candidates,
            grades=grades,
            start_time=start,
            end_time=end,
            interval_seconds=interval,
        ),
        *grade_bc_needs(
            windows=windows,
            candidates=candidates,
            grades=grades,
            caps=_caps(scene),
            start_time=start,
            end_time=end,
            interval_seconds=interval,
        ),
    ]
    plan = build_plan(
        scene_id=prediction.scene_id,
        scene_revision=prediction.scene_revision,
        windows=windows,
        needs=needs,
        start_time=start,
        end_time=end,
    )
    return ShadingPlan.model_validate(plan)
