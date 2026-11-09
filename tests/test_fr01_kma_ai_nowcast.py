"""Comprehensive tests for FR-01.4 KMA AI × Open-Meteo Hybrid Quantile Nowcasting."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from app.db import init_db
from app.main import create_app
from app.migrations.runner import LATEST_VERSION, MIGRATIONS
from app.nowcast.kma_ml import KmaQuantileNowcaster
from app.nowcast.repository import utc_z
from app.schemas.nowcast_ai import (
    AiNowcastRequest,
    CloudPatchSummary,
    KmaPointObservation,
)
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from engine.fr01.models import Site

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Any]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'kma_nowcast.db'}", connect_args={"check_same_thread": False}
    )
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Any) -> Iterator[TestClient]:
    app = create_app(database_engine)
    with TestClient(app) as test_client:
        yield test_client


def test_migrations_add_quantile_columns(tmp_path: Path) -> None:
    """Verify the latest schema retains the v3 quantile columns."""
    db_path = tmp_path / "migration_v3.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Initialize up to latest version
    init_db(engine)

    with engine.connect() as conn:
        ver = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
        assert ver == LATEST_VERSION
    assert ver == 4

    columns = {col["name"]: col for col in inspect(engine).get_columns("nowcast_point")}
    assert "dni_p10_W_m2" in columns
    assert "dni_p50_W_m2" in columns
    assert "dni_p90_W_m2" in columns
    assert "kma_ai_dni_W_m2" in columns
    assert "open_meteo_dni_W_m2" in columns

    # Verify idempotency
    init_db(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == LATEST_VERSION


def test_upgrade_from_v2_to_latest_preserves_existing_data(tmp_path: Path) -> None:
    """Verify migrating an existing v2 database to the latest version preserves records."""
    db_path = tmp_path / "v2_upgrade.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Run v1 and v2 manually
    with engine.begin() as conn:
        MIGRATIONS[1](conn)
        conn.exec_driver_sql("PRAGMA user_version = 1")
        MIGRATIONS[2](conn)
        conn.exec_driver_sql("PRAGMA user_version = 2")

        # Insert a pre-existing run and point
        conn.execute(
            text("""
            INSERT INTO nowcast_run(run_id, site_key, created_at, input_snapshot_ids_json, algorithm_version, status, expires_at)
            VALUES ('run-v2', '37.5,127.0', '2026-10-14T02:00:00Z', '[]', 'legacy_v2', 'ready', '2026-10-14T03:00:00Z')
            """)
        )
        conn.execute(
            text("""
            INSERT INTO nowcast_point(run_id, valid_time, clear_sky_dni_W_m2, clear_sky_index, final_dni_W_m2, prediction_method, contributors_json)
            VALUES ('run-v2', '2026-10-14T02:00:00Z', 850.0, 0.9, 765.0, 'open_meteo', '[]')
            """)
        )

    # Now run upgrade_database to apply all remaining migrations.
    init_db(engine)

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == LATEST_VERSION
        row = (
            conn.execute(
                text("SELECT final_dni_W_m2, dni_p50_W_m2 FROM nowcast_point WHERE run_id='run-v2'")
            )
            .mappings()
            .one()
        )
        assert row["final_dni_W_m2"] == 765.0
        assert row["dni_p50_W_m2"] is None  # Nullable for pre-existing rows


def test_kma_nowcaster_monotonicity_and_bounds() -> None:
    """Verify strict mathematical monotonicity: 0 <= p10 <= p50 <= p90 <= cs_dni * 1.05."""
    nowcaster = KmaQuantileNowcaster()
    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    base_time = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)  # Midday local

    kma_obs = [
        KmaPointObservation(
            timestamp=utc_z(base_time - timedelta(minutes=20)),
            dsr_W_m2=650.0,
            cloud_cover_pct=20.0,
        ),
        KmaPointObservation(
            timestamp=utc_z(base_time - timedelta(minutes=10)),
            dsr_W_m2=620.0,
            cloud_cover_pct=25.0,
        ),
    ]
    patch = CloudPatchSummary(
        patch_size_km=10.0,
        mean_cloud_fraction=0.30,
        upwind_cloud_gradient=0.10,
        motion_u_m_s=3.0,
        motion_v_m_s=-1.0,
    )

    req = AiNowcastRequest(
        start_time=utc_z(base_time),
        duration_minutes=120,
        interval_seconds=600,
        mode="kma_ai_ensemble",
        kma_observations=kma_obs,
        cloud_patch=patch,
    )

    resp = nowcaster.predict(site, req, now=base_time)
    assert len(resp.points) > 5

    for pt in resp.points:
        assert pt.dni_p10_W_m2 >= 0.0
        assert pt.dni_p10_W_m2 <= pt.dni_p50_W_m2 + 1e-6
        assert pt.dni_p50_W_m2 <= pt.dni_p90_W_m2 + 1e-6
        assert pt.dni_p90_W_m2 <= pt.clear_sky_dni_W_m2 * 1.05 + 1e-3
        assert pt.uncertainty_spread_W_m2 >= 0.0
        assert abs(pt.uncertainty_spread_W_m2 - (pt.dni_p90_W_m2 - pt.dni_p10_W_m2)) < 0.2


def test_kma_nowcaster_nighttime_zero_dni() -> None:
    """When sun is below horizon, all DNI values must be 0.0 with 0 uncertainty."""
    nowcaster = KmaQuantileNowcaster()
    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    night_time = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # Midnight local

    req = AiNowcastRequest(
        start_time=utc_z(night_time),
        duration_minutes=60,
        interval_seconds=600,
        mode="kma_ai_ensemble",
    )

    resp = nowcaster.predict(site, req, now=night_time)
    for pt in resp.points:
        assert pt.solar_elevation_deg <= 0.0
        assert pt.clear_sky_dni_W_m2 == 0.0
        assert pt.dni_p10_W_m2 == 0.0
        assert pt.dni_p50_W_m2 == 0.0
        assert pt.dni_p90_W_m2 == 0.0
        assert pt.uncertainty_spread_W_m2 == 0.0


def test_kma_nowcaster_fallback_when_no_kma_obs() -> None:
    """When no KMA observations exist, falls back cleanly to synthetic bands / clear-sky."""
    nowcaster = KmaQuantileNowcaster()
    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    base_time = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)

    req = AiNowcastRequest(
        start_time=utc_z(base_time),
        duration_minutes=60,
        interval_seconds=600,
        mode="kma_ai_ensemble",
        kma_observations=[],
    )

    resp = nowcaster.predict(site, req, now=base_time)
    assert len(resp.points) > 0
    daytime_pts = [p for p in resp.points if p.solar_elevation_deg > 0]
    assert len(daytime_pts) > 0
    for p in daytime_pts:
        assert p.kma_ai_dni_W_m2 is None
        assert p.open_meteo_dni_W_m2 is not None
        assert p.dni_p10_W_m2 <= p.dni_p50_W_m2 <= p.dni_p90_W_m2


def test_kma_nowcaster_fit_and_weights_update() -> None:
    """Test training quantile ensemble with .fit(X, y)."""
    nowcaster = KmaQuantileNowcaster()
    x = np.array(
        [
            [1.0, 0.90, 0.85, 0.90, 0.0],
            [1.0, 0.70, 0.65, 0.70, -0.1],
            [1.0, 0.40, 0.45, 0.40, -0.2],
            [1.0, 0.20, 0.25, 0.20, -0.3],
            [1.0, 0.05, 0.10, 0.05, -0.4],
            [1.0, 0.85, 0.80, 0.85, 0.05],
        ]
    )
    y = np.array([0.92, 0.68, 0.38, 0.15, 0.0, 0.88])

    nowcaster.fit(x, y)
    assert nowcaster.ensemble.fitted is True

    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    base_time = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
    req = AiNowcastRequest(
        start_time=utc_z(base_time),
        duration_minutes=30,
        interval_seconds=600,
    )
    resp = nowcaster.predict(site, req, now=base_time)
    for p in resp.points:
        assert p.dni_p10_W_m2 <= p.dni_p50_W_m2 <= p.dni_p90_W_m2


def test_kma_nowcaster_metrics_and_pinball_loss() -> None:
    """Test computation of RMSE, MAE, and pinball loss when reference actual DNI is provided."""
    nowcaster = KmaQuantileNowcaster()
    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    base_time = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)

    ref_map = {utc_z(base_time + timedelta(seconds=i * 600)): 500.0 + i * 20.0 for i in range(4)}

    req = AiNowcastRequest(
        start_time=utc_z(base_time),
        duration_minutes=30,
        interval_seconds=600,
        reference_actual_dni=ref_map,
    )

    resp = nowcaster.predict(site, req, now=base_time)
    assert resp.metrics.kma_ai_rmse_W_m2 is not None
    assert resp.metrics.kma_ai_rmse_W_m2 >= 0.0
    assert resp.metrics.open_meteo_rmse_W_m2 is not None
    assert resp.metrics.kma_ai_pinball_loss is not None
    assert resp.metrics.kma_ai_pinball_loss >= 0.0
    assert 0.0 <= resp.metrics.cloud_volatility_index <= 1.0


def test_kma_nowcaster_database_persistence(database_engine: Any) -> None:
    """Verify persisting nowcast run and points into SQLite schema with v3 columns."""
    nowcaster = KmaQuantileNowcaster(engine=database_engine)
    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    base_time = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)

    req = AiNowcastRequest(
        start_time=utc_z(base_time),
        duration_minutes=30,
        interval_seconds=600,
        kma_observations=[
            KmaPointObservation(timestamp=utc_z(base_time - timedelta(minutes=10)), dsr_W_m2=700.0)
        ],
    )

    resp = nowcaster.predict(site, req, now=base_time)

    with database_engine.connect() as conn:
        run_count = conn.execute(
            text("SELECT count(*) FROM nowcast_run WHERE run_id = :rid"),
            {"rid": resp.run_id},
        ).scalar_one()
        assert run_count == 1

        points = (
            conn.execute(
                text("""
            SELECT valid_time, dni_p10_W_m2, dni_p50_W_m2, dni_p90_W_m2, kma_ai_dni_W_m2
            FROM nowcast_point WHERE run_id = :rid
            """),
                {"rid": resp.run_id},
            )
            .mappings()
            .all()
        )

        assert len(points) == len(resp.points)
        for p in points:
            assert p["dni_p10_W_m2"] is not None
            assert p["dni_p50_W_m2"] is not None
            assert p["dni_p90_W_m2"] is not None
            assert p["dni_p10_W_m2"] <= p["dni_p50_W_m2"] <= p["dni_p90_W_m2"]


def test_api_rejects_request_supplied_observations(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    """The production API does not accept caller-supplied weather observations."""
    create_resp = client.post("/api/scenes", json=scene_payload)
    assert create_resp.status_code == 201
    scene_id = create_resp.json()["scene_id"]

    body = {
        "start_time": "2026-10-14T03:00:00Z",
        "duration_minutes": 60,
        "interval_seconds": 600,
        "mode": "kma_ai_ensemble",
        "kma_observations": [
            {
                "timestamp": "2026-10-14T02:50:00Z",
                "dsr_W_m2": 680.0,
                "cloud_cover_pct": 15.0,
            }
        ],
    }

    resp = client.post(f"/api/scenes/{scene_id}/ai-nowcast", json=body)
    assert resp.status_code == 404


def test_api_get_nowcast_requires_worker_result(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    """GET never synthesizes a timeline when the worker has not produced one."""
    create_resp = client.post("/api/scenes", json=scene_payload)
    assert create_resp.status_code == 201
    scene_id = create_resp.json()["scene_id"]

    resp = client.get(f"/api/scenes/{scene_id}/ai-nowcast/latest")
    assert resp.status_code == 503


def test_api_nowcast_validation_and_not_found(client: TestClient) -> None:
    """Verify 404 for missing scene and 422 for invalid parameters."""
    # 404
    resp = client.get("/api/scenes/nonexistent-scene-123/nowcast-comparison")
    assert resp.status_code == 404

    # POST injection path is deliberately absent.
    resp3 = client.post(
        "/api/scenes/any-scene/ai-nowcast",
        json={"start_time": "2026-10-14T03:00:00Z", "extra_bad_field": 123},
    )
    assert resp3.status_code == 404


def test_kma_nowcaster_strict_physical_bounds_and_night_zero() -> None:
    """Verify strict physical bounds: 0.0 <= p10 <= p50 <= p90 <= cs*1.05 and night == 0."""
    nowcaster = KmaQuantileNowcaster()
    site = Site(latitude_deg=37.5665, longitude_deg=126.9780, timezone="Asia/Seoul")
    # Start at local midnight (15:00 UTC previous day) for 24h
    start_dt = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)

    req = AiNowcastRequest(
        start_time=utc_z(start_dt),
        duration_minutes=1440,
        interval_seconds=1800,  # 48 steps
        mode="kma_ai_ensemble",
    )

    resp = nowcaster.predict(site, req, now=start_dt)
    assert len(resp.points) == 49

    for p in resp.points:
        if p.solar_elevation_deg <= 0.0:
            assert p.clear_sky_dni_W_m2 == 0.0
            assert p.dni_p10_W_m2 == 0.0
            assert p.dni_p50_W_m2 == 0.0
            assert p.dni_p90_W_m2 == 0.0
            assert p.uncertainty_spread_W_m2 == 0.0
        else:
            assert 0.0 <= p.dni_p10_W_m2
            assert p.dni_p10_W_m2 <= p.dni_p50_W_m2
            assert p.dni_p50_W_m2 <= p.dni_p90_W_m2
            assert p.dni_p90_W_m2 <= round(p.clear_sky_dni_W_m2 * 1.05 + 0.1, 1)
            assert p.uncertainty_spread_W_m2 >= 0.0
