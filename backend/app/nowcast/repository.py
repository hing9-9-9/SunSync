"""SQLite repository for immutable source snapshots, runs, retention and leases."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine, text

from app.nowcast.adapters import RawSnapshot


def utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be UTC-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SnapshotRepository:
    def __init__(self, engine: Engine, id_factory=None) -> None:  # type: ignore[no-untyped-def]
        self.engine = engine
        self.id_factory = id_factory or (lambda: str(uuid4()))

    def store(
        self, raw: RawSnapshot, fetched_at: datetime, raw_payload_path: str | None = None
    ) -> str:
        fetched = utc_z(fetched_at)
        payload_hash = hashlib.sha256(raw.payload).hexdigest()
        issued = utc_z(raw.issued_at) if raw.issued_at else None
        # Repeated provider payloads must not duplicate immutable points.
        with self.engine.begin() as conn:
            existing = conn.execute(
                text("""SELECT snapshot_id FROM weather_snapshot
                    WHERE provider=:provider AND product=:product
                      AND COALESCE(issued_at, '')=COALESCE(:issued_at, '')
                      AND raw_payload_hash=:payload_hash"""),
                {
                    "provider": raw.provider,
                    "product": raw.product,
                    "issued_at": issued,
                    "payload_hash": payload_hash,
                },
            ).scalar_one_or_none()
            if existing:
                return str(existing)
            snapshot_id = self.id_factory()
            conn.execute(
                text("""INSERT INTO weather_snapshot
                (snapshot_id,provider,product,model,observed_at,issued_at,fetched_at,raw_payload_hash,raw_payload_path,quality_status)
                VALUES (:snapshot_id,:provider,:product,:model,:observed_at,:issued_at,:fetched_at,:payload_hash,:raw_payload_path,:quality_status)"""),
                {
                    "snapshot_id": snapshot_id,
                    "provider": raw.provider,
                    "product": raw.product,
                    "model": raw.model,
                    "observed_at": utc_z(raw.observed_at) if raw.observed_at else None,
                    "issued_at": issued,
                    "fetched_at": fetched,
                    "payload_hash": payload_hash,
                    "raw_payload_path": raw_payload_path,
                    "quality_status": raw.quality_status,
                },
            )
            if raw.provenance:
                conn.execute(
                    text(
                        "UPDATE weather_snapshot SET provenance_json=:provenance WHERE snapshot_id=:snapshot_id"
                    ),
                    {
                        "snapshot_id": snapshot_id,
                        "provenance": json.dumps(raw.provenance, sort_keys=True),
                    },
                )
            for point in raw.points:
                meaning = point.temporal_meaning
                if meaning not in {"instant", "mean"}:
                    raise ValueError("temporal_meaning must be instant or mean")
                if meaning == "mean" and (
                    point.interval_start is None or point.interval_end is None
                ):
                    raise ValueError("mean point requires interval_start and interval_end")
                conn.execute(
                    text("""INSERT INTO weather_point
                    (snapshot_id,valid_time,latitude_deg,longitude_deg,grid_resolution_km,dni_W_m2,ghi_W_m2,direct_horizontal_W_m2,cloud_cover_percent,temporal_meaning,interval_start,interval_end)
                    VALUES (:snapshot_id,:valid_time,:latitude_deg,:longitude_deg,:grid_resolution_km,:dni,:ghi,:bhi,:cloud,:meaning,:interval_start,:interval_end)"""),
                    {
                        "snapshot_id": snapshot_id,
                        "valid_time": utc_z(point.valid_time),
                        "latitude_deg": point.latitude_deg,
                        "longitude_deg": point.longitude_deg,
                        "grid_resolution_km": point.grid_resolution_km,
                        "dni": point.dni_W_m2,
                        "ghi": point.ghi_W_m2,
                        "bhi": point.direct_horizontal_W_m2,
                        "cloud": point.cloud_cover_percent,
                        "meaning": meaning,
                        "interval_start": utc_z(point.interval_start)
                        if point.interval_start
                        else None,
                        "interval_end": utc_z(point.interval_end) if point.interval_end else None,
                    },
                )
            return snapshot_id

    def acquire_lease(
        self, lease_name: str, owner_id: str, now: datetime, ttl_seconds: int = 90
    ) -> bool:
        now_text, expires = utc_z(now), utc_z(now + timedelta(seconds=ttl_seconds))
        with self.engine.begin() as conn:
            result = conn.execute(
                text("""INSERT INTO worker_lease(lease_name, owner_id, expires_at)
                VALUES (:name,:owner,:expires)
                ON CONFLICT(lease_name) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at
                WHERE worker_lease.expires_at <= :now OR worker_lease.owner_id = :owner"""),
                {"name": lease_name, "owner": owner_id, "expires": expires, "now": now_text},
            )
            return result.rowcount == 1

    def add_run(
        self, run_id: str, snapshot_ids: list[str], created_at: datetime, expires_at: datetime
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO nowcast_run
                (run_id,site_key,created_at,input_snapshot_ids_json,algorithm_version,status,expires_at)
                VALUES (:run_id,'test',:created_at,:ids,'test','ready',:expires_at)"""),
                {
                    "run_id": run_id,
                    "created_at": utc_z(created_at),
                    "ids": json.dumps(snapshot_ids),
                    "expires_at": utc_z(expires_at),
                },
            )

    def prune(self, now: datetime) -> dict[str, int]:
        cutoff_snapshots = utc_z(now - timedelta(days=14))
        cutoff_runs = utc_z(now - timedelta(days=30))
        with self.engine.begin() as conn:
            expired_runs = (
                conn.execute(
                    text("SELECT run_id FROM nowcast_run WHERE expires_at < :cutoff"),
                    {"cutoff": cutoff_runs},
                )
                .scalars()
                .all()
            )
            if expired_runs:
                placeholders = ",".join(f":run_{i}" for i in range(len(expired_runs)))
                parameters = {f"run_{i}": run_id for i, run_id in enumerate(expired_runs)}
                conn.execute(
                    text(f"DELETE FROM nowcast_point WHERE run_id IN ({placeholders})"), parameters
                )
                runs = conn.execute(
                    text(f"DELETE FROM nowcast_run WHERE run_id IN ({placeholders})"), parameters
                ).rowcount
            else:
                runs = 0
            # JSON1 may not be compiled in every SQLite build; protect references
            # using the canonical JSON string with a quoted id match.
            candidates = (
                conn.execute(
                    text("SELECT snapshot_id FROM weather_snapshot WHERE fetched_at < :cutoff"),
                    {"cutoff": cutoff_snapshots},
                )
                .scalars()
                .all()
            )
            deleted = 0
            for snapshot_id in candidates:
                reference = conn.execute(
                    text(
                        "SELECT 1 FROM nowcast_run WHERE input_snapshot_ids_json LIKE :needle LIMIT 1"
                    ),
                    {"needle": f'%"{snapshot_id}"%'},
                ).scalar_one_or_none()
                if reference is None:
                    conn.execute(
                        text("DELETE FROM weather_point WHERE snapshot_id=:id"), {"id": snapshot_id}
                    )
                    deleted += conn.execute(
                        text("DELETE FROM weather_snapshot WHERE snapshot_id=:id"),
                        {"id": snapshot_id},
                    ).rowcount
            return {"nowcast_runs": runs, "weather_snapshots": deleted}
