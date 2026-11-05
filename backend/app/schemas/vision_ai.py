"""Pydantic schemas for FR-03 & FR-06 Vision AI furniture 3D bounding box estimation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.scene import Occluder, ProtectedRegion
from engine.vision_ai.protocols import FurnitureLabel


class CameraIntrinsicsInput(BaseModel):
    """Camera intrinsics and image resolution."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    width: Annotated[int, Field(gt=0)] = 1280
    height: Annotated[int, Field(gt=0)] = 720
    fx: Annotated[float, Field(gt=0.0)] = 900.0
    fy: Annotated[float, Field(gt=0.0)] = 900.0
    cx: Annotated[float, Field(gt=0.0)] = 640.0
    cy: Annotated[float, Field(gt=0.0)] = 360.0


class Detection2DInput(BaseModel):
    """2D bounding box detection input with furniture label and depth hints."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    box_xyxy: Annotated[tuple[float, float, float, float], Field(strict=False)]
    label: FurnitureLabel
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.9
    observed_depth_rel: float | None = None
    depth_span_rel: float | None = None
    name: str | None = None
    protection_grade: Literal["A", "B", "C", "D"] = "B"


class MarkerAnchorInput(BaseModel):
    """Window ArUco marker anchor specification for metric depth calibration."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    marker_id: str | None = None
    code_id: Annotated[int, Field(ge=0, lt=50)] = 0
    side_length_m: Annotated[float, Field(gt=0.0, le=5.0)] = 0.6
    corners_px: (
        Annotated[
            list[Annotated[tuple[float, float], Field(strict=False)]],
            Field(min_length=4, max_length=4, strict=False),
        ]
        | None
    ) = None
    anchor_depth_rel: float = 1.0


class VisionFurnitureEstimateRequest(BaseModel):
    """Request payload for vision-based 3D furniture estimation and scene registration."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    camera: CameraIntrinsicsInput = Field(default_factory=CameraIntrinsicsInput)
    marker_anchor: MarkerAnchorInput = Field(default_factory=MarkerAnchorInput)
    T_ENU_camera: Annotated[list[float], Field(min_length=16, max_length=16)] | None = None
    depth_scale_alpha: float | None = None
    depth_shift_beta: float | None = None
    detections: Annotated[list[Detection2DInput], Field(min_length=1)]
    apply_to_scene: bool = True
    auto_create_protected_regions: bool = True


class EstimatedFurnitureResult(BaseModel):
    """Estimated 3D cuboid and optional protection region for a detected piece of furniture."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    occluder: Occluder
    protected_region: ProtectedRegion | None = None
    confidence: float
    calibrated_distance_m: float
    clamped_to_room: bool


class VisionFurnitureEstimateResponse(BaseModel):
    """Response returned by vision-based 3D furniture estimation endpoint."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    scene_id: str
    scene_revision: int
    applied_to_scene: bool
    camera_position_enu_m: Annotated[tuple[float, float, float], Field(strict=False)]
    depth_calibration_alpha: float
    depth_calibration_beta: float
    estimated_items: list[EstimatedFurnitureResult]
