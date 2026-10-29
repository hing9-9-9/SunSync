"""Standalone Step C GK2A SWRAD-to-DNI snapshot worker."""
from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import create_engine

from app.db import DATABASE_URL, init_db
from app.nowcast.adapters import Gk2aDsrError
from app.nowcast.experimental.gk2a.swrad_dni_adapter import Gk2aSwradDniAdapter
from app.nowcast.repository import SnapshotRepository
from app.nowcast.sites import load_unique_sites

LEASE_NAME = "gk2a_swrad_dni_snapshot"


def run_once(engine: Engine, *, api_key: str | None, now: datetime | None = None, owner_id: str | None = None) -> dict[str, int]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if not api_key:
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0, "blocked": 1}
    repository = SnapshotRepository(engine)
    if not repository.acquire_lease(LEASE_NAME, owner_id or str(uuid4()), current):
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0, "blocked": 0}
    sites, stored, failed = load_unique_sites(engine), 0, 0
    for site in sites:
        try:
            repository.store(Gk2aSwradDniAdapter(site, api_key).fetch(current), current)
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
