"""FR-01.4 Step A — Open-Meteo DNI snapshot worker only.

Run with ``python -m app.nowcast.open_meteo_worker``. After each collection it also deletes the
snapshots and runs that ``SnapshotRepository.prune`` considers expired. It is intentionally not
attached to FastAPI's lifespan: scheduling and process supervision belong to
deployment.
"""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import create_engine

from app.db import DATABASE_URL, init_db
from app.nowcast.adapters import OpenMeteoDniAdapter, OpenMeteoSnapshotError
from app.nowcast.repository import SnapshotRepository
from app.nowcast.sites import load_unique_sites
from app.nowcast.worker_report import exit_code, report_line
from engine.fr01.models import Site

LEASE_NAME = "open_meteo_dni_snapshot"
# A short network failure is tried once more; a bad response is not (it would answer the same).
RETRYABLE_REASONS = {"open_meteo_timeout", "open_meteo_transport_error"}
RETRY_DELAY_SECONDS = 2.0
KEEP_DAYS_ENV = "SUNSYNC_SNAPSHOT_KEEP_DAYS"
DEFAULT_KEEP_DAYS = 14
MAX_KEEP_DAYS = 3650


def _fetch(site: Site, current: datetime, retry_delay_seconds: float):  # type: ignore[no-untyped-def]
    try:
        return OpenMeteoDniAdapter(site).fetch(current)
    except OpenMeteoSnapshotError as error:
        if str(error) not in RETRYABLE_REASONS:
            raise
    time.sleep(retry_delay_seconds)
    return OpenMeteoDniAdapter(site).fetch(current)


def _keep_days() -> int | None:
    """Days of snapshots to keep; None when the setting is not a whole number of days."""
    raw = os.environ.get(KEEP_DAYS_ENV, "").strip()
    if not raw:
        return DEFAULT_KEEP_DAYS
    try:
        days = int(raw)
    except ValueError:
        return None
    return days if 1 <= days <= MAX_KEEP_DAYS else None


def _prune(engine: Engine, now: datetime) -> str:
    """Delete old snapshots and runs after a collection; a failure here never fails the run.

    A bad retention setting deletes nothing: the safe direction when the intent is unclear.
    """
    days = _keep_days()
    if days is None:
        return "prune=skipped_bad_setting"
    try:
        pruned = SnapshotRepository(engine).prune(now, snapshot_days=days, run_days=max(30, days))
    except SQLAlchemyError:
        return "prune=failed"
    return f"pruned_snapshots={pruned['weather_snapshots']} pruned_runs={pruned['nowcast_runs']}"


def _reason(error: ValueError) -> str:
    return str(error) if isinstance(error, OpenMeteoSnapshotError) else "open_meteo_value_error"


def run_once(
    engine: Engine,
    *,
    now: datetime | None = None,
    owner_id: str | None = None,
    failures: Counter[str] | None = None,
    retry_delay_seconds: float = RETRY_DELAY_SECONDS,
) -> dict[str, int]:
    """Collect Open-Meteo DNI for every scene site; provider errors preserve history.

    ``failures`` (when given) is filled with how often each failure reason happened.
    """

    current_input = now or datetime.now(UTC)
    if current_input.tzinfo is None or current_input.utcoffset() is None:
        raise ValueError("now must be UTC-aware")
    current = current_input.astimezone(UTC)
    owner = owner_id or str(uuid4())
    repository = SnapshotRepository(engine)
    sites = load_unique_sites(engine)
    # Worst case per site: two tries and the pause between them. The lock must outlive the run.
    lease_seconds = 60 + 15 * len(sites)
    if not repository.acquire_lease(LEASE_NAME, owner, current, ttl_seconds=lease_seconds):
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0}
    stored = failed = 0
    for site in sites:
        try:
            snapshot = _fetch(site, current, retry_delay_seconds)
            repository.store(
                snapshot,
                current,
                site_key=f"{site.latitude_deg:.6f},{site.longitude_deg:.6f}",
            )
            stored += 1
        except (OpenMeteoSnapshotError, ValueError) as error:
            # No invalid snapshot is written.  The last successful one remains.
            failed += 1
            if failures is not None:
                failures[_reason(error)] += 1
    return {"sites": len(sites), "stored": stored, "failed": failed, "lease_acquired": 1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--owner-id", default=None)
    args = parser.parse_args()
    engine = create_engine(args.database_url, connect_args={"check_same_thread": False})
    init_db(engine)
    failures: Counter[str] = Counter()
    result = run_once(engine, owner_id=args.owner_id, failures=failures)
    # Deliberately report aggregate counts and fixed reason words only: no provider response or secret.
    line = report_line(result, failures)
    if result["lease_acquired"]:
        line += " " + _prune(engine, datetime.now(UTC))
    print(line)
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
