"""FR-01.4 Step A — Open-Meteo DNI snapshot worker only.

Run with ``python -m app.nowcast.open_meteo_worker``. It is intentionally not
attached to FastAPI's lifespan: scheduling and process supervision belong to
deployment.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import create_engine

from app.db import DATABASE_URL, init_db
from app.nowcast.adapters import OpenMeteoDniAdapter, OpenMeteoSnapshotError
from app.nowcast.repository import SnapshotRepository
from app.nowcast.sites import load_unique_sites

LEASE_NAME = "open_meteo_dni_snapshot"


def run_once(
    engine: Engine, *, now: datetime | None = None, owner_id: str | None = None
) -> dict[str, int]:
    """Collect Open-Meteo DNI for every scene site; provider errors preserve history."""

    current_input = now or datetime.now(UTC)
    if current_input.tzinfo is None or current_input.utcoffset() is None:
        raise ValueError("now must be UTC-aware")
    current = current_input.astimezone(UTC)
    owner = owner_id or str(uuid4())
    repository = SnapshotRepository(engine)
    if not repository.acquire_lease(LEASE_NAME, owner, current):
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0}
    sites = load_unique_sites(engine)
    stored = failed = 0
    for site in sites:
        try:
            snapshot = OpenMeteoDniAdapter(site).fetch(current)
            repository.store(snapshot, current)
            stored += 1
        except (OpenMeteoSnapshotError, ValueError):
            # No invalid snapshot is written.  The last successful one remains.
            failed += 1
    return {"sites": len(sites), "stored": stored, "failed": failed, "lease_acquired": 1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--owner-id", default=None)
    args = parser.parse_args()
    engine = create_engine(args.database_url, connect_args={"check_same_thread": False})
    init_db(engine)
    result = run_once(engine, owner_id=args.owner_id)
    # Deliberately report aggregate counts only: no provider response or secret.
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
