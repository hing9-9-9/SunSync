"""Regression coverage for window frames and persisted legacy marker recovery."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from app.main import create_app
from app.models.scene import SceneRecord
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from engine.vision_ai.pipeline import VisionRegistrationPipeline


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


def transpose_rotation(scene: dict[str, Any]) -> None:
    matrix = np.array(scene["markers"][0]["T_ENU_marker"]).reshape(4, 4)
    matrix[:3, :3] = matrix[:3, :3].T
    scene["markers"][0]["T_ENU_marker"] = matrix.ravel().tolist()


def store_legacy(engine: Engine, scene: dict[str, Any]) -> str:
    payload = json.dumps(scene)
    with Session(engine) as session:
        record = session.get(SceneRecord, scene["scene_id"])
        assert record is not None
        record.scene_json = payload
        session.add(record)
        session.commit()
    return payload


@pytest.mark.parametrize("wall", ["north", "east", "west"])
def test_transposed_marker_rejected_on_write(client: TestClient, wall: str) -> None:
    scene = make_scene(client, wall)
    original = copy.deepcopy(scene)
    transpose_rotation(scene)
    scene["revision"] += 1
    result = client.put(f"/api/scenes/{scene['scene_id']}", json=scene)
    assert result.status_code == 422
    assert "rotation columns" in result.text
    assert client.post("/api/scenes/validate", json=scene).status_code == 422
    assert client.get(f"/api/scenes/{scene['scene_id']}").json() == original


@pytest.mark.parametrize("wall", ["south", "north", "east", "west"])
def test_rotated_in_plane_marker_rejected(client: TestClient, wall: str) -> None:
    scene = make_scene(client, wall)
    matrix = np.array(scene["markers"][0]["T_ENU_marker"]).reshape(4, 4)
    matrix[:3, :2] *= -1  # Still rigid and on the window, but not the displayed film axes.
    scene["markers"][0]["T_ENU_marker"] = matrix.ravel().tolist()
    assert client.post("/api/scenes/validate", json=scene).status_code == 422


@pytest.mark.parametrize("wall", ["south", "north", "east", "west"])
def test_legacy_repair_persists_on_pairing_only(
    client: TestClient, database_engine: Engine, wall: str
) -> None:
    scene = make_scene(client, wall)
    original = copy.deepcopy(scene)
    transpose_rotation(scene)
    stored = store_legacy(database_engine, scene)
    url = f"/api/scenes/{scene['scene_id']}"
    result = client.get(url)
    assert result.status_code == (200 if wall == "south" else 409)
    with Session(database_engine) as session:
        assert session.get(SceneRecord, scene["scene_id"]).scene_json == stored
    repaired = client.post(
        url + "/marker-pairing", json={"code_id": 0, "enable_marker_display": False}
    )
    assert repaired.status_code == 200, repaired.text
    final = client.get(url).json()
    assert final["revision"] == original["revision"] + 1
    assert final["markers"] == original["markers"]
    assert final["observations"] == original["observations"]
    assert final["occluders"] == original["occluders"]


@pytest.mark.parametrize("corruption", ["other_rotation", "room", "dependent_calibration"])
def test_pairing_does_not_hide_unrelated_errors_or_reinterpret_measurements(
    client: TestClient, database_engine: Engine, corruption: str
) -> None:
    scene = make_scene(client, "east")
    transpose_rotation(scene)
    if corruption == "other_rotation":
        matrix = np.array(scene["markers"][0]["T_ENU_marker"]).reshape(4, 4)
        matrix[:3, :2] *= -1
        scene["markers"][0]["T_ENU_marker"] = matrix.ravel().tolist()
    elif corruption == "room":
        scene["room"]["height_m"] = -1.0
    else:
        scene["registration_calibration"] = {
            "calibration_id": "a" * 64,
            "marker": copy.deepcopy(scene["markers"][0]),
        }
    stored = store_legacy(database_engine, scene)
    result = client.post(f"/api/scenes/{scene['scene_id']}/marker-pairing", json={"code_id": 0})
    assert result.status_code == 409, result.text
    if corruption == "dependent_calibration":
        assert "dependent measurements or registration calibration" in result.text
    with Session(database_engine) as session:
        assert session.get(SceneRecord, scene["scene_id"]).scene_json == stored


@pytest.mark.parametrize("wall", ["south", "north", "east", "west"])
@pytest.mark.parametrize("paired", [False, True])
def test_vision_anchor_uses_window_basis_columns(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, wall: str, paired: bool
) -> None:
    scene = make_scene(client, wall, paired)
    captured = []

    def capture(self: Any, **kwargs: Any) -> Any:
        captured.append(kwargs["T_ENU_marker"])
        raise ValueError("captured anchor")

    monkeypatch.setattr(VisionRegistrationPipeline, "run", capture)
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
                "corners_px": [[550.0, 270.0], [730.0, 270.0], [730.0, 450.0], [550.0, 450.0]],
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
    assert result.status_code == 422 and "captured anchor" in result.text
    matrix = np.array(captured[0]).reshape(4, 4)
    window = scene["windows"][0]
    for col, name in enumerate(["u_axis", "v_axis", "inward_normal"]):
        assert matrix[:3, col] == pytest.approx(window[name])
    origin = np.array(window["vertices_m"][0]) + 0.5 * (
        np.array(window["u_axis"]) + np.array(window["v_axis"])
    )
    assert matrix[:3, 3] == pytest.approx(origin)
