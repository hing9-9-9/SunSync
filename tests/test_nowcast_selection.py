"""Step D durable-snapshot selection contracts."""

from datetime import UTC, datetime, timedelta

from app.db import init_db
from app.nowcast.adapters import RawPoint, RawSnapshot
from app.nowcast.repository import SnapshotRepository
from app.nowcast.selection import select_and_record
from sqlmodel import create_engine

from engine.fr01.models import Site

SITE = Site(37.5665, 126.9780, "Asia/Seoul")
NOW = datetime(2026, 10, 28, 3, tzinfo=UTC)


def snapshot(value: float, interval_start: datetime, interval_end: datetime) -> RawSnapshot:
    return RawSnapshot(
        provider="open_meteo",
        product="test",
        payload=f"open-meteo-{value}-{interval_end.isoformat()}".encode(),
        points=(RawPoint(interval_end, SITE.latitude_deg, SITE.longitude_deg, temporal_meaning="mean",
                         dni_W_m2=value, interval_start=interval_start, interval_end=interval_end),),
        provenance={"spatial_provenance": "provider_grid"},
    )


def test_fresh_open_meteo_interval_value_creates_traceable_run(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'selection.db'}")
    init_db(engine)
    repository = SnapshotRepository(engine)
    repository.store(snapshot(400, NOW, NOW + timedelta(hours=1)), NOW)

    result = select_and_record(engine, SITE, (NOW,), NOW)

    assert result is not None
    run_id, values = result
    assert values[0].value == 400
    assert values[0].source == "open_meteo"
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT status FROM nowcast_run WHERE run_id=?", (run_id,)).scalar_one() == "ready"
        assert connection.exec_driver_sql("SELECT final_dni_W_m2 FROM nowcast_point WHERE run_id=?", (run_id,)).scalar_one() == 400


def test_stale_or_out_of_interval_snapshot_uses_per_timestamp_clear_sky_fallback(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
    init_db(engine)
    SnapshotRepository(engine).store(snapshot(500, NOW, NOW + timedelta(hours=1)), NOW - timedelta(hours=3))

    result = select_and_record(engine, SITE, (NOW, NOW + timedelta(hours=2)), NOW)

    assert result is not None
    assert [value.source for value in result[1]] == ["pvlib_clear_sky", "pvlib_clear_sky"]
    assert all(value.fallback_used for value in result[1])
    with engine.connect() as connection:
        reasons = connection.exec_driver_sql(
            "SELECT fallback_reason FROM nowcast_point ORDER BY valid_time"
        ).scalars().all()
    assert reasons == ["open_meteo_out_of_coverage", "open_meteo_out_of_coverage"]
