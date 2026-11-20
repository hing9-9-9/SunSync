"""Scheduled KMA GK2A snapshot worker; never invoked from request handling."""

from __future__ import annotations

import argparse
import os
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import create_engine

from app.config import load_local_environment
from app.db import DATABASE_URL, init_db
from app.nowcast.kma_adapter import KmaCollection, KmaGk2aAdapter, KmaGk2aError
from app.nowcast.kma_dataset import write_kma_collection
from app.nowcast.repository import SnapshotRepository
from app.nowcast.sites import load_unique_sites
from app.nowcast.worker_report import exit_code, report_line
from engine.fr01.models import Site

LEASE_NAME = "kma_gk2a_snapshot"


def _reason(error: Exception) -> str:
    if isinstance(error, KmaGk2aError):
        return error.reason
    return "kma_file_error" if isinstance(error, OSError) else "kma_value_error"


def run_once(
    engine: Engine,
    *,
    api_key: str,
    output_root: Path,
    now: datetime | None = None,
    owner_id: str | None = None,
    adapter_factory: Callable[[Site, str], KmaGk2aAdapter] | None = None,
    failures: Counter[str] | None = None,
) -> dict[str, int]:
    """Collect recent KMA data for all persisted sites and store immutable snapshots.

    ``failures`` (when given) is filled with how often each failure reason happened.
    """

    current_input = now or datetime.now(UTC)
    if current_input.tzinfo is None or current_input.utcoffset() is None:
        raise ValueError("now must be UTC-aware")
    if not api_key.strip():
        raise ValueError("KMA_API_KEY is not configured")
    current = current_input.astimezone(UTC)
    owner = owner_id or str(uuid4())
    repository = SnapshotRepository(engine)
    if not repository.acquire_lease(LEASE_NAME, owner, current, ttl_seconds=300):
        return {"sites": 0, "stored": 0, "failed": 0, "lease_acquired": 0}

    sites = load_unique_sites(engine)
    stored = failed = 0
    factory = adapter_factory or (lambda site, key: KmaGk2aAdapter(site, key))
    for site in sites:
        try:
            collection: KmaCollection = factory(site, api_key).fetch_recent(current)
            paths = write_kma_collection(collection, output_root)
            repository.store(
                collection.snapshot,
                current,
                str(paths.manifest),
                site_key=f"{site.latitude_deg:.6f},{site.longitude_deg:.6f}",
            )
            stored += 1
        except (KmaGk2aError, OSError, ValueError) as error:
            failed += 1
            if failures is not None:
                failures[_reason(error)] += 1
    return {
        "sites": len(sites),
        "stored": stored,
        "failed": failed,
        "lease_acquired": 1,
    }


def main() -> int:
    load_local_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--owner-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("data/fr01"))
    args = parser.parse_args()
    api_key = os.getenv("KMA_API_KEY", "")
    if not api_key.strip():
        parser.error("KMA_API_KEY is missing; set it in the repository .env")
    engine = create_engine(args.database_url, connect_args={"check_same_thread": False})
    init_db(engine)
    failures: Counter[str] = Counter()
    result = run_once(
        engine,
        api_key=api_key,
        output_root=args.output_dir,
        owner_id=args.owner_id,
        failures=failures,
    )
    print(report_line(result, failures))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
