"""Durable input selection and persistence for the frozen FR-01 AI nowcast."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from app.nowcast.repository import utc_z

KMA_MAX_AGE = timedelta(minutes=20)
OPEN_METEO_MAX_AGE = timedelta(hours=2)
RUN_MAX_AGE = timedelta(minutes=20)


def site_key(latitude_deg: float, longitude_deg: float) -> str:
    return f"{latitude_deg:.6f},{longitude_deg:.6f}"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class KmaOperationalInput:
    snapshot_id: str
    observed_at: datetime
    fetched_at: datetime
    dsr_W_m2: float | None
    cloud_cover_pct: float | None


@dataclass(frozen=True)
class OpenMeteoOperationalInput:
    snapshot_id: str
    fetched_at: datetime
    valid_time: datetime
    interval_start: datetime
    interval_end: datetime
    dni_W_m2: float


@dataclass(frozen=True)
class OperationalPointRecord:
    horizon_minutes: int
    valid_time: datetime
    prediction_source: str
    dni_W_m2: float | None
    dni_p10_W_m2: float | None
    dni_p50_W_m2: float | None
    dni_p90_W_m2: float | None
    open_meteo_dni_W_m2: float | None
    shadow_dni_p10_W_m2: float | None
    shadow_dni_p50_W_m2: float | None
    shadow_dni_p90_W_m2: float | None
    kma_snapshot_id: str | None
    kma_observed_at: datetime | None
    open_meteo_snapshot_id: str | None
    open_meteo_fetched_at: datetime | None
    fallback_used: bool
    fallback_reason: str | None
    quality_flags: tuple[str, ...]

    def result_document(self) -> dict[str, Any]:
        return {
            "horizon_minutes": self.horizon_minutes,
            "valid_time": utc_z(self.valid_time),
            "prediction_source": self.prediction_source,
            "dni_W_m2": self.dni_W_m2,
            "dni_p10_W_m2": self.dni_p10_W_m2,
            "dni_p50_W_m2": self.dni_p50_W_m2,
            "dni_p90_W_m2": self.dni_p90_W_m2,
            "open_meteo_dni_W_m2": self.open_meteo_dni_W_m2,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "quality_flags": list(self.quality_flags),
        }


class OperationalNowcastRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def latest_kma(self, key: str, origin: datetime) -> KmaOperationalInput | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT s.snapshot_id, s.fetched_at, p.valid_time,
                               p.ghi_W_m2, p.cloud_cover_percent
                        FROM weather_snapshot s
                        JOIN weather_point p ON p.snapshot_id = s.snapshot_id
                        WHERE s.provider = 'kma_gk2a'
                          AND s.site_key = :site_key
                          AND s.fetched_at <= :origin
                          AND p.valid_time <= :origin
                        ORDER BY p.valid_time DESC, s.fetched_at DESC, s.snapshot_id ASC
                        LIMIT 1
                        """
                    ),
                    {"site_key": key, "origin": utc_z(origin)},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return KmaOperationalInput(
            snapshot_id=str(row["snapshot_id"]),
            observed_at=_parse_utc(row["valid_time"]),
            fetched_at=_parse_utc(row["fetched_at"]),
            dsr_W_m2=float(row["ghi_W_m2"]) if row["ghi_W_m2"] is not None else None,
            cloud_cover_pct=float(row["cloud_cover_percent"])
            if row["cloud_cover_percent"] is not None
            else None,
        )

    def open_meteo_at(
        self, key: str, origin: datetime, valid_time: datetime
    ) -> OpenMeteoOperationalInput | None:
        cutoff = origin - OPEN_METEO_MAX_AGE
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT s.snapshot_id, s.fetched_at, p.valid_time,
                               p.interval_start, p.interval_end, p.dni_W_m2
                        FROM weather_snapshot s
                        JOIN weather_point p ON p.snapshot_id = s.snapshot_id
                        WHERE s.provider = 'open_meteo'
                          AND s.product = 'hourly_direct_normal_irradiance'
                          AND s.model = 'ecmwf_ifs025'
                          AND s.quality_status = 'valid'
                          AND s.site_key = :site_key
                          AND s.fetched_at <= :origin
                          AND s.fetched_at >= :cutoff
                          AND p.dni_W_m2 IS NOT NULL
                          AND p.interval_start <= :valid_time
                          AND :valid_time < p.interval_end
                        ORDER BY s.fetched_at DESC, s.snapshot_id ASC
                        LIMIT 1
                        """
                    ),
                    {
                        "site_key": key,
                        "origin": utc_z(origin),
                        "cutoff": utc_z(cutoff),
                        "valid_time": utc_z(valid_time),
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return OpenMeteoOperationalInput(
            snapshot_id=str(row["snapshot_id"]),
            fetched_at=_parse_utc(row["fetched_at"]),
            valid_time=_parse_utc(row["valid_time"]),
            interval_start=_parse_utc(row["interval_start"]),
            interval_end=_parse_utc(row["interval_end"]),
            dni_W_m2=float(row["dni_W_m2"]),
        )

    def persist_run(
        self,
        *,
        scene_id: str,
        key: str,
        prediction_origin: datetime,
        created_at: datetime,
        model_version: str,
        model_manifest_sha256: str,
        rollout_mode: str,
        points: tuple[OperationalPointRecord, ...],
    ) -> str:
        run_id = str(uuid4())
        snapshot_ids = sorted(
            {
                snapshot_id
                for point in points
                for snapshot_id in (point.kma_snapshot_id, point.open_meteo_snapshot_id)
                if snapshot_id is not None
            }
        )
        result_payload = json.dumps(
            [point.result_document() for point in points],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        result_hash = hashlib.sha256(result_payload).hexdigest()
        status = "degraded" if any(point.prediction_source == "unavailable" for point in points) else "ready"
        expires_at = created_at + RUN_MAX_AGE

        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ai_nowcast_run(
                      run_id, scene_id, site_key, prediction_origin, created_at,
                      model_version, model_manifest_sha256, input_snapshot_ids_json,
                      rollout_mode, status, result_hash, expires_at
                    ) VALUES (
                      :run_id, :scene_id, :site_key, :prediction_origin, :created_at,
                      :model_version, :model_manifest_sha256, :snapshot_ids,
                      :rollout_mode, :status, :result_hash, :expires_at
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "scene_id": scene_id,
                    "site_key": key,
                    "prediction_origin": utc_z(prediction_origin),
                    "created_at": utc_z(created_at),
                    "model_version": model_version,
                    "model_manifest_sha256": model_manifest_sha256,
                    "snapshot_ids": json.dumps(snapshot_ids),
                    "rollout_mode": rollout_mode,
                    "status": status,
                    "result_hash": result_hash,
                    "expires_at": utc_z(expires_at),
                },
            )
            for point in points:
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_nowcast_point(
                          run_id, horizon_minutes, valid_time, prediction_source,
                          dni_W_m2, dni_p10_W_m2, dni_p50_W_m2, dni_p90_W_m2,
                          open_meteo_dni_W_m2,
                          shadow_dni_p10_W_m2, shadow_dni_p50_W_m2, shadow_dni_p90_W_m2,
                          kma_snapshot_id, kma_observed_at,
                          open_meteo_snapshot_id, open_meteo_fetched_at,
                          fallback_used, fallback_reason, quality_flags_json
                        ) VALUES (
                          :run_id, :horizon, :valid_time, :source,
                          :dni, :p10, :p50, :p90, :om_dni,
                          :shadow_p10, :shadow_p50, :shadow_p90,
                          :kma_snapshot, :kma_observed,
                          :om_snapshot, :om_fetched,
                          :fallback_used, :fallback_reason, :quality_flags
                        )
                        """
                    ),
                    {
                        "run_id": run_id,
                        "horizon": point.horizon_minutes,
                        "valid_time": utc_z(point.valid_time),
                        "source": point.prediction_source,
                        "dni": point.dni_W_m2,
                        "p10": point.dni_p10_W_m2,
                        "p50": point.dni_p50_W_m2,
                        "p90": point.dni_p90_W_m2,
                        "om_dni": point.open_meteo_dni_W_m2,
                        "shadow_p10": point.shadow_dni_p10_W_m2,
                        "shadow_p50": point.shadow_dni_p50_W_m2,
                        "shadow_p90": point.shadow_dni_p90_W_m2,
                        "kma_snapshot": point.kma_snapshot_id,
                        "kma_observed": utc_z(point.kma_observed_at)
                        if point.kma_observed_at
                        else None,
                        "om_snapshot": point.open_meteo_snapshot_id,
                        "om_fetched": utc_z(point.open_meteo_fetched_at)
                        if point.open_meteo_fetched_at
                        else None,
                        "fallback_used": int(point.fallback_used),
                        "fallback_reason": point.fallback_reason,
                        "quality_flags": json.dumps(list(point.quality_flags)),
                    },
                )
        return run_id

    def latest_run(self, scene_id: str, now: datetime) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            run = (
                connection.execute(
                    text(
                        """
                        SELECT * FROM ai_nowcast_run
                        WHERE scene_id = :scene_id AND expires_at >= :now
                        ORDER BY created_at DESC, run_id ASC
                        LIMIT 1
                        """
                    ),
                    {"scene_id": scene_id, "now": utc_z(now)},
                )
                .mappings()
                .one_or_none()
            )
            if run is None:
                return None
            points = (
                connection.execute(
                    text(
                        """
                        SELECT * FROM ai_nowcast_point
                        WHERE run_id = :run_id
                        ORDER BY horizon_minutes
                        """
                    ),
                    {"run_id": run["run_id"]},
                )
                .mappings()
                .all()
            )
        return {"run": dict(run), "points": [dict(point) for point in points]}
