"""Pydantic schemas for FR-03 & FR-06 Vision AI furniture 3D bounding box estimation."""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.scene import Occluder, ProtectedRegion
from engine.vision_ai.protocols import DetectionSource, FurnitureLabel

EstimationQuality = Literal["high", "medium", "low_needs_aruco"]
CaptureShapeHint = Literal["rectangle", "circle", "ellipse", "l-shape"]

_UI_LABEL_ALIASES: dict[str, FurnitureLabel] = {
    "bookcase": "bookshelf",
    "bookshelf": "bookshelf",
    "desk": "desk",
    "table": "desk",
    "chair": "chair",
    "cabinet": "other",
    "display": "other",
    "other": "other",
}


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

    @field_validator("label", mode="before")
    @classmethod
    def _normalize_label(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _UI_LABEL_ALIASES.get(value.strip().lower(), value)
        return value


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
    image_base64: str | None = Field(default=None, min_length=1, max_length=16_000_000)
    detections: list[Detection2DInput] = Field(default_factory=list)
    apply_to_scene: bool = True
    auto_create_protected_regions: bool = True
    expected_revision: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _require_detections_or_image(self) -> Self:
        if self.image_base64 is None and len(self.detections) == 0:
            raise ValueError("Either detections or image_base64 must be provided.")
        return self


class EstimatedFurnitureResult(BaseModel):
    """Estimated 3D cuboid and optional protection region for a detected piece of furniture."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    occluder: Occluder
    protected_region: ProtectedRegion | None = None
    confidence: float
    calibrated_distance_m: float
    clamped_to_room: bool
    detection_source: DetectionSource = "client_hint"
    estimation_quality: EstimationQuality = "medium"
    needs_aruco_refinement: bool = False
    uncertainty_reason_ko: str = ""
    aruco_capture_guidance_ko: str = ""
    recommended_capture_shape: CaptureShapeHint = "rectangle"
    estimated_corners_enu_m: Annotated[list[tuple[float, float]], Field(strict=False)] = Field(
        default_factory=list
    )


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
    registration_fragment: dict[str, Any] | None = None
