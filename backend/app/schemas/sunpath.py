"""FR-01 request and response contracts; FR-06 Scene remains unchanged."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DniSource = Literal["scene_weather", "open_meteo", "pvlib_clear_sky", "unknown"]
SunpathErrorCode = Literal[
    "FURNITURE_OVERLAP",
    "INTERSECTION_LIMIT_EXCEEDED",
    "SAMPLE_LIMIT_EXCEEDED",
    "INVALID_SCENE_GEOMETRY",
    "INVALID_PREDICTION_REQUEST",
]
SunpathErrorTargetType = Literal["furniture", "protected_region", "scene", "request"]
FallbackReason = Literal[
    "scene_weather_missing",
    "scene_weather_stale",
    "scene_weather_invalid",
    "scene_weather_out_of_coverage",
    "open_meteo_timeout",
    "open_meteo_transport_error",
    "open_meteo_invalid_response",
    "open_meteo_out_of_coverage",
    "clear_sky_error",
    "none",
]
NonnegativeIrradiance = Annotated[float, Field(ge=0)]


class SunpathModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class SunpathErrorDetail(SunpathModel):
    code: SunpathErrorCode
    message: str
    target_type: SunpathErrorTargetType | None = None
    target_id: str | None = None
    target_name: str | None = None
    related_target_type: SunpathErrorTargetType | None = None
    related_target_id: str | None = None
    related_target_name: str | None = None
    context: dict[str, object] = Field(default_factory=dict)


class SunpathPredictionRequest(SunpathModel):
    start_time: str
    duration_minutes: Annotated[int, Field(ge=1, le=1440)] = 120
    interval_seconds: Annotated[int, Field(ge=1, le=3600)] = 300
    detail_level: Literal["summary", "diagnostic"] = "summary"
    protected_region_ids: list[str] | None = None
    expected_scene_revision: Annotated[int, Field(ge=1, le=2**63 - 1)] | None = None

    @field_validator("start_time")
    @classmethod
    def timezone_aware_start(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("start_time must be an ISO 8601 timestamp") from error
        if parsed.utcoffset() is None:
            raise ValueError("start_time must include a timezone offset")
        return value

    @field_validator("protected_region_ids")
    @classmethod
    def selected_regions_are_nonempty_and_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("protected_region_ids must not be empty")
        if any(not isinstance(region_id, str) or not region_id for region_id in value):
            raise ValueError("protected_region_ids must contain non-empty strings")
        if len(value) != len(set(value)):
            raise ValueError("protected_region_ids must not contain duplicates")
        return value


class CandidatePathResponse(SunpathModel):
    window_id: str
    window_hit_m: tuple[float, float, float]
    sample_to_window_distance_m: float
    reverse_direction_enu: tuple[float, float, float]
    incoming_direction_enu: tuple[float, float, float]


class BlockerResponse(SunpathModel):
    kind: str
    object_id: str
    hit_m: tuple[float, float, float]
    distance_m: float


class SampleResultResponse(SunpathModel):
    sample_index: int
    point_m: tuple[float, float, float]
    status: Literal["exposed", "not_exposed"]
    reason: str
    candidate_path: CandidatePathResponse | None
    blocker: BlockerResponse | None
    direct_irradiance_W_m2: NonnegativeIrradiance | None = None


class RegionTimeResponse(SunpathModel):
    timestamp: datetime
    status: Literal["exposed", "not_exposed"]
    sample_count: Annotated[int, Field(ge=0)]
    exposed_sample_count: int
    max_direct_irradiance_W_m2: NonnegativeIrradiance | None
    samples: list[SampleResultResponse] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class ExposureIntervalResponse(SunpathModel):
    start_time: datetime
    end_time: datetime


class RegionPredictionResponse(SunpathModel):
    region_id: str
    aggregation: Literal["any_sample"] = "any_sample"
    first_predicted_exposure_time: datetime | None
    exposed_at_start: bool
    exposure_intervals: list[ExposureIntervalResponse]
    timeline: list[RegionTimeResponse]


class SolarPositionResponse(SunpathModel):
    timestamp: datetime
    elevation_deg: float
    azimuth_deg_true: float
    sun_direction_enu: tuple[float, float, float]


class SunpathPredictionResponse(SunpathModel):
    result_version: Literal["0.4.0"] = "0.4.0"
    scene_id: str
    scene_revision: int
    coordinate_frame: Literal["ENU"] = "ENU"
    units: Literal["m"] = "m"
    exposure_basis: Literal["geometric_direct_path"] = "geometric_direct_path"
    optics_applied: Literal[False] = False
    start_time: datetime
    end_time: datetime
    interval_seconds: int
    timestamps: list[datetime]
    solar_positions: list[SolarPositionResponse]
    irradiance: list[IrradianceResponse]
    regions: list[RegionPredictionResponse]
    assumptions: list[str]


class IrradianceResponse(SunpathModel):
    """Request-scoped DNI provenance for one prediction timestamp."""

    timestamp: datetime
    baseline_dni_W_m2: NonnegativeIrradiance | None
    final_dni_W_m2: NonnegativeIrradiance | None
    source: DniSource
    fallback_used: bool
    fallback_reason: FallbackReason | None
    valid_time: datetime | None
    fetched_at: datetime | None
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    request_latitude_deg: float | None = None
    request_longitude_deg: float | None = None
    grid_latitude_deg: float | None = None
    grid_longitude_deg: float | None = None
    grid_elevation_m: float | None = None
    model_selection: Literal["best_match"] | None = None
    resolved_model: str | None = None
    ml_applied: Literal[False] = False
    ml_reason: Literal["stage2_not_enabled"] = "stage2_not_enabled"
    model_version: None = None
    prediction_method: Literal[
        "stage1_weather_selection", "deterministic_nowcast", "clear_sky_fallback"
    ] = "stage1_weather_selection"
    nowcast_run_id: str | None = None
    nowcast_horizon_seconds: int | None = Field(default=None, ge=0)
    clear_sky_dni_W_m2: NonnegativeIrradiance | None = None
    clear_sky_index: NonnegativeIrradiance | None = None
    source_snapshots: list[SourceSnapshotResponse] = Field(default_factory=list)


class SourceSnapshotResponse(SunpathModel):
    snapshot_id: str
    provider: str
    role: Literal["nowcast", "forecast"]
    weight: float = Field(gt=0, le=1)
