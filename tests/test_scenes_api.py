"""Exercise the real API against disposable SQLite files, never the local DB."""

import copy
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from app.main import create_app
from app.models.room import Room
from app.scene_cli import schema_document
from app.schemas.scene import Scene
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, create_engine

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scenes.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


def next_revision(payload: dict[str, Any], name: str) -> dict[str, Any]:
    changed = copy.deepcopy(payload)
    changed["revision"] += 1
    if changed.get("scenario") is not None:
        changed["scenario"]["scene_revision"] = changed["revision"]
    changed["room"]["name"] = name
    return changed


def test_complete_scene_round_trip_preserves_nulls_and_provenance(client, scene_payload):
    expected = Scene.model_validate(scene_payload).model_dump(mode="json")
    created = client.post("/api/scenes", json=scene_payload)
    assert created.status_code == 201, created.text
    assert created.json() == expected
    assert created.json()["revision"] == 2  # Import does not reset document revision.

    fetched = client.get(f"/api/scenes/{scene_payload['scene_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == expected
    assert fetched.json()["north_alignment"]["accuracy_deg"] is None
    assert fetched.json()["weather"]["forecast_reference_time"] is None
    assert fetched.json()["observations"] == expected["observations"]
    assert client.get("/api/scenes").json() == [expected]


def test_manual_input_without_weather_round_trips(client):
    payload = json.loads((EXAMPLES / "fr06_manual_room.json").read_text(encoding="utf-8"))
    created = client.post("/api/scenes", json=payload)
    assert created.status_code == 201, created.text
    expected = Scene.model_validate(payload).model_dump(mode="json")
    assert created.json() == expected
    assert created.json()["weather"] is None
    assert created.json()["scenario"] is None
    assert client.get(f"/api/scenes/{payload['scene_id']}").json() == expected


def test_duplicate_create_does_not_replace_existing(client, scene_payload):
    original = client.post("/api/scenes", json=scene_payload).json()
    scene_payload["room"]["name"] = "Duplicate must not replace original"
    response = client.post("/api/scenes", json=scene_payload)
    assert response.status_code == 409
    assert client.get(f"/api/scenes/{scene_payload['scene_id']}").json() == original


def test_scene_id_schema_round_trips_without_shadowing_schema_endpoint(client, scene_payload):
    scene_payload["scene_id"] = "schema"
    created = client.post("/api/scenes", json=scene_payload)
    assert created.status_code == 201
    assert client.get("/api/scenes/schema").json() == created.json()
    assert client.get("/api/scene-schema").json() == schema_document()


@pytest.mark.parametrize(
    "invalid",
    ["schema_version", "revision", "height", "floor", "unknown_field"],
)
def test_invalid_input_returns_422_without_write(client, scene_payload, invalid):
    if invalid == "schema_version":
        scene_payload["schema_version"] = "0.1.0"
    elif invalid == "revision":
        scene_payload["revision"] = 0
    elif invalid == "height":
        scene_payload["room"]["height_m"] = -1
    elif invalid == "floor":
        scene_payload["room"]["floor_vertices_m"] = [[0, 0, 0]] * 4
    else:
        scene_payload["unrecognized_field"] = True
    response = client.post("/api/scenes", json=scene_payload)
    assert response.status_code == 422, response.text
    assert client.get("/api/scenes").json() == []


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400"])
@pytest.mark.parametrize("field", ["height", "extra"])
def test_nonfinite_raw_input_returns_serializable_422_without_write(
    client, scene_payload, token, field
):
    if field == "height":
        scene_payload["room"]["height_m"] = "INVALID_NUMBER_TOKEN"
    else:
        scene_payload["unknown_field"] = "INVALID_NUMBER_TOKEN"
    raw_body = json.dumps(scene_payload).replace('"INVALID_NUMBER_TOKEN"', token)
    response = client.post(
        "/api/scenes", content=raw_body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert client.get("/api/scenes").json() == []


def test_revision_beyond_sqlite_integer_range_returns_422_without_write(client, scene_payload):
    scene_payload["revision"] = 2**63
    scene_payload["scenario"]["scene_revision"] = 2**63
    response = client.post("/api/scenes", json=scene_payload)
    assert response.status_code == 422, response.text
    assert client.get("/api/scenes").json() == []


def test_validation_and_schema_endpoints_do_not_persist(client, scene_payload):
    response = client.post("/api/scenes/validate", json=scene_payload)
    assert response.status_code == 200
    assert response.json() == Scene.model_validate(scene_payload).model_dump(mode="json")
    assert client.get("/api/scenes").json() == []
    schema = client.get("/api/scene-schema")
    assert schema.status_code == 200
    assert schema.json() == schema_document()
    scene_payload["room"]["height_m"] = -1
    assert client.post("/api/scenes/validate", json=scene_payload).status_code == 422


def test_list_is_bounded_stable_and_paginated(client, scene_payload):
    for scene_id in ["scene_c", "scene_a", "scene_b"]:
        scene_payload["scene_id"] = scene_id
        assert client.post("/api/scenes", json=scene_payload).status_code == 201
    page = client.get("/api/scenes", params={"offset": 1, "limit": 1})
    assert page.status_code == 200
    assert [scene["scene_id"] for scene in page.json()] == ["scene_b"]
    assert client.get("/api/scenes", params={"offset": 3}).json() == []
    assert client.get("/api/scenes", params={"offset": 2**63 - 1}).json() == []
    for params in [{"offset": -1}, {"offset": 2**63}, {"limit": 0}, {"limit": 101}]:
        assert client.get("/api/scenes", params=params).status_code == 422


def test_replace_requires_matching_id_and_next_revision(client, scene_payload):
    original = client.post("/api/scenes", json=scene_payload).json()
    path = f"/api/scenes/{scene_payload['scene_id']}"
    update = next_revision(scene_payload, "Updated room")
    update["scene_id"] = "different_scene"
    assert client.put(path, json=update).status_code == 422
    assert client.get(path).json() == original

    update["scene_id"] = scene_payload["scene_id"]
    accepted = client.put(path, json=update)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == Scene.model_validate(update).model_dump(mode="json")
    assert client.get(path).json() == accepted.json()

    stale = next_revision(scene_payload, "Stale client overwrites room")
    assert client.put(path, json=stale).status_code == 409
    skipped = next_revision(next_revision(update, "Skipped revision"), "Skipped revision")
    assert client.put(path, json=skipped).status_code == 409
    assert client.get(path).json() == accepted.json()


def test_invalid_replace_does_not_mutate_existing_scene(client, scene_payload):
    original = client.post("/api/scenes", json=scene_payload).json()
    path = f"/api/scenes/{scene_payload['scene_id']}"
    invalid = next_revision(scene_payload, "Invalid update")
    invalid["room"]["height_m"] = -1
    assert client.put(path, json=invalid).status_code == 422
    assert client.get(path).json() == original


def test_concurrent_same_revision_updates_cannot_lose_a_write(client, scene_payload):
    assert client.post("/api/scenes", json=scene_payload).status_code == 201
    path = f"/api/scenes/{scene_payload['scene_id']}"
    barrier = Barrier(2)

    def update(name):
        payload = next_revision(scene_payload, name)
        barrier.wait(timeout=10)
        return client.put(path, json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(update, ["Client one", "Client two"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert client.get(path).json() == winner


def test_unknown_scene_returns_404(client, scene_payload):
    assert client.get("/api/scenes/missing").status_code == 404
    update = next_revision(scene_payload, "No scene yet")
    update["scene_id"] = "missing"
    assert client.put("/api/scenes/missing", json=update).status_code == 404
    assert client.get("/api/scenes").json() == []


def test_scene_survives_app_restart(database_engine, scene_payload):
    with TestClient(create_app(database_engine)) as first_client:
        response = first_client.post("/api/scenes", json=scene_payload)
        assert response.status_code == 201
        expected = response.json()
    database_engine.dispose()
    with TestClient(create_app(database_engine)) as restarted_client:
        assert restarted_client.get(f"/api/scenes/{scene_payload['scene_id']}").json() == expected


def test_startup_preserves_existing_draft_room_table(database_engine):
    Room.__table__.create(database_engine)
    with Session(database_engine) as session:
        room = Room(
            name="Existing room",
            latitude=37.5,
            longitude=127,
            width_m=8,
            depth_m=6,
            height_m=3,
        )
        session.add(room)
        session.commit()
        session.refresh(room)
        room_id = room.id
    with TestClient(create_app(database_engine)) as test_client:
        assert test_client.get("/api/health").json() == {"status": "ok"}
        assert test_client.get("/api/scenes").json() == []
    assert {"room", "scenes"} <= set(inspect(database_engine).get_table_names())
    with Session(database_engine) as session:
        assert session.get(Room, room_id).name == "Existing room"
