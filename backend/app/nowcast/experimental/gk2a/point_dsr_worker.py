"""FR-01.4 Implementation Step B — standalone GK2A DSR point-snapshot worker."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import create_engine

from app.db import DATABASE_URL, init_db
from app.nowcast.experimental.gk2a.point_dsr_adapter import Gk2aDsrAdapter, Gk2aDsrError
from app.nowcast.repository import SnapshotRepository
from app.nowcast.sites import load_unique_sites

LEASE_NAME = "gk2a_dsr_snapshot"


def run_once(engine: Engine, *, api_key: str | None, now: datetime | None = None, owner_id: str | None = None) -> dict[str, int]:
    """Collect one 24-hour DSR window per unique site without exposing the key."""

    current_input = now or datetime.now(UTC)
    if current_input.tzinfo is None or current_input.utcoffset() is None:
        raise ValueError("now must be UTC-aware")
    current = current_input.astimezone(UTC)
    if not api_key:
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0, "blocked": 1}
    repository = SnapshotRepository(engine)
    owner = owner_id or str(uuid4())
    if not repository.acquire_lease(LEASE_NAME, owner, current):
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0, "blocked": 0}
    sites = load_unique_sites(engine)
    stored = failed = 0
    for site in sites:
        try:
            repository.store(Gk2aDsrAdapter(site, api_key).fetch_recent_24_hours(current), current)
            stored += 1
        except (Gk2aDsrError, ValueError):
            failed += 1
    return {"sites": len(sites), "stored": stored, "failed": failed, "lease_acquired": 1, "blocked": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--owner-id", default=None)
    args = parser.parse_args()
    engine = create_engine(args.database_url, connect_args={"check_same_thread": False})
    init_db(engine)
    result = run_once(engine, api_key=os.environ.get("KMA_API_KEY"), owner_id=args.owner_id)
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 2 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
