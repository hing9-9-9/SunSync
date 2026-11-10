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


def make_scene(
    client: TestClient, wall: str, paired: bool = True, width: float = 1.0, height: float = 1.0
) -> dict[str, Any]:
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
        "windowDimensions": {
            "width": width,
            "height": height,
            "wall": wall,
            "offset": 2.0,
            "sill": 0.8,
        },
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


def start(client: TestClient, scene: dict, request_id: str | None = None, **target):
    return client.post(
        f"/api/scenes/{scene['scene_id']}/capture-marker",
        json={"request_id": request_id or str(uuid4()), **target},
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


@pytest.mark.parametrize("wall", ["north", "east", "south", "west"])
def test_selected_module_on_large_window_has_correct_pattern_and_world_height(client, wall):
    scene = make_scene(client, wall, False, width=3.0, height=2.0)
    token = str(uuid4())
    data = start(client, scene, token, module_row=1, module_col=2)
    assert data.status_code == 200, data.text
    payload = data.json()
    assert payload["film_module"] == {
        "row": 1,
        "col": 2,
        "rows": 2,
        "cols": 3,
        "side_m": 1.0,
        "marker_center_uv_m": pytest.approx([2.5, 0.5]),
    }
    marker = payload["marker"]
    assert marker["side_length_m"] == 0.6
    assert (marker["start_row"], marker["start_col"]) == (12, 22)
    win = scene["windows"][0]
    expected = [
        win["vertices_m"][0][i] + 2.5 * win["u_axis"][i] + 0.5 * win["v_axis"][i] for i in range(3)
    ]
    assert marker["T_ENU_marker"][3:12:4] == pytest.approx(expected)
    assert marker["T_ENU_marker"][11] == pytest.approx(1.3)
    cells = payload["film_cells"]
    assert len(cells) == 20 and all(len(row) == 30 for row in cells)
    assert [row[22:28] for row in cells[12:18]] == generate_aruco_4x4_pattern(marker["code_id"])
    assert cells[2][2] == 0.0
    assert start(client, scene, token, module_row=1, module_col=2).json() == payload
    assert start(client, scene, token, module_row=0, module_col=0).status_code == 409
    base = f"/api/scenes/{scene['scene_id']}"
    assert client.post(base + f"/capture-marker/{token}/end").status_code == 200
    second = start(client, scene, module_row=0, module_col=1)
    assert second.status_code == 200, second.text
    assert second.json()["marker"]["T_ENU_marker"][11] == pytest.approx(2.3)


@pytest.mark.parametrize(
    "target",
    [
        {"module_row": 2},
        {"module_col": 3},
        {"module_row": -1},
        {"module_col": 0.5},
        {"module_row": True},
    ],
)
def test_invalid_module_rejected_without_pairing(client, target):
    scene = make_scene(client, "north", False, width=3.0, height=2.0)
    assert start(client, scene, **target).status_code == 422
    assert client.get(f"/api/scenes/{scene['scene_id']}").json() == scene


def test_pairing_selects_module_and_keeps_fixed_scale(client):
    scene = make_scene(client, "north", False, width=3.0, height=2.0)
    base = f"/api/scenes/{scene['scene_id']}"
    result = client.post(base + "/marker-pairing", json={"module_row": 1, "module_col": 1})
    assert result.status_code == 200, result.text
    marker = client.get(base).json()["markers"][0]
    assert marker["start_row"] == marker["start_col"] == 12
    assert marker["side_length_m"] == 0.6
    assert marker["T_ENU_marker"][11] == pytest.approx(1.3)


def test_partial_modules_not_silently_stretched(client):
    scene = make_scene(client, "north", False, width=2.0, height=1.5)
    assert start(client, scene).status_code == 422
    base = f"/api/scenes/{scene['scene_id']}"
    assert client.post(base + "/marker-pairing", json={}).status_code == 422
    assert client.get(base).json() == scene


def test_module_repair_moves_display_and_end_restores_original_grid(client):
    scene = make_scene(client, "north", False, width=3.0, height=2.0)
    base = f"/api/scenes/{scene['scene_id']}"
    assert client.post(base + "/marker-pairing", json={}).status_code == 200
    result = client.post(base + "/marker-pairing", json={"module_row": 1, "module_col": 2})
    assert result.status_code == 200, result.text
    cells = result.json()["film_state"]["cells"]
    assert cells[2][2] == 0.0 and cells[12][22] == 1.0
    assert client.post(base + "/film-marker-mode", json={"enabled": False}).status_code == 200
    cells = client.get(base + "/film-state").json()["windows"][0]["cells"]
    assert all(value == 0.0 for row in cells for value in row)
    result = client.post(
        base + "/film-marker-mode",
        json={
            "enabled": True,
            "module_row": 1,
            "module_col": 1,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["cells"][12][12] == 1.0
    assert (
        client.post(
            base + "/film-marker-mode",
            json={
                "enabled": True,
                "module_row": 0,
                "module_col": 0,
                "start_row": 2,
            },
        ).status_code
        == 422
    )


def test_multi_module_vision_does_not_assume_window_centre(client):
    scene = make_scene(client, "north", False, width=3.0, height=2.0)
    result = client.post(
        f"/api/scenes/{scene['scene_id']}/vision-furniture-estimate",
        json={
            "camera": {
                "width": 1280,
                "height": 720,
                "fx": 900.0,
                "fy": 900.0,
                "cx": 640.0,
                "cy": 360.0,
            },
            "marker_anchor": {
                "code_id": 0,
                "side_length_m": 0.6,
                "corners_px": [[550, 270], [730, 270], [730, 450], [550, 450]],
                "anchor_depth_rel": 3.0,
            },
            "detections": [
                {
                    "box_xyxy": [450.0, 300.0, 750.0, 550.0],
                    "label": "chair",
                    "confidence": 0.9,
                    "observed_depth_rel": 2.0,
                }
            ],
            "apply_to_scene": False,
        },
    )
    assert result.status_code == 422 and "pair a film module" in result.text


def test_capture_targets_exposes_full_module_grid(client):
    scene = make_scene(client, "east", False, width=3, height=2)
    response = client.get(f"/api/scenes/{scene['scene_id']}/capture-marker/targets")
    assert response.status_code == 200
    assert response.json() == {
        "windows": [{"window_id": scene["windows"][0]["window_id"], "rows": 2, "cols": 3}]
    }


def test_capture_targets_excludes_partial_modules(client):
    scene = make_scene(client, "east", False, width=2, height=1.5)
    response = client.get(f"/api/scenes/{scene['scene_id']}/capture-marker/targets")
    assert response.status_code == 200
    assert response.json() == {"windows": []}
