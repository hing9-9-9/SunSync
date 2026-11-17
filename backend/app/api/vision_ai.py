"""API router for FR-03 & FR-06 Vision AI 3D furniture estimation and scene registration."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlmodel import Session

from app.api.capture_markers import saved_capture_target
from app.api.scenes import replace_scene
from app.db import get_session
from app.models.scene import SceneRecord
from app.schemas.scene import GradePolicy, Grades, ManualObservation, ProtectionPolicy, Scene
from app.schemas.vision_ai import (
    MarkerAnchorInput,
    VisionFurnitureEstimateRequest,
    VisionFurnitureEstimateResponse,
)
from app.services.stored_scene import load_stored_scene
from engine.vision_ai.pipeline import VisionRegistrationPipeline

router = APIRouter(tags=["vision-ai"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _build_vision_fragment(
    scene: Scene,
    target_marker: Any,
    t_enu_marker: list[float],
    marker_code_id: int,
    marker_side_m: float,
    obs_dict: dict[str, Any],
    estimated_items: list[Any],
) -> dict[str, Any]:
    """Build an FR-06-aligned registration fragment for Vision AI estimations."""
    if target_marker is not None:
        marker_payload = target_marker.model_dump(mode="json")
    else:
        marker_payload = {
            "marker_id": f"vision_marker_{marker_code_id}",
            "family": "aruco",
            "dictionary": "DICT_4X4_50",
            "code_id": marker_code_id,
            "side_length_m": marker_side_m,
            "plane": "window",
            "T_ENU_marker": t_enu_marker,
        }
    calibration_id = (
        scene.registration_calibration.calibration_id
        if scene.registration_calibration is not None
        else sha256(f"{scene.scene_id}:{marker_payload['marker_id']}".encode()).hexdigest()
    )
    return {
        "schema_version": "sunsync.fr06-fragment/1",
        "calibration_id": calibration_id,
        "units": "m",
        "coordinate_frame": "ENU",
        "markers": [marker_payload],
        "observations": [obs_dict],
        "occluders": [item.occluder.model_dump(mode="json") for item in estimated_items],
        "protected_regions": [
            item.protected_region.model_dump(mode="json")
            for item in estimated_items
            if item.protected_region is not None
        ],
    }


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
    if request.expected_revision is not None and scene.revision != request.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Revision conflict: reload the scene.",
        )

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
        selected = saved_capture_target(scene_id, session)
        if selected is not None:
            win = next(
                (w for w in scene.windows if w.window_id == selected["window_id"]),
                scene.windows[0],
            )
            try:
                _, _, u_m, v_m = win.module_marker_placement(
                    selected["module_row"], selected["module_col"]
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            t_enu_marker = win.marker_transform(u_m, v_m)
        else:
            # A window centre is not the selected film's centre on a multi-module window.
            # Retain the unambiguous single-film legacy path only.
            win = scene.windows[0]
            if (
                len(scene.windows) != 1
                or abs(win.width_m - 1.0) > 1e-6
                or abs(win.height_m - 1.0) > 1e-6
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Select and pair a film module before vision measurement.",
                )
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

    manual_obs = ManualObservation(
        observation_id=obs_id,
        method="manual",
        captured_at=datetime.now(UTC).isoformat(),
        uncertainty_m=0.05,
        confirmed=False,
        note="Vision AI 3D bounding box registration",
    )
    obs_dict = manual_obs.model_dump(mode="json")
    registration_fragment = _build_vision_fragment(
        scene=scene,
        target_marker=target_marker,
        t_enu_marker=t_enu_marker,
        marker_code_id=request.marker_anchor.code_id,
        marker_side_m=request.marker_anchor.side_length_m,
        obs_dict=obs_dict,
        estimated_items=estimated_items,
    )

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

        scene_data["observations"].append(obs_dict)

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

        replace_scene(scene_id, validated_scene, session)

    return VisionFurnitureEstimateResponse(
        scene_id=scene_id,
        scene_revision=final_revision,
        applied_to_scene=request.apply_to_scene,
        camera_position_enu_m=pose_res.camera_position_enu_m,
        depth_calibration_alpha=depth_calib.alpha,
        depth_calibration_beta=depth_calib.beta,
        estimated_items=estimated_items,
        registration_fragment=registration_fragment,
    )


def _extract_image_bytes(raw_body: bytes, content_type: str) -> bytes:
    """Extract image bytes from raw request body or simple multipart/form-data payload."""
    if "multipart/form-data" in content_type.lower() and b"\r\n\r\n" in raw_body:
        for part in raw_body.split(b"--"):
            if b"\r\n\r\n" not in part:
                continue
            headers, payload = part.split(b"\r\n\r\n", 1)
            if b"filename=" in headers or b"image/" in headers or b'name="image"' in headers:
                return payload[:-2] if payload.endswith(b"\r\n") else payload
    return raw_body


@router.post(
    "/scenes/{scene_id}/vision-furniture-photo",
    response_model=VisionFurnitureEstimateResponse,
    status_code=status.HTTP_200_OK,
)
async def estimate_furniture_3d_from_photo(
    scene_id: str,
    http_request: Request,
    session: SessionDependency,
    code_id: Annotated[int, Query(ge=0, lt=50)] = 0,
    side_length_m: Annotated[float, Query(gt=0.0, le=5.0)] = 0.6,
    apply_to_scene: Annotated[bool, Query()] = True,
    auto_create_protected_regions: Annotated[bool, Query()] = True,
    expected_revision: Annotated[int | None, Query(ge=1)] = None,
) -> VisionFurnitureEstimateResponse:
    """Accept raw image bytes or multipart upload, run Vision AI estimation, and update the scene."""
    raw_body = await http_request.body()
    image_bytes = _extract_image_bytes(raw_body, http_request.headers.get("content-type", ""))
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded image file is empty.",
        )
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        request = VisionFurnitureEstimateRequest(
            image_base64=image_b64,
            marker_anchor=MarkerAnchorInput(code_id=code_id, side_length_m=side_length_m),
            apply_to_scene=apply_to_scene,
            auto_create_protected_regions=auto_create_protected_regions,
            expected_revision=expected_revision,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid vision photo request parameters: {exc}",
        ) from exc
    return estimate_furniture_3d(scene_id=scene_id, request=request, session=session)
