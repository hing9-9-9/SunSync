"""Integration tests for event-driven + rolling time-bucket scenario precomputation."""

from __future__ import annotations

import concurrent.futures
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from app.models.scenario_snapshot import ScenarioSnapshotRecord
from app.nowcast import operational_nowcast_worker
from app.nowcast.adapters import RawPoint, RawSnapshot
from app.nowcast.kma_operational_repository import site_key
from app.nowcast.repository import SnapshotRepository
from app.services import scenario_store
from app.services import sunpath as sunpath_service
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlmodel import Session, create_engine, select

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scenario.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_geometry_cache() -> Iterator[None]:
    with sunpath_service._GEOMETRY_LOCK:
        sunpath_service._GEOMETRY_CACHE.clear()
    yield
    with sunpath_service._GEOMETRY_LOCK:
        sunpath_service._GEOMETRY_CACHE.clear()


def test_scenario_persists_once_and_serves_all_endpoints_without_recomputation(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post("/api/scenes", json=scene_payload)
    assert created.status_code == 201, created.text
    scene_id = scene_payload["scene_id"]

    ray_calls = 0
    real_compute = sunpath_service._compute_ray_trace

    def counting_ray_trace(*args: Any, **kwargs: Any) -> Any:
        nonlocal ray_calls
        ray_calls += 1
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(sunpath_service, "_compute_ray_trace", counting_ray_trace)

    req_body = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 60,
        "interval_seconds": 300,
        "expected_scene_revision": scene_payload["revision"],
    }

    r1 = client.post(f"/api/scenes/{scene_id}/sunpath-predictions", json=req_body)
    assert r1.status_code == 200, r1.text
    assert ray_calls == 1

    # FR-02 shading plan, FR-04 adaptive preview, and scenario bundle reuse the persisted scenario.
    r2 = client.post(f"/api/scenes/{scene_id}/shading-plans", json=req_body)
    assert r2.status_code == 200, r2.text
    assert ray_calls == 1

    adaptive_req = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 60,
        "interval_seconds": 300,
    }
    r3 = client.post(f"/api/scenes/{scene_id}/adaptive-shading-plan", json=adaptive_req)
    assert r3.status_code == 200, r3.text
    assert ray_calls == 1

    # Second call hits adaptive_plan_json directly from SQLite as well.
    r3_cached = client.post(f"/api/scenes/{scene_id}/adaptive-shading-plan", json=adaptive_req)
    assert r3_cached.status_code == 200, r3_cached.text
    assert r3_cached.json() == r3.json()
    assert ray_calls == 1

    bundle = client.get(
        f"/api/scenes/{scene_id}/scenario",
        params={
            "start_time": "2026-10-14T04:30:00Z",
            "duration_minutes": 60,
            "interval_seconds": 300,
        },
    )
    assert bundle.status_code == 200, bundle.text
    body = bundle.json()
    assert body["scene_id"] == scene_id
    assert body["sunpath_prediction"] == r1.json()
    assert body["shading_plan"] == r2.json()
    assert body["adaptive_plan"] == r3.json()

    with Session(database_engine) as session:
        rows = session.exec(
            select(ScenarioSnapshotRecord).where(ScenarioSnapshotRecord.scene_id == scene_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].shading_plan_json is not None
        assert rows[0].adaptive_plan_json is not None


def test_single_flight_coalesces_concurrent_sunpath_and_shading_requests(
    client: TestClient,
    scene_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.post("/api/scenes", json=scene_payload).status_code == 201
    scene_id = scene_payload["scene_id"]

    ray_calls = 0
    real_compute = sunpath_service._compute_ray_trace

    def counting_ray_trace(*args: Any, **kwargs: Any) -> Any:
        nonlocal ray_calls
        ray_calls += 1
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(sunpath_service, "_compute_ray_trace", counting_ray_trace)

    req_body = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 60,
        "interval_seconds": 300,
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_sun = pool.submit(
            client.post, f"/api/scenes/{scene_id}/sunpath-predictions", json=req_body
        )
        f_shd = pool.submit(client.post, f"/api/scenes/{scene_id}/shading-plans", json=req_body)
        res_sun, res_shd = f_sun.result(), f_shd.result()

    assert res_sun.status_code == 200, res_sun.text
    assert res_shd.status_code == 200, res_shd.text
    assert ray_calls == 1


def test_local_scene_update_and_ui_delete_invalidate_scenario_snapshots(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
) -> None:
    assert client.post("/api/scenes", json=scene_payload).status_code == 201
    scene_id = scene_payload["scene_id"]
    req_body = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 30,
        "interval_seconds": 300,
    }
    assert client.post(f"/api/scenes/{scene_id}/shading-plans", json=req_body).status_code == 200

    with Session(database_engine) as session:
        assert (
            len(
                session.exec(
                    select(ScenarioSnapshotRecord).where(
                        ScenarioSnapshotRecord.scene_id == scene_id
                    )
                ).all()
            )
            == 1
        )

    updated_payload = {
        **scene_payload,
        "revision": scene_payload["revision"] + 1,
        "scenario": {**scene_payload["scenario"], "scene_revision": scene_payload["revision"] + 1}
        if scene_payload.get("scenario")
        else None,
    }
    assert client.put(f"/api/scenes/{scene_id}", json=updated_payload).status_code == 200

    with Session(database_engine) as session:
        assert (
            session.exec(
                select(ScenarioSnapshotRecord).where(ScenarioSnapshotRecord.scene_id == scene_id)
            ).all()
            == []
        )

    # Also verify UI space PUT / DELETE invalidates associated scenario snapshots.
    ui_payload = {
        "id": "ui-cache-space",
        "building": "도서관",
        "floor": "2층",
        "name": "열람실",
        "width": 6,
        "depth": 5,
        "height": 3,
        "zone": "구역 01",
        "zones": ["구역 01"],
        "site": {
            "latitude": 37.5665,
            "longitude": 126.978,
            "timezone": "Asia/Seoul",
            "northConfirmed": True,
        },
        "windowDimensions": {"width": 2, "height": 1.5, "wall": "south", "offset": 2, "sill": 0.9},
        "furniture": [
            {
                "id": "f1",
                "name": "책장",
                "type": "bookcase",
                "zone": "구역 01",
                "x": 2,
                "y": 1,
                "width": 1.2,
                "depth": 0.5,
                "height": 1.6,
            }
        ],
        "regions": [
            {
                "id": "r1",
                "name": "책장 보호면",
                "zone": "구역 01",
                "furnitureId": "f1",
                "x": 2,
                "y": 1,
                "width": 1.2,
                "depth": 0.5,
                "face": "남쪽 전면",
                "bottom": 0,
                "top": 1.6,
                "grade": "A",
            }
        ],
        "source": "manual",
        "revision": 1,
    }
    created = client.post("/api/ui/spaces", json=ui_payload)
    assert created.status_code == 201, created.text
    ui_saved = created.json()
    ui_scene_id = ui_saved["serverSceneId"]

    assert client.get(f"/api/scenes/{ui_scene_id}/scenario").status_code == 200
    with Session(database_engine) as session:
        assert (
            len(
                session.exec(
                    select(ScenarioSnapshotRecord).where(
                        ScenarioSnapshotRecord.scene_id == ui_scene_id
                    )
                ).all()
            )
            == 1
        )

    ui_saved["name"] = "변경된 열람실"
    assert client.put(f"/api/ui/spaces/{ui_saved['id']}", json=ui_saved).status_code == 200
    with Session(database_engine) as session:
        assert (
            session.exec(
                select(ScenarioSnapshotRecord).where(ScenarioSnapshotRecord.scene_id == ui_scene_id)
            ).all()
            == []
        )

    assert client.get(f"/api/scenes/{ui_scene_id}/scenario").status_code == 200
    assert client.delete(f"/api/ui/spaces/{ui_saved['id']}").status_code == 204
    with Session(database_engine) as session:
        assert (
            session.exec(
                select(ScenarioSnapshotRecord).where(ScenarioSnapshotRecord.scene_id == ui_scene_id)
            ).all()
            == []
        )


def test_global_rolling_update_refreshes_on_weather_change_and_reuses_geometry(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_without_weather = {
        k: v for k, v in scene_payload.items() if k not in ("weather", "scenario")
    }
    assert client.post("/api/scenes", json=scene_without_weather).status_code == 201
    scene_id = scene_without_weather["scene_id"]

    ray_calls = 0
    real_compute = sunpath_service._compute_ray_trace

    def counting_ray_trace(*args: Any, **kwargs: Any) -> Any:
        nonlocal ray_calls
        ray_calls += 1
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(sunpath_service, "_compute_ray_trace", counting_ray_trace)

    t0 = datetime(2026, 10, 14, 4, 2, tzinfo=UTC)
    stats0 = scenario_store.run_rolling_scenario_update(database_engine, now=t0)
    assert stats0 == {"scenes": 1, "updated": 1, "failed": 0}
    assert ray_calls == 1

    with Session(database_engine) as session:
        snap0 = session.exec(
            select(ScenarioSnapshotRecord).where(ScenarioSnapshotRecord.scene_id == scene_id)
        ).one()
        assert snap0.time_bucket == "2026-10-14T04:00:00Z"
        fp0 = snap0.weather_fingerprint

    # New active AI nowcast arrives in the next 10-minute bucket -> fingerprint changes, geometry reused!
    t1 = t0 + timedelta(minutes=10)
    lat = scene_without_weather["site"]["latitude_deg"]
    lon = scene_without_weather["site"]["longitude_deg"]
    skey = site_key(lat, lon)
    with database_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ai_nowcast_run(
                  run_id, scene_id, site_key, prediction_origin, created_at, model_version,
                  model_manifest_sha256, input_snapshot_ids_json, rollout_mode, status, result_hash, expires_at
                ) VALUES (
                  'active-run-1', :scene_id, :site_key, '2026-10-14T04:10:00Z', '2026-10-14T04:10:00Z',
                  'fr01_kma_quantile_v1', 'hash', '[]', 'active', 'ready', 'res', '2026-10-14T04:30:00Z'
                )
                """
            ),
            {"scene_id": scene_id, "site_key": skey},
        )
        conn.execute(
            text(
                """
                INSERT INTO ai_nowcast_point(
                  run_id, horizon_minutes, valid_time, prediction_source, dni_W_m2,
                  dni_p10_W_m2, dni_p50_W_m2, dni_p90_W_m2, open_meteo_dni_W_m2,
                  fallback_used, quality_flags_json
                ) VALUES (
                  'active-run-1', 10, '2026-10-14T04:20:00Z', 'ai_model', 450, 300, 450, 600, 420, 0, '[]'
                )
                """
            )
        )

    SnapshotRepository(database_engine, id_factory=lambda: "om-snap-1").store(
        RawSnapshot(
            provider="open_meteo",
            product="hourly_direct_normal_irradiance",
            model="ecmwf_ifs025",
            payload=b"om-1",
            points=(
                RawPoint(
                    valid_time=datetime(2026, 10, 14, 5, 0, tzinfo=UTC),
                    latitude_deg=lat,
                    longitude_deg=lon,
                    temporal_meaning="mean",
                    interval_start=datetime(2026, 10, 14, 4, 0, tzinfo=UTC),
                    interval_end=datetime(2026, 10, 14, 5, 0, tzinfo=UTC),
                    dni_W_m2=510.0,
                ),
            ),
            provenance={"spatial_provenance": "provider_grid"},
        ),
        t1 - timedelta(minutes=1),
        site_key=skey,
    )

    stats1 = operational_nowcast_worker.run_once(database_engine, now=t1)
    assert stats1["lease_acquired"] == 1
    assert stats1["scenarios_updated"] == 1
    assert ray_calls == 1  # Geometry raycast reused from _GEOMETRY_CACHE!

    with Session(database_engine) as session:
        snap1 = session.exec(
            select(ScenarioSnapshotRecord).where(ScenarioSnapshotRecord.scene_id == scene_id)
        ).one()
        assert snap1.time_bucket == "2026-10-14T04:10:00Z"
        assert snap1.weather_fingerprint != fp0
        assert snap1.adaptive_plan_json is not None


def test_scenario_endpoint_returns_404_for_missing_scene(client: TestClient) -> None:
    assert client.get("/api/scenes/missing-scene/scenario").status_code == 404
