"""Step D durable Open-Meteo interval selection and clear-sky fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from uuid import uuid4

from sqlalchemy import Engine, text

from app.nowcast.repository import utc_z
from engine.fr01.models import Site

ALGORITHM_VERSION = "deterministic_snapshot_selection_v1"
OPEN_METEO_FRESHNESS_SECONDS = 7200


@dataclass(frozen=True)
class SelectedDni:
    value: float | None
    source: str
    snapshot_id: str | None
    provider: str | None
    fetched_at: datetime | None
    method: str
    valid_time: datetime | None
    interval_start: datetime | None
    interval_end: datetime | None
    fallback_used: bool
    fallback_reason: str | None
    clear_sky_dni: float | None
    provenance: dict[str, object] | None = None


def _clear_sky_dni(site: Site, timestamp: datetime) -> float:
    try:
        import pandas as pd
        from pvlib.location import Location

        value = Location(site.latitude_deg, site.longitude_deg, tz="UTC").get_clearsky(
            pd.DatetimeIndex([timestamp.astimezone(UTC)])
        )["dni"].iloc[0]
        value = float(value)
        if not isfinite(value) or value < 0:
            raise ValueError
        return value
    except Exception as error:
        raise ValueError("clear_sky_error") from error


def select_and_record(engine: Engine, site: Site, timestamps: tuple[datetime, ...], now: datetime) -> tuple[str, list[SelectedDni]]:
    """Select fresh durable Open-Meteo hourly means independently per timestamp.

    The provider timestamp is the *end* of its one-hour mean.  A point applies
    only when ``interval_start <= timestamp < interval_end``.  No GK2A source,
    interpolation, or request-time provider call participates in this path.
    """
    current = now.astimezone(UTC)
    candidates: list[SelectedDni] = []
    snapshot_ids: set[str] = set()
    with engine.connect() as conn:
        for timestamp in timestamps:
            rows = conn.execute(text("""SELECT s.snapshot_id,s.provider,s.fetched_at,s.quality_status,
                    s.provenance_json,p.valid_time,p.interval_start,p.interval_end,p.dni_W_m2
                FROM weather_snapshot s JOIN weather_point p ON p.snapshot_id=s.snapshot_id
                WHERE s.provider='open_meteo' AND p.dni_W_m2 IS NOT NULL
                  AND p.interval_start <= :timestamp AND :timestamp < p.interval_end
                ORDER BY s.fetched_at DESC, s.snapshot_id ASC"""),
                {"timestamp": utc_z(timestamp)}).mappings().all()
            selected = None
            for row in rows:
                value = row["dni_W_m2"]
                fetched = datetime.fromisoformat(row["fetched_at"])
                try:
                    provenance = json.loads(row["provenance_json"] or "null")
                except json.JSONDecodeError:
                    provenance = None
                if row["quality_status"] != "valid" or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
                    continue
                # Snapshots created before provider-grid provenance existed are
                # intentionally excluded: their point coordinate was the request
                # coordinate and cannot be represented truthfully in v0.3.
                if not isinstance(provenance, dict) or provenance.get("spatial_provenance") != "provider_grid":
                    continue
                if (current - fetched).total_seconds() > OPEN_METEO_FRESHNESS_SECONDS or fetched > current:
                    continue
                try:
                    clear = _clear_sky_dni(site, timestamp)
                except ValueError:
                    clear = None
                selected = SelectedDni(float(value), "open_meteo", row["snapshot_id"], "open_meteo", fetched,
                                       "deterministic_nowcast", datetime.fromisoformat(row["valid_time"]),
                                       datetime.fromisoformat(row["interval_start"]), datetime.fromisoformat(row["interval_end"]),
                                       False, None, clear, provenance)
                break
            if selected is None:
                try:
                    clear = _clear_sky_dni(site, timestamp)
                    selected = SelectedDni(clear, "pvlib_clear_sky", None, None, None,
                                           "clear_sky_fallback", timestamp, None, None,
                                           True, "open_meteo_out_of_coverage", clear)
                except ValueError:
                    selected = SelectedDni(None, "unknown", None, None, None,
                                           "clear_sky_fallback", None, None, None,
                                           True, "clear_sky_error", None)
            candidates.append(selected)
            if selected.snapshot_id:
                snapshot_ids.add(selected.snapshot_id)
    run_id = str(uuid4())
    expires = current + timedelta(minutes=30)
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO nowcast_run(run_id,site_key,created_at,input_snapshot_ids_json,algorithm_version,status,expires_at)
            VALUES (:id,:site,:created,:snapshots,:algorithm,'ready',:expires)"""),
            {"id": run_id, "site": f"{site.latitude_deg:.6f},{site.longitude_deg:.6f}", "created": utc_z(current),
             "snapshots": json.dumps(sorted(snapshot_ids)), "algorithm": ALGORITHM_VERSION, "expires": utc_z(expires)})
        for timestamp, item in zip(timestamps, candidates, strict=True):
            conn.execute(text("""INSERT INTO nowcast_point(run_id,valid_time,clear_sky_dni_W_m2,clear_sky_index,final_dni_W_m2,prediction_method,contributors_json,fallback_reason)
                VALUES (:run,:time,:clear,:index,:final,:method,:contributors,:fallback_reason)"""),
                {"run": run_id, "time": utc_z(timestamp), "clear": item.clear_sky_dni, "index": (item.value / item.clear_sky_dni) if item.value is not None and item.clear_sky_dni not in (None, 0) else None,
                 "final": item.value, "method": item.method,
                 "contributors": json.dumps([{ "snapshot_id": item.snapshot_id, "provider": item.provider, "role": "forecast", "weight": 1.0}]) if item.snapshot_id else "[]",
                 "fallback_reason": item.fallback_reason})
    return run_id, candidates
