"""API router for FR-03 & FR-06 Vision AI 3D furniture estimation and scene registration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlmodel import Session, select

from app.db import get_session
from app.models.scene import SceneRecord
from app.schemas.scene import GradePolicy, Grades, ManualObservation, ProtectionPolicy, Scene
from app.schemas.vision_ai import (
    VisionFurnitureEstimateRequest,
    VisionFurnitureEstimateResponse,
)
from app.services.stored_scene import load_stored_scene
from app.ui_scene import UISpaceRecord
from engine.vision_ai.pipeline import VisionRegistrationPipeline

router = APIRouter(tags=["vision-ai"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/scenes/{scene_id}/vision-furniture-estimate",
    response_model=VisionFurnitureEstimateResponse,
    status_code=status.HTTP_200_OK,
)
def estimate_furniture_3d(
    scene_id: str,
    request: VisionFurnitureEstimateRequest,
    session: SessionDependency,
) -> VisionFurnitureEstimateResponse:
    """Classify 2D furniture and estimate calibrated 3D bounding boxes anchored by window ArUco marker."""
    record = session.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found.")

    scene = load_stored_scene(record.scene_json)

    # Resolve target anchor marker transform
    target_marker = None
    if request.marker_anchor.marker_id:
        target_marker = next(
            (m for m in scene.markers if m.marker_id == request.marker_anchor.marker_id),
            None,
        )
    if target_marker is None:
        target_marker = next(
            (m for m in scene.markers if m.code_id == request.marker_anchor.code_id),
            None,
        )
    if target_marker is None:
        # Fall back to first window marker
        target_marker = next((m for m in scene.markers if m.plane == "window"), None)

    t_enu_marker: list[float]
    if target_marker is not None:
        t_enu_marker = list(target_marker.T_ENU_marker)
    elif len(scene.windows) > 0:
        win = scene.windows[0]
        t_enu_marker = win.marker_transform(win.width_m / 2.0, win.height_m / 2.0)
    elif request.T_ENU_camera is not None:
        t_enu_marker = [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Scene has no window or ArUco marker to anchor camera pose.",
        )

    obs_id = f"obs_vision_{uuid4().hex[:8]}"
    effective_policy_id = (
        scene.policy.policy_id if scene.policy is not None else f"policy_{scene_id}"
    )

    existing_scene_ids = (
        {occ.occluder_id for occ in scene.occluders}
        | {reg.region_id for reg in scene.protected_regions}
        | {win.window_id for win in scene.windows}
        | {scene.room.room_id}
        | {m.marker_id for m in scene.markers}
        | {obs.observation_id for obs in scene.observations}
    )

    pipeline = VisionRegistrationPipeline()
    try:
        pose_res, depth_calib, estimated_items = pipeline.run(
            scene_id=scene_id,
            room_id=scene.room.room_id,
            room_floor_vertices=[(p[0], p[1]) for p in scene.room.floor_vertices_m],
            room_height_m=scene.room.height_m,
            T_ENU_marker=t_enu_marker,
            request=request,
            observation_id=obs_id,
            policy_id=effective_policy_id,
            existing_ids=existing_scene_ids,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Vision registration failed: {exc}",
        ) from exc

    final_revision = scene.revision

    if request.apply_to_scene:
        final_revision = scene.revision + 1
        scene_data = scene.model_dump(mode="json")
        scene_data["revision"] = final_revision
        if scene_data.get("scenario") is not None:
            scene_data["scenario"]["scene_revision"] = final_revision

        if scene_data.get("policy") is None and request.auto_create_protected_regions:
            default_policy = ProtectionPolicy(
                policy_id=effective_policy_id,
                version="0.2.0",
                status="proposal_demo_only",
                metric="direct_solar_irradiance_W_m2",
                aggregation="max_over_sample_points",
                threshold_status="synthetic_demo_not_agreed",
                max_time_step_s=10.0,
                brightening_margin_W_m2=5.0,
                brightening_hold_s=60.0,
                grades=Grades(
                    A=GradePolicy(
                        rank=1,
                        constraint="hard",
                        rule="block_geometric_direct_paths",
                        max_direct_irradiance_W_m2=0,
                    ),
                    B=GradePolicy(
                        rank=2,
                        constraint="soft",
                        rule="intensity_cap",
                        max_direct_irradiance_W_m2=30,
                    ),
                    C=GradePolicy(
                        rank=3,
                        constraint="soft",
                        rule="intensity_cap",
                        max_direct_irradiance_W_m2=70,
                    ),
                    D=GradePolicy(
                        rank=4,
                        constraint="none",
                        rule="daylight_priority",
                        max_direct_irradiance_W_m2=None,
                    ),
                ),
            )
            scene_data["policy"] = default_policy.model_dump(mode="json")

        manual_obs = ManualObservation(
            observation_id=obs_id,
            method="manual",
            captured_at=datetime.now(UTC).isoformat(),
            uncertainty_m=0.05,
            confirmed=False,
            note="Vision AI 3D bounding box registration",
        )
        scene_data["observations"].append(manual_obs.model_dump(mode="json"))

        for item in estimated_items:
            scene_data["occluders"].append(item.occluder.model_dump(mode="json"))
            if item.protected_region is not None:
                scene_data["protected_regions"].append(
                    item.protected_region.model_dump(mode="json")
                )

        try:
            validated_scene = Scene.model_validate(scene_data)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Validated scene geometry failed: {exc}",
            ) from exc

        record.revision = validated_scene.revision
        record.scene_json = validated_scene.model_dump_json()
        session.add(record)

        ui_space_record = session.exec(
            select(UISpaceRecord).where(UISpaceRecord.scene_id == scene_id)
        ).first()
        if ui_space_record is not None:
            ui_space_record.scene_revision = validated_scene.revision
            try:
                ui_payload = json.loads(ui_space_record.payload_json)
                ui_payload["serverRevision"] = validated_scene.revision
                ui_space_record.payload_json = json.dumps(ui_payload)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
            session.add(ui_space_record)

        session.commit()

    return VisionFurnitureEstimateResponse(
        scene_id=scene_id,
        scene_revision=final_revision,
        applied_to_scene=request.apply_to_scene,
        camera_position_enu_m=pose_res.camera_position_enu_m,
        depth_calibration_alpha=depth_calib.alpha,
        depth_calibration_beta=depth_calib.beta,
        estimated_items=estimated_items,
    )
