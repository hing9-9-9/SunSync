"""Pydantic schemas for FR-01.4 KMA AI × Open-Meteo hybrid quantile nowcasting."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.scene import Timestamp

NowcastMode = Literal["kma_ai_ensemble", "open_meteo_only", "shadow_compare"]


class KmaPointObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    timestamp: Timestamp
    dsr_W_m2: Annotated[float, Field(ge=0.0, le=1500.0)]
    cloud_cover_pct: Annotated[float, Field(ge=0.0, le=100.0)] = 0.0
    temperature_c: float = 20.0
    humidity_pct: Annotated[float, Field(ge=0.0, le=100.0)] = 50.0


class CloudPatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    patch_size_km: float = 10.0
    mean_cloud_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    upwind_cloud_gradient: float = 0.0
    motion_u_m_s: float = 0.0
    motion_v_m_s: float = 0.0
    cloud_top_height_m: float | None = None


class AiNowcastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    start_time: Timestamp
    duration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120
    interval_seconds: Annotated[int, Field(ge=60, le=3600)] = 600
    mode: NowcastMode = "kma_ai_ensemble"
    kma_observations: list[KmaPointObservation] = Field(default_factory=list)
    cloud_patch: CloudPatchSummary | None = None
    reference_actual_dni: dict[str, float] | None = None


class QuantileNowcastPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    timestamp: str
    solar_elevation_deg: float
    clear_sky_dni_W_m2: float
    open_meteo_dni_W_m2: float | None
    kma_ai_dni_W_m2: float | None
    dni_p10_W_m2: float
    dni_p50_W_m2: float
    dni_p90_W_m2: float
    clear_sky_index_p50: float
    uncertainty_spread_W_m2: float
    selected_dni_W_m2: float
    selected_source: str
    fallback_used: bool


class ComparisonMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    sample_count: int
    mean_uncertainty_spread_W_m2: float
    cloud_volatility_index: float
    kma_vs_open_meteo_mae_W_m2: float | None
    kma_ai_rmse_W_m2: float | None = None
    open_meteo_rmse_W_m2: float | None = None
    kma_ai_pinball_loss: float | None = None


class NowcastComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    run_id: str
    scene_id: str
    mode: NowcastMode
    model_version: str
    start_time: str
    end_time: str
    interval_seconds: int
    points: list[QuantileNowcastPoint]
    metrics: ComparisonMetrics


OperationalPredictionSource = Literal[
    "ai_model", "open_meteo_default", "open_meteo_fallback", "unavailable"
]


class OperationalNowcastPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    horizon_minutes: Literal[10, 30, 60]
    valid_time: str
    prediction_source: OperationalPredictionSource
    dni_W_m2: float | None
    p10_dni_W_m2: float | None
    p50_dni_W_m2: float | None
    p90_dni_W_m2: float | None
    open_meteo_dni_W_m2: float | None
    fallback_used: bool
    fallback_reason: str | None
    quality_flags: list[str]
    kma_snapshot_id: str | None
    kma_observed_at: str | None
    open_meteo_snapshot_id: str | None
    open_meteo_fetched_at: str | None


class OperationalNowcastResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    run_id: str
    scene_id: str
    prediction_origin: str
    created_at: str
    model_version: str
    model_manifest_sha256: str
    rollout_mode: Literal["shadow", "active"]
    status: Literal["ready", "degraded"]
    result_hash: str
    points: list[OperationalNowcastPoint]
