"""Pydantic schemas for FR-03 & FR-06 Vision AI furniture 3D bounding box estimation."""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.scene import Occluder, ProtectedRegion
from engine.vision_ai.protocols import DetectionSource, FurnitureLabel

EstimationQuality = Literal["high", "medium", "low_needs_aruco"]
CaptureShapeHint = Literal["rectangle", "circle", "ellipse", "l-shape"]
VisionRequestMode = Literal["normal", "experiment"]
DepthEstimationSource = Literal["depth_anything_v2", "synthetic_geometric", "provided_metric_map"]

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
    fov_deg: Annotated[float, Field(gt=10.0, lt=170.0)] | None = None
    focal_length_35mm: Annotated[float, Field(gt=5.0, lt=300.0)] | None = None
    marker_anchor: MarkerAnchorInput = Field(default_factory=MarkerAnchorInput)
    T_ENU_camera: Annotated[list[float], Field(min_length=16, max_length=16)] | None = None
    depth_scale_alpha: float | None = None
    depth_shift_beta: float | None = None
    image_base64: str | None = Field(default=None, min_length=1, max_length=16_000_000)
    detections: list[Detection2DInput] = Field(default_factory=list)
    mode: VisionRequestMode = "normal"
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
    diagnostics: "FurnitureDiagnostics | None" = None


class FurnitureDiagnostics(BaseModel):
    """Per-furniture evidence used to derive a 3D cuboid."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    box_xyxy_px: tuple[float, float, float, float]
    representative_depth_rule: str
    representative_depth_region_xyxy_px: tuple[int, int, int, int] | None = None
    representative_depth_median_m: float | None = None
    valid_depth_pixel_count: int = 0
    depth_source: Literal["depth_map", "detection_hint", "default"]
    depth_fallback_reason: str | None = None
    depth_iqr_m: float | None = None
    background_pixel_mixing_risk: Literal["low", "high", "not_evaluated"]
    dimension_sources: dict[str, Literal["measured", "prior", "clamped"]]
    prior_dimensions_m: tuple[float, float, float]
    unclamped_base_center_enu_m: tuple[float, float, float]
    unclamped_dimensions_m: tuple[float, float, float]
    room_clamp_reason: str | None = None


class VisionModelDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_mode: VisionRequestMode
    detector_source: DetectionSource
    depth_source: DepthEstimationSource
    yolo_adapter_selected: bool
    depth_anything_adapter_selected: bool


class ArucoDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    pose_method: str
    marker_corners_px: list[tuple[float, float]] | None = None
    reprojection_error_mean_px: float | None = None
    reprojection_error_max_px: float | None = None
    resolved_fx_px: float | None = None
    intrinsics_source: str | None = None


class DepthCalibrationDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    anchor_relative_depths: list[float]
    anchor_metric_depths_m: list[float]
    alpha: float
    beta: float
    residual_rmse_m: float | None = None


class VisionDiagnostics(BaseModel):
    """Optional, non-persistent evidence for field-test dry-runs."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    model: VisionModelDiagnostics
    aruco: ArucoDiagnostics
    depth: DepthCalibrationDiagnostics


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
    diagnostics: VisionDiagnostics | None = None
