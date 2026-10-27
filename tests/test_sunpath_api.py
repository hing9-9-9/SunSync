"""FR-01 endpoint integration tests over real FR-06 Scene storage."""

import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from app.models.scene import SceneRecord
from app.schemas.scene import Scene
from app.services import sunpath as sunpath_service
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sunpath.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


def create_scene(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/scenes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def request_body(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "start_time": "2026-10-14T03:00:00Z",
        "duration_minutes": 10,
        "interval_seconds": 300,
    }
    result.update(changes)
    return result


def test_prediction_returns_scene_snapshot_timeline_and_sample_diagnostics(client, scene_payload):
    original = create_scene(client, scene_payload)
    response = client.post(
        f"/api/scenes/{scene_payload['scene_id']}/sunpath-predictions",
        json=request_body(expected_scene_revision=2),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scene_id"] == scene_payload["scene_id"]
    assert result["scene_revision"] == 2
    assert result["coordinate_frame"] == "ENU"
    assert result["units"] == "m"
    assert result["exposure_basis"] == "geometric_direct_path"
    assert result["optics_applied"] is False
    assert (
        len(result["timestamps"])
        == len(result["solar_positions"])
        == len(result["irradiance"])
        == 3
    )
    assert result["result_version"] == "0.2.0"
    assert all(
        irradiance["timestamp"] == timestamp
        and irradiance["ml_applied"] is False
        and irradiance["ml_reason"] == "stage2_not_enabled"
        and irradiance["model_version"] is None
        for timestamp, irradiance in zip(result["timestamps"], result["irradiance"], strict=True)
    )
    assert [region["region_id"] for region in result["regions"]] == [
        region["region_id"] for region in scene_payload["protected_regions"]
    ]
    sample = result["regions"][0]["timeline"][0]["samples"][0]
    assert {
        "sample_index",
        "point_m",
        "status",
        "reason",
        "candidate_path",
        "blocker",
        "direct_irradiance_W_m2",
    } <= set(sample)
    for timestamp in result["timestamps"]:
        assert datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0
    assert client.get(f"/api/scenes/{scene_payload['scene_id']}").json() == original


def test_prediction_uses_one_mock_weather_snapshot_without_mutating_scene(
    client, scene_payload, monkeypatch
):
    scene_payload.pop("weather")
    scene_payload.pop("scenario")
    original = create_scene(client, scene_payload)
    calls = 0

    def snapshot(*_args):
        nonlocal calls
        calls += 1
        fetched_at = datetime(2026, 10, 14, 3, tzinfo=UTC)
        return {
            datetime(2026, 10, 14, 4, tzinfo=UTC): (
                500.0,
                datetime(2026, 10, 14, 4, tzinfo=UTC),
                fetched_at,
            )
        }

    monkeypatch.setattr(sunpath_service, "_open_meteo_snapshot", snapshot)
    response = client.post(
        f"/api/scenes/{scene_payload['scene_id']}/sunpath-predictions",
        json=request_body(duration_minutes=10, interval_seconds=300),
    )
    assert response.status_code == 200, response.text
    assert calls == 1
    assert {entry["source"] for entry in response.json()["irradiance"]} == {"open_meteo"}
    assert client.get(f"/api/scenes/{scene_payload['scene_id']}").json() == original


def test_prediction_can_select_one_region_and_does_not_require_scenario_or_weather(
    client, scene_payload
):
    no_optional_inputs = copy.deepcopy(scene_payload)
    no_optional_inputs["scene_id"] = "scene_without_optional_inputs"
    no_optional_inputs["revision"] = 1
    no_optional_inputs.pop("scenario")
    no_optional_inputs.pop("weather")
    create_scene(client, no_optional_inputs)
    response = client.post(
        "/api/scenes/scene_without_optional_inputs/sunpath-predictions",
        json=request_body(protected_region_ids=["region_B"], expected_scene_revision=1),
    )
    assert response.status_code == 200, response.text
    assert [region["region_id"] for region in response.json()["regions"]] == ["region_B"]


def test_prediction_evaluates_every_sample_at_every_requested_timestamp(client, scene_payload):
    create_scene(client, scene_payload)
    response = client.post(
        f"/api/scenes/{scene_payload['scene_id']}/sunpath-predictions",
        json=request_body(
            duration_minutes=120,
            interval_seconds=600,
            protected_region_ids=["region_A"],
            expected_scene_revision=2,
        ),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    region = result["regions"][0]
    assert len(result["timestamps"]) == len(region["timeline"]) == 13
    timestamp_values = [datetime.fromisoformat(value) for value in result["timestamps"]]
    assert all(
        (following - preceding).total_seconds() == 600
        for preceding, following in pairwise(timestamp_values)
    )
    for entry in region["timeline"]:
        assert [sample["sample_index"] for sample in entry["samples"]] == [0, 1, 2, 3, 4]
        assert entry["exposed_sample_count"] == sum(
            sample["status"] == "exposed" for sample in entry["samples"]
        )
        assert entry["status"] == ("exposed" if entry["exposed_sample_count"] else "not_exposed")
    first_exposed_entry = next(
        (entry for entry in region["timeline"] if entry["status"] == "exposed"), None
    )
    assert region["first_predicted_exposure_time"] == (
        first_exposed_entry["timestamp"] if first_exposed_entry is not None else None
    )


def test_prediction_reports_not_found_revision_and_request_validation_errors(client, scene_payload):
    assert (
        client.post("/api/scenes/missing/sunpath-predictions", json=request_body()).status_code
        == 404
    )
    create_scene(client, scene_payload)
    path = f"/api/scenes/{scene_payload['scene_id']}/sunpath-predictions"
    assert client.post(path, json=request_body(expected_scene_revision=1)).status_code == 409
    assert client.post(path, json=request_body(start_time="2026-10-14T03:00:00")).status_code == 422
    assert (
        client.post(
            path, json=request_body(protected_region_ids=["region_A", "region_A"])
        ).status_code
        == 422
    )
    assert (
        client.post(path, json=request_body(protected_region_ids=["missing_region"])).status_code
        == 422
    )
    assert (
        client.post(path, json=request_body(duration_minutes=1440, interval_seconds=1)).status_code
        == 422
    )


def test_prediction_rejects_invalid_stored_scene_geometry_without_mutation(
    client, database_engine, scene_payload
):
    validated = Scene.model_validate(scene_payload)
    with Session(database_engine) as session:
        session.add(
            SceneRecord(
                scene_id="invalid_scene",
                revision=1,
                scene_json=validated.model_dump_json().replace('"height_m":3.0', '"height_m":-1.0'),
            )
        )
        session.commit()
    response = client.post("/api/scenes/invalid_scene/sunpath-predictions", json=request_body())
    assert response.status_code == 422
    with Session(database_engine) as session:
        record = session.get(SceneRecord, "invalid_scene")
        assert record is not None
        assert record.revision == 1
