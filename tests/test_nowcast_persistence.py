from datetime import UTC, datetime, timedelta

import pytest
from app.db import init_db
from app.migrations.runner import LATEST_VERSION
from app.nowcast.adapters import MockSnapshotAdapter, RawPoint, RawSnapshot
from app.nowcast.repository import SnapshotRepository
from sqlalchemy import inspect, text
from sqlmodel import create_engine


def make_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nowcast.db'}")
    init_db(engine)
    return engine


def raw(issued_at, value=0.0):
    return RawSnapshot(
        provider="mock",
        product="fixture",
        payload=f"payload-{value}".encode(),
        issued_at=issued_at,
        points=(
            RawPoint(valid_time=issued_at, latitude_deg=37.5, longitude_deg=127.0, dni_W_m2=value),
        ),
    )


def test_migration_is_idempotent_and_preserves_existing_scene_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scenes (scene_id TEXT PRIMARY KEY, revision INTEGER, scene_json TEXT)"
            )
        )
        conn.execute(text("INSERT INTO scenes VALUES ('scene-1', 1, '{}')"))
    init_db(engine)
    init_db(engine)
    assert {
        "scenes",
        "weather_snapshot",
        "weather_point",
        "nowcast_run",
        "nowcast_point",
        "worker_lease",
    } <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert conn.execute(text("SELECT scene_id FROM scenes")).scalar_one() == "scene-1"
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == LATEST_VERSION


def test_mock_adapter_snapshot_is_deduplicated_and_stores_utc_z(tmp_path):
    engine = make_engine(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fixture = raw(now, 0)
    adapter = MockSnapshotAdapter([fixture, fixture])
    repository = SnapshotRepository(engine, id_factory=lambda: "snapshot-1")
    first = repository.store(adapter.fetch(now), now)
    second = repository.store(adapter.fetch(now + timedelta(minutes=1)), now + timedelta(minutes=1))
    assert first == second == "snapshot-1"
    with engine.connect() as conn:
        snapshot = conn.execute(text("SELECT fetched_at FROM weather_snapshot")).scalar_one()
        assert snapshot.endswith("Z")
        assert conn.execute(text("SELECT count(*) FROM weather_point")).scalar_one() == 1


def test_invalid_mean_snapshot_rolls_back_completely(tmp_path):
    engine = make_engine(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    invalid = RawSnapshot("mock", "bad", b"bad", (RawPoint(now, 1, 2, temporal_meaning="mean"),))
    with pytest.raises(ValueError):
        SnapshotRepository(engine).store(invalid, now)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM weather_snapshot")).scalar_one() == 0


def test_retention_keeps_snapshot_referenced_by_unexpired_run(tmp_path):
    engine = make_engine(tmp_path)
    repository = SnapshotRepository(engine, id_factory=iter(["protected", "disposable"]).__next__)
    now = datetime(2026, 2, 1, tzinfo=UTC)
    old = now - timedelta(days=15)
    protected = repository.store(raw(old, 1), old)
    disposable = repository.store(raw(old, 2), old)
    repository.add_run("current-run", [protected], old, now + timedelta(days=1))
    assert repository.prune(now) == {"nowcast_runs": 0, "weather_snapshots": 1}
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT snapshot_id FROM weather_snapshot")).scalar_one() == protected
        )
        assert conn.execute(text("SELECT count(*) FROM weather_point")).scalar_one() == 1
    assert disposable != protected


def test_worker_lease_allows_owner_renewal_and_expired_takeover(tmp_path):
    repository = SnapshotRepository(make_engine(tmp_path))
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert repository.acquire_lease("nowcast", "worker-a", now)
    assert not repository.acquire_lease("nowcast", "worker-b", now + timedelta(seconds=10))
    assert repository.acquire_lease("nowcast", "worker-a", now + timedelta(seconds=10))
    assert repository.acquire_lease("nowcast", "worker-b", now + timedelta(seconds=101))
