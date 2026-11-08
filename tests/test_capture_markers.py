"""Regression coverage for window frames and persisted legacy marker recovery."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from app.api.capture_markers import CaptureMarkerSession
from app.main import create_app
from app.models.film import FilmStateRecord
from app.services.film import generate_aruco_4x4_pattern
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'frames.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


def make_scene(client: TestClient, wall: str, paired: bool = True) -> dict[str, Any]:
    space = {
        "id": "standard_film",
        "building": "B",
        "floor": "1F",
        "name": "Room",
        "width": 6.0,
        "depth": 5.0,
        "height": 3.0,
        "zone": "z",
        "zones": ["z"],
        "source": "manual",
        "revision": 1,
        "site": {
            "latitude": 37.5,
            "longitude": 127.0,
            "timezone": "Asia/Seoul",
            "northConfirmed": True,
        },
        "windowDimensions": {"width": 1.0, "height": 1.0, "wall": wall, "offset": 2.0, "sill": 0.8},
        "furniture": [],
        "regions": [],
    }
    created = client.post("/api/ui/spaces", json=space)
    assert created.status_code == 201, created.text
    scene_id = created.json()["serverSceneId"]
    if paired:
        result = client.post(f"/api/scenes/{scene_id}/marker-pairing", json={"code_id": 0})
        assert result.status_code == 200, result.text
    return client.get(f"/api/scenes/{scene_id}").json()


def start(client: TestClient, scene: dict, request_id: str | None = None):
    return client.post(
        f"/api/scenes/{scene['scene_id']}/capture-marker",
        json={"request_id": request_id or str(uuid4())},
    )


def test_random_id_is_persisted_displayed_and_idempotent(client, monkeypatch, database_engine):
    scene = make_scene(client, "east", False)
    calls = []

    def choose(ids):
        calls.append(ids)
        return 37

    monkeypatch.setattr("app.api.capture_markers.secrets.choice", choose)
    token = str(uuid4())
    response = start(client, scene, token)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["session_id"] == token
    assert data["board"]["ids_by_row"] == [[37]]
    assert data["marker"]["code_id"] == 37
    assert data["display_status"] == "virtual_applied"
    assert data["board"]["marker_side_m"] == 0.6
    pattern = generate_aruco_4x4_pattern(37)
    assert [row[2:8] for row in data["film_cells"][2:8]] == pattern
    assert start(client, scene, token).json() == data
    assert len(calls) == 1
    assert start(client, scene).status_code == 409
    with TestClient(create_app(database_engine)) as restarted:
        assert (
            restarted.get(f"/api/scenes/{scene['scene_id']}/capture-marker/{token}").json() == data
        )


def test_end_restores_cells_and_old_session_cannot_end_new_one(client):
    scene = make_scene(client, "north", False)
    base = f"/api/scenes/{scene['scene_id']}"
    client.post(base + "/film-commands", json={"commands": [{"row": 3, "col": 3, "opacity": 0.5}]})
    first = start(client, scene).json()
    end = base + f"/capture-marker/{first['session_id']}/end"
    assert client.post(end).status_code == 200
    assert client.post(end).status_code == 200
    with TestClient(client.app) as reader:
        film = reader.get(base + "/film-state").json()["windows"][0]
        assert film["marker_mode_active"] is False
        assert film["cells"][3][3] == 0.5
    assert client.get(base + f"/capture-marker/{first['session_id']}").status_code == 410
    second = start(client, scene)
    assert second.status_code == 200, second.text
    assert client.post(end).status_code == 409


def test_marker_display_failure_rolls_back_geometry_and_session(
    client, database_engine, monkeypatch
):
    scene = make_scene(client, "west", False)

    def fail(*args, **kwargs):
        raise HTTPException(503, "display unavailable")

    monkeypatch.setattr("app.api.capture_markers.set_marker_mode", fail)
    assert start(client, scene).status_code == 503
    assert client.get(f"/api/scenes/{scene['scene_id']}").json() == scene
    with Session(database_engine) as db:
        assert db.get(CaptureMarkerSession, scene["scene_id"]) is None
        assert (
            db.get(FilmStateRecord, f"{scene['scene_id']}:{scene['windows'][0]['window_id']}")
            is None
        )


def test_changed_film_rejects_stale_capture(client):
    scene = make_scene(client, "south", False)
    data = start(client, scene).json()
    base = f"/api/scenes/{scene['scene_id']}"
    assert client.post(base + "/film-marker-mode", json={"enabled": False}).status_code == 200
    assert client.get(base + f"/capture-marker/{data['session_id']}").status_code == 409


def test_expired_session_can_be_replaced(client, database_engine):
    scene = make_scene(client, "south", False)
    data = start(client, scene).json()
    with Session(database_engine) as db:
        row = db.get(CaptureMarkerSession, scene["scene_id"])
        row.expires_at = "2000-01-01T00:00:00+00:00"
        db.add(row)
        db.commit()
    assert (
        client.get(
            f"/api/scenes/{scene['scene_id']}/capture-marker/{data['session_id']}"
        ).status_code
        == 410
    )
    assert start(client, scene).status_code == 200


def test_simultaneous_starts_do_not_rotate_an_active_marker(client):
    scene = make_scene(client, "south", False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(client, scene), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]


def test_standard_geometry_required_and_no_scene_creation(client):
    assert (
        client.post(
            "/api/scenes/missing/capture-marker", json={"request_id": str(uuid4())}
        ).status_code
        == 404
    )
    scene = make_scene(client, "south", False)
    scene["windows"][0]["film_rows"] = 20
    scene["windows"][0]["film_cols"] = 20
    scene["revision"] += 1
    assert client.put(f"/api/scenes/{scene['scene_id']}", json=scene).status_code == 200
    assert start(client, scene).status_code == 422
