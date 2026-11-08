"""Build frozen-model FR-01 operational nowcasts from durable source snapshots."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, text
from sqlmodel import create_engine

from app.db import DATABASE_URL, init_db
from app.nowcast.kma_operational import FrozenOperationalNowcaster, floor_to_ten_minutes
from app.nowcast.repository import SnapshotRepository
from app.schemas.scene import Scene
from engine.fr01.models import Site

LEASE_NAME = "fr01_operational_ai_nowcast"


def run_once(
    engine: Engine,
    *,
    now: datetime | None = None,
    owner_id: str | None = None,
    rollout_mode: str = "shadow",
) -> dict[str, int]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    owner = owner_id or str(uuid4())
    lease_repository = SnapshotRepository(engine)
    if not lease_repository.acquire_lease(LEASE_NAME, owner, current, ttl_seconds=540):
        return {"scenes": 0, "stored": 0, "failed": 0, "lease_acquired": 0}

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT scene_id, scene_json FROM scenes ORDER BY scene_id")
        ).all()
    nowcaster = FrozenOperationalNowcaster(engine, rollout_mode=rollout_mode)
    origin = floor_to_ten_minutes(current)
    stored = failed = valid_scenes = 0
    for scene_id, payload in rows:
        try:
            scene = Scene.model_validate_json(payload)
            site = Site(
                scene.site.latitude_deg,
                scene.site.longitude_deg,
                scene.site.timezone,
            )
        except ValueError:
            failed += 1
            continue
        valid_scenes += 1
        try:
            nowcaster.predict_and_persist(
                scene_id=str(scene_id),
                site=site,
                prediction_origin=origin,
                created_at=current,
            )
            stored += 1
        except (OSError, RuntimeError, ValueError):
            failed += 1
    return {
        "scenes": valid_scenes,
        "stored": stored,
        "failed": failed,
        "lease_acquired": 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DATABASE_URL)
    parser.add_argument("--owner-id", default=None)
    parser.add_argument(
        "--rollout-mode",
        choices=("shadow", "active"),
        default=os.getenv("FR01_AI_ROLLOUT_MODE", "shadow"),
    )
    args = parser.parse_args()
    engine = create_engine(args.database_url, connect_args={"check_same_thread": False})
    init_db(engine)
    result = run_once(
        engine,
        owner_id=args.owner_id,
        rollout_mode=args.rollout_mode,
    )
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
