"""Pydantic schemas for FR-04 Uncertainty-Aware Adaptive Smart Film Control & ThinQ ON Bridge."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.scene import Timestamp
from app.schemas.shading_plan import ShadingPlan

HardwareFilmMode = Literal["PDLC", "LG_SGF", "SPD"]


class AdaptiveBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class UncertaintyTimelinePoint(AdaptiveBaseModel):
    """Irradiance quantile predictions at a single timestamp for cloud-uncertainty modeling."""

    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)

    timestamp: Timestamp
    dni_p10_W_m2: Annotated[float, Field(ge=0.0)]
    dni_p50_W_m2: Annotated[float, Field(ge=0.0)]
    dni_p90_W_m2: Annotated[float, Field(ge=0.0)]

    @model_validator(mode="after")
    def validate_quantiles_order(self) -> Self:
        if not (self.dni_p10_W_m2 <= self.dni_p50_W_m2 <= self.dni_p90_W_m2):
            raise ValueError(
                f"Quantiles must satisfy p10 <= p50 <= p90, got {self.dni_p10_W_m2}, "
                f"{self.dni_p50_W_m2}, {self.dni_p90_W_m2}"
            )
        return self


class AdaptiveShadingPlanRequest(AdaptiveBaseModel):
    """Request contract for computing an uncertainty-aware stabilized shading plan."""

    start_time: str
    duration_minutes: Annotated[int, Field(ge=1, le=1440)] = 120
    interval_seconds: Annotated[int, Field(ge=1, le=3600)] = 300
    hardware_mode: HardwareFilmMode = "LG_SGF"
    enable_edge_margin: bool = False
    auto_tune_weights: bool = True
    apply_to_virtual_film: bool = False
    uncertainty_timeline: list[UncertaintyTimelinePoint] = Field(default_factory=list)

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


class AdaptiveComparisonMetrics(AdaptiveBaseModel):
    """Quantitative performance comparison between baseline FR-02 and adaptive FR-04 plans.

    Percent values are signed: positive is better than the baseline, negative is worse.
    Violation counts are cell-steps above the protection cap (q50 and q90 variants), counted
    against the cap itself and also given for the baseline.
    """

    baseline_switch_count: int
    adaptive_switch_count: int
    flicker_reduction_pct: float
    baseline_mean_opacity: float
    adaptive_mean_opacity: float
    daylight_gain_pct: float
    protection_violation_count: int
    baseline_violation_count: int = 0
    protection_violation_count_q90: int = 0
    baseline_violation_count_q90: int = 0
    baseline_total_change: float = 0.0
    adaptive_total_change: float = 0.0
    total_change_reduction_pct: float = 0.0
    tuned_weights: dict[str, float] = Field(default_factory=dict)


class CellDimmingTrajectory(AdaptiveBaseModel):
    """Time-series dimming trajectory for a film cell supporting continuous opacity and transmittance."""

    window_id: str
    row: int
    col: int
    timestamps: list[str]
    opacities: list[float]
    transmittances: list[float]


class AdaptiveShadingPlanResponse(AdaptiveBaseModel):
    """Response contract returning baseline, stabilized plan, dimming trajectories, and comparison metrics."""

    scene_id: str
    scene_revision: int
    hardware_mode: HardwareFilmMode
    baseline_plan: ShadingPlan
    stabilized_plan: ShadingPlan
    dimming_trajectories: list[CellDimmingTrajectory]
    metrics: AdaptiveComparisonMetrics


class ThinQHubSyncRequest(AdaptiveBaseModel):
    """Request to compute adaptive plan and synchronize continuous dimming to LG ThinQ ON hub."""

    hub_device_id: str = "thinq_on_hub_01"
    target_timestamp: Timestamp
    hardware_mode: HardwareFilmMode = "LG_SGF"
    plan_request: AdaptiveShadingPlanRequest


class ThinQHubDevicePayload(AdaptiveBaseModel):
    """Device payload delivered to LG ThinQ ON hub for smart film window control."""

    hub_device_id: str
    protocol_version: str = "LG_THINQ_ON_SGF_V1"
    scene_id: str
    window_id: str
    timestamp: str
    hardware_mode: HardwareFilmMode
    cell_opacity_matrix: list[list[float]]
    cell_transmittance_matrix: list[list[float]]
    active_shaded_cells: int
    dimmed_cells: int
    energy_daylight_score: float
    decision_summary_ko: str


class ThinQHubSyncResponse(AdaptiveBaseModel):
    """Response returned upon synchronizing adaptive shading states with LG ThinQ ON hub."""

    synced_at: str
    metrics: AdaptiveComparisonMetrics
    devices: list[ThinQHubDevicePayload]
