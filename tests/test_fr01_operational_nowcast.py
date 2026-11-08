from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.db import init_db
from app.main import create_app
from app.models.scene import SceneRecord
from app.nowcast.adapters import RawPoint, RawSnapshot
from app.nowcast.kma_operational import FrozenOperationalNowcaster
from app.nowcast.kma_operational_repository import site_key
from app.nowcast.repository import SnapshotRepository
from app.schemas.scene import Scene
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import text
from sqlmodel import Session, create_engine

from engine.fr01.models import Site

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "fr06_scene_example.json"
ORIGIN = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Any]:
    database = create_engine(
        f"sqlite:///{tmp_path / 'operational.db'}", connect_args={"check_same_thread": False}
    )
    init_db(database)
    yield database
    database.dispose()


def _site_and_scene(engine: Any) -> tuple[Site, str]:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    scene = Scene.model_validate(document)
    with Session(engine) as session:
        session.add(
            SceneRecord(
                scene_id=scene.scene_id,
                revision=scene.revision,
                scene_json=scene.model_dump_json(),
            )
        )
        session.commit()
    return (
        Site(scene.site.latitude_deg, scene.site.longitude_deg, scene.site.timezone),
        scene.scene_id,
    )


def _store_inputs(
    engine: Any,
    site: Site,
    *,
    kma_observed_at: datetime = ORIGIN,
    include_open_meteo: bool = True,
) -> None:
    key = site_key(site.latitude_deg, site.longitude_deg)
    repository = SnapshotRepository(engine, id_factory=iter(["kma", "om"]).__next__)
    repository.store(
        RawSnapshot(
            provider="kma_gk2a",
            product="10min_dsr_cloud_point",
            model="gk2a",
            observed_at=kma_observed_at,
            payload=b"kma",
            points=(
                RawPoint(
                    valid_time=kma_observed_at,
                    latitude_deg=site.latitude_deg,
                    longitude_deg=site.longitude_deg,
                    ghi_W_m2=620.0,
                    cloud_cover_percent=25.0,
                ),
            ),
        ),
        ORIGIN,
        site_key=key,
    )
    if not include_open_meteo:
        return
    repository.store(
        RawSnapshot(
            provider="open_meteo",
            product="hourly_direct_normal_irradiance",
            model="ecmwf_ifs025",
            payload=b"om",
            points=(
                RawPoint(
                    valid_time=ORIGIN + timedelta(hours=1),
                    latitude_deg=site.latitude_deg,
                    longitude_deg=site.longitude_deg,
                    temporal_meaning="mean",
                    interval_start=ORIGIN,
                    interval_end=ORIGIN + timedelta(hours=1),
                    dni_W_m2=500.0,
                ),
                RawPoint(
                    valid_time=ORIGIN + timedelta(hours=2),
                    latitude_deg=site.latitude_deg,
                    longitude_deg=site.longitude_deg,
                    temporal_meaning="mean",
                    interval_start=ORIGIN + timedelta(hours=1),
                    interval_end=ORIGIN + timedelta(hours=2),
                    dni_W_m2=450.0,
                ),
            ),
        ),
        ORIGIN - timedelta(minutes=5),
        site_key=key,
    )


def _points(engine: Any, run_id: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT * FROM ai_nowcast_point WHERE run_id=:run ORDER BY horizon_minutes"
                ),
                {"run": run_id},
            ).mappings()
        ]


def test_migration_v4_creates_operational_tables(engine: Any) -> None:
    tables = set(sqlalchemy_inspect(engine).get_table_names())
    assert {"ai_nowcast_run", "ai_nowcast_point"} <= tables
    snapshot_columns = {
        column["name"] for column in sqlalchemy_inspect(engine).get_columns("weather_snapshot")
    }
    assert "site_key" in snapshot_columns


def test_active_mode_exposes_10_30_and_keeps_60_shadow(engine: Any) -> None:
    site, scene_id = _site_and_scene(engine)
    _store_inputs(engine, site)
    run_id = FrozenOperationalNowcaster(engine, rollout_mode="active").predict_and_persist(
        scene_id=scene_id,
        site=site,
        prediction_origin=ORIGIN,
        created_at=ORIGIN,
    )
    points = _points(engine, run_id)
    assert [point["prediction_source"] for point in points] == [
        "ai_model",
        "ai_model",
        "open_meteo_default",
    ]
    assert all(points[index]["dni_p50_W_m2"] is not None for index in (0, 1))
    assert points[2]["dni_p50_W_m2"] is None
    assert points[2]["shadow_dni_p50_W_m2"] is not None
    assert points[2]["dni_W_m2"] == 450.0


def test_shadow_mode_never_exposes_ai_quantiles(engine: Any) -> None:
    site, scene_id = _site_and_scene(engine)
    _store_inputs(engine, site)
    run_id = FrozenOperationalNowcaster(engine, rollout_mode="shadow").predict_and_persist(
        scene_id=scene_id,
        site=site,
        prediction_origin=ORIGIN,
        created_at=ORIGIN,
    )
    points = _points(engine, run_id)
    assert all(point["prediction_source"] == "open_meteo_default" for point in points)
    assert all(point["dni_p50_W_m2"] is None for point in points)
    assert all(point["shadow_dni_p50_W_m2"] is not None for point in points)


def test_stale_kma_falls_back_and_missing_open_meteo_is_unavailable(engine: Any) -> None:
    site, scene_id = _site_and_scene(engine)
    _store_inputs(engine, site, kma_observed_at=ORIGIN - timedelta(minutes=30))
    run_id = FrozenOperationalNowcaster(engine, rollout_mode="active").predict_and_persist(
        scene_id=scene_id,
        site=site,
        prediction_origin=ORIGIN,
        created_at=ORIGIN,
    )
    points = _points(engine, run_id)
    assert points[0]["prediction_source"] == "open_meteo_fallback"
    assert points[0]["fallback_reason"] == "stale_kma_observation"

    other_site = Site(35.0, 128.0, "Asia/Seoul")
    unavailable_run = FrozenOperationalNowcaster(
        engine, rollout_mode="active"
    ).predict_and_persist(
        scene_id="other",
        site=other_site,
        prediction_origin=ORIGIN,
        created_at=ORIGIN,
    )
    assert all(
        point["prediction_source"] == "unavailable"
        for point in _points(engine, unavailable_run)
    )


def test_api_reads_worker_result_and_post_injection_path_is_removed(engine: Any) -> None:
    site, scene_id = _site_and_scene(engine)
    now = datetime.now(UTC)
    origin = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    _store_inputs(engine, site, kma_observed_at=origin, include_open_meteo=False)
    FrozenOperationalNowcaster(engine, rollout_mode="shadow").predict_and_persist(
        scene_id=scene_id,
        site=site,
        prediction_origin=origin,
        created_at=now,
    )
    with TestClient(create_app(engine)) as client:
        response = client.get(f"/api/scenes/{scene_id}/ai-nowcast/latest")
        assert response.status_code == 200
        assert response.json()["model_version"] == "fr01_kma_quantile_v1"
        assert len(response.json()["points"]) == 3
        assert client.post(f"/api/scenes/{scene_id}/ai-nowcast", json={}).status_code == 404


def test_operational_service_has_no_fit_or_provider_call() -> None:
    from app.nowcast import kma_operational

    source = inspect.getsource(kma_operational.FrozenOperationalNowcaster)
    assert ".fit(" not in source
    assert "urlopen" not in source
