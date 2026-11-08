"""Production inference for the frozen FR-01 KMA/Open-Meteo model bundle."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pvlib.irradiance import erbs
from pvlib.location import Location
from sqlalchemy import Engine

from app.nowcast.kma_model_artifact import MODEL_VERSION, load_model_bundle, sha256_file
from app.nowcast.kma_operational_repository import (
    KMA_MAX_AGE,
    KmaOperationalInput,
    OpenMeteoOperationalInput,
    OperationalNowcastRepository,
    OperationalPointRecord,
    site_key,
)
from engine.fr01.models import Site

ALLOWED_ROLLOUT_MODES = {"shadow", "active"}
HORIZONS = (10, 30, 60)
MIN_SOLAR_ELEVATION_DEG = 5.0
DEFAULT_ARTIFACT_ROOT = Path(__file__).resolve().parent / "model_artifacts" / MODEL_VERSION


def floor_to_ten_minutes(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("prediction origin must be timezone-aware")
    utc = value.astimezone(UTC)
    return utc.replace(minute=(utc.minute // 10) * 10, second=0, microsecond=0)


def _solar_geometry(
    site: Site, timestamps: tuple[datetime, ...]
) -> dict[datetime, dict[str, float]]:
    location = Location(site.latitude_deg, site.longitude_deg, tz="UTC")
    index = pd.DatetimeIndex(timestamps)
    position = location.get_solarposition(index)
    clear_sky = location.get_clearsky(index)
    return {
        timestamp: {
            "elevation": float(position["apparent_elevation"].iloc[index_value]),
            "zenith": float(position["apparent_zenith"].iloc[index_value]),
            "clear_sky_dni": max(0.0, float(clear_sky["dni"].iloc[index_value])),
        }
        for index_value, timestamp in enumerate(timestamps)
    }


def _dsr_to_dni(dsr_W_m2: float, zenith_deg: float, timestamp: datetime) -> float:
    if dsr_W_m2 <= 0.0 or zenith_deg >= 89.5:
        return 0.0
    decomposition = erbs(dsr_W_m2, zenith_deg, timestamp)
    return max(0.0, float(np.asarray(decomposition["dni"]).item()))


def _kma_problem(kma: KmaOperationalInput | None, origin: datetime) -> str | None:
    if kma is None:
        return "missing_kma_snapshot"
    age = origin - kma.observed_at
    if age < timedelta(0):
        return "future_kma_observation"
    if age > KMA_MAX_AGE:
        return "stale_kma_observation"
    if kma.dsr_W_m2 is None:
        return "missing_kma_dsr"
    if kma.cloud_cover_pct is None:
        return "missing_kma_cloud"
    if kma.dsr_W_m2 < 0.0 or not 0.0 <= kma.cloud_cover_pct <= 100.0:
        return "invalid_kma_observation"
    return None


class FrozenOperationalNowcaster:
    """Hash-verified, prediction-only service. It has no fitting interface."""

    def __init__(
        self,
        engine: Engine,
        *,
        rollout_mode: str = "shadow",
        artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    ) -> None:
        if rollout_mode not in ALLOWED_ROLLOUT_MODES:
            raise ValueError("rollout_mode must be shadow or active")
        self.repository = OperationalNowcastRepository(engine)
        self.rollout_mode = rollout_mode
        self.artifact_root = artifact_root
        self.models = load_model_bundle(artifact_root)
        self.manifest_sha256 = sha256_file(artifact_root / "manifest.json")

    def _model_quantiles(
        self,
        *,
        site: Site,
        origin: datetime,
        horizon: int,
        kma: KmaOperationalInput,
        open_meteo: OpenMeteoOperationalInput,
        geometries: dict[datetime, dict[str, float]],
    ) -> tuple[float, float, float] | None:
        valid_time = origin + timedelta(minutes=horizon)
        origin_geometry = geometries[origin]
        target_geometry = geometries[valid_time]
        if (
            origin_geometry["elevation"] < MIN_SOLAR_ELEVATION_DEG
            or target_geometry["elevation"] < MIN_SOLAR_ELEVATION_DEG
            or origin_geometry["clear_sky_dni"] <= 0.0
            or target_geometry["clear_sky_dni"] <= 0.0
        ):
            return None
        assert kma.dsr_W_m2 is not None
        assert kma.cloud_cover_pct is not None
        kma_prior = _dsr_to_dni(kma.dsr_W_m2, origin_geometry["zenith"], origin)
        k_erbs = kma_prior / origin_geometry["clear_sky_dni"]
        k_om = open_meteo.dni_W_m2 / target_geometry["clear_sky_dni"]
        observation_weight = math.exp(-(horizon / 60.0) / 1.5)
        features = np.asarray(
            [
                [
                    1.0,
                    k_erbs * observation_weight + k_om * (1.0 - observation_weight),
                    k_om,
                    1.0 - kma.cloud_cover_pct / 100.0,
                    0.0,
                ]
            ],
            dtype=np.float64,
        )
        factors = self.models[horizon].predict_factors(features)
        clear_sky_target = target_geometry["clear_sky_dni"]
        return (
            round(float(factors["p10"][0] * clear_sky_target), 3),
            round(float(factors["p50"][0] * clear_sky_target), 3),
            round(float(factors["p90"][0] * clear_sky_target), 3),
        )

    def predict_and_persist(
        self,
        *,
        scene_id: str,
        site: Site,
        prediction_origin: datetime,
        created_at: datetime | None = None,
    ) -> str:
        origin = floor_to_ten_minutes(prediction_origin)
        current = (created_at or datetime.now(UTC)).astimezone(UTC)
        if origin > current:
            raise ValueError("prediction origin must not be in the future")
        key = site_key(site.latitude_deg, site.longitude_deg)
        kma = self.repository.latest_kma(key, origin)
        kma_problem = _kma_problem(kma, origin)
        timestamps = (origin,) + tuple(origin + timedelta(minutes=value) for value in HORIZONS)
        geometries = _solar_geometry(site, timestamps)
        points: list[OperationalPointRecord] = []

        for horizon in HORIZONS:
            valid_time = origin + timedelta(minutes=horizon)
            open_meteo = self.repository.open_meteo_at(key, origin, valid_time)
            flags: list[str] = []
            if kma_problem:
                flags.append(kma_problem)
            if open_meteo is None:
                flags.append("missing_open_meteo")
                points.append(
                    OperationalPointRecord(
                        horizon,
                        valid_time,
                        "unavailable",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        kma.snapshot_id if kma else None,
                        kma.observed_at if kma else None,
                        None,
                        None,
                        True,
                        "missing_open_meteo",
                        tuple(flags),
                    )
                )
                continue

            quantiles = None
            if kma_problem is None and kma is not None:
                quantiles = self._model_quantiles(
                    site=site,
                    origin=origin,
                    horizon=horizon,
                    kma=kma,
                    open_meteo=open_meteo,
                    geometries=geometries,
                )
                if quantiles is None:
                    flags.append("low_solar_elevation")

            use_ai = horizon in {10, 30} and self.rollout_mode == "active" and quantiles is not None
            if use_ai:
                assert quantiles is not None
                source = "ai_model"
                selected = quantiles[1]
                public_quantiles: tuple[float | None, float | None, float | None] = quantiles
                fallback_used = False
                fallback_reason = None
            else:
                selected = open_meteo.dni_W_m2
                public_quantiles = (None, None, None)
                if horizon == 60 or self.rollout_mode == "shadow":
                    source = "open_meteo_default"
                    fallback_used = False
                    fallback_reason = None
                else:
                    source = "open_meteo_fallback"
                    fallback_used = True
                    fallback_reason = kma_problem or "low_solar_elevation"

            shadow_quantiles = (
                quantiles if quantiles is not None and not use_ai else (None, None, None)
            )
            points.append(
                OperationalPointRecord(
                    horizon,
                    valid_time,
                    source,
                    round(selected, 3),
                    public_quantiles[0],
                    public_quantiles[1],
                    public_quantiles[2],
                    round(open_meteo.dni_W_m2, 3),
                    shadow_quantiles[0],
                    shadow_quantiles[1],
                    shadow_quantiles[2],
                    kma.snapshot_id if kma else None,
                    kma.observed_at if kma else None,
                    open_meteo.snapshot_id,
                    open_meteo.fetched_at,
                    fallback_used,
                    fallback_reason,
                    tuple(flags),
                )
            )

        return self.repository.persist_run(
            scene_id=scene_id,
            key=key,
            prediction_origin=origin,
            created_at=current,
            model_version=MODEL_VERSION,
            model_manifest_sha256=self.manifest_sha256,
            rollout_mode=self.rollout_mode,
            points=tuple(points),
        )
