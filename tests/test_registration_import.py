"""Exercise real pose recovery, FR-06 import, validation and persisted retrieval."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from app.main import create_app
from app.schemas.scene import Scene
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlmodel import create_engine
from sunsync_registration.core import measure
from sunsync_registration.fr06 import export_fragment
from sunsync_registration.models import MeasureRequest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "registration_fixture", ROOT / "measure/registration-core/examples/build_synthetic_example.py"
)
assert spec is not None and spec.loader is not None
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def measured_fragment(furniture_id="desk_01", mode="surface_is_top", floating=False):
    request = example.fixture().model_dump()
    request["plan"].update(space_id="room_001", furniture_id=furniture_id, height_mode=mode)
    request["alignment"]["T_enu_board"][3] = 2.0
    request["alignment"]["T_enu_board"][7] = 3.0
    if mode == "manual":
        request["plan"]["manual_height_m"] = 0.5 if floating else 1.0
    if floating:
        request["plan"].update(floor_supported=False, manual_base_height_m=0.4)
    if mode == "capture_highest":
        for sample in range(3):
            capture = copy.deepcopy(request["captures"][sample])
            capture.update(capture_id=f"H1-{sample}", point_id="H1")
            # The synthetic highest point is on the tabletop; only its height matters.
            request["captures"].append(capture)
    return export_fragment(measure(MeasureRequest.model_validate(request)))


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'registration.db'}", connect_args={"check_same_thread": False}
    )
    with TestClient(create_app(engine)) as result:
        payload = json.loads((ROOT / "docs/examples/fr06_manual_room.json").read_text("utf-8"))
        response = result.post("/api/scenes", json=payload)
        assert response.status_code == 201, response.text
        yield result
    engine.dispose()


def submit(client, fragment, revision=1):
    return client.post(
        "/api/scenes/scene_manual_room/registrations",
        json={"expected_revision": revision, "fragment": fragment},
    )


@pytest.mark.parametrize(
    ("mode", "floating"),
    [("surface_is_top", False), ("manual", False), ("manual", True), ("capture_highest", False)],
)
def test_measure_import_store_and_validate(client, mode, floating):
    before = client.get("/api/scenes/scene_manual_room").json()
    fragment = measured_fragment(mode=mode, floating=floating)
    response = submit(client, fragment)
    assert response.status_code == 200, response.text
    saved = client.get("/api/scenes/scene_manual_room").json()
    assert saved == response.json()
    Scene.model_validate(saved)
    Draft202012Validator(Scene.model_json_schema()).validate(saved)
    assert client.post("/api/scenes/validate", json=saved).status_code == 200
    assert saved["revision"] == 2
    assert saved["room"] == before["room"]
    assert saved["site"] == before["site"]
    assert saved["occluders"][0]["dimensions_m"] == pytest.approx(
        fragment["occluders"][0]["dimensions_m"]
    )
    assert saved["observations"][-1]["captured_at"] is not None
    assert saved["observations"][-1]["model_id"] == "catalog/desk_normalized"
    assert saved["occluders"][0]["type"] == "desk"
    saved["scene_id"] = "scene_reimported"
    response = client.post("/api/scenes", json=saved)
    assert response.status_code == 201, response.text


def test_multiple_furniture_reuse_board_and_revision_conflict(client):
    assert submit(client, measured_fragment()).status_code == 200
    second = measured_fragment("desk_02")
    assert submit(client, second).status_code == 409
    response = submit(client, second, revision=2)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert len(saved["markers"]) == 1
    assert len(saved["occluders"]) == 2
    assert saved["revision"] == 3
    assert submit(client, second, revision=3).status_code == 422
    assert client.get("/api/scenes/scene_manual_room").json() == saved


@pytest.mark.parametrize(
    "change",
    ["missing_time", "naive_time", "room", "transform", "height", "width", "yaw", "point", "shape"],
)
def test_invalid_registration_never_changes_saved_scene(client, change):
    before = client.get("/api/scenes/scene_manual_room").json()
    fragment = measured_fragment()
    obs = fragment["observations"][0]
    obj = fragment["occluders"][0]
    if change == "missing_time":
        obs["captured_at"] = None
    elif change == "naive_time":
        obs["captured_at"] = "2026-11-04T10:00:00"
    elif change == "room":
        obj["room_id"] = "different_room"
    elif change == "transform":
        fragment["markers"][0]["T_ENU_marker"][3] += 0.5
    elif change == "height":
        obj["dimensions_m"][2] += 0.5
    elif change == "width":
        obj["dimensions_m"][0] += 0.5
    elif change == "yaw":
        obj["yaw_deg_ccw_from_east"] = 90.0
    elif change == "point":
        obs["points_enu_m"][0][2] += 0.5
    else:
        obs["tabletop_shape"] = "circle"
    response = submit(client, fragment)
    assert response.status_code == 422, response.text
    assert client.get("/api/scenes/scene_manual_room").json() == before


def test_export_requires_real_capture_times_and_rectangle():
    request = example.fixture().model_dump()
    request["captures"][0]["captured_at"] = None
    with pytest.raises(ValueError, match="capture timestamps"):
        export_fragment(measure(MeasureRequest.model_validate(request)))
    measurement = measure(example.fixture())
    measurement["shape"]["kind"] = "circle"
    with pytest.raises(ValueError, match="rectangle"):
        export_fragment(measurement)


def test_existing_marker_cannot_be_relocated(client):
    assert submit(client, measured_fragment()).status_code == 200
    before = client.get("/api/scenes/scene_manual_room").json()
    second = measured_fragment("desk_02")
    second["markers"][0]["T_ENU_marker"][3] += 0.5
    assert submit(client, second, revision=2).status_code == 422
    assert client.get("/api/scenes/scene_manual_room").json() == before


def test_registration_preserves_existing_windows_weather_policy_and_scenario(client):
    payload = json.loads((ROOT / "docs/examples/fr06_scene_example.json").read_text("utf-8"))
    response = client.post("/api/scenes", json=payload)
    assert response.status_code == 201, response.text
    before = response.json()
    response = client.post(
        "/api/scenes/scene_demo_001/registrations",
        json={"expected_revision": 2, "fragment": measured_fragment("additional_desk")},
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    for key in ("room", "site", "windows", "weather", "policy", "protected_regions"):
        assert saved[key] == before[key]
    assert saved["scenario"]["scene_revision"] == 3
    expected_scenario = {**before["scenario"], "scene_revision": 3}
    assert saved["scenario"] == expected_scenario
    assert len(saved["occluders"]) == len(before["occluders"]) + 1


def test_unconfirmed_north_and_missing_height_provenance_are_rejected(client):
    before = client.get("/api/scenes/scene_manual_room").json()
    changed = copy.deepcopy(before)
    changed["revision"] = 2
    changed["north_alignment"]["confirmed"] = False
    assert client.put("/api/scenes/scene_manual_room", json=changed).status_code == 200
    assert submit(client, measured_fragment(), revision=2).status_code == 422
    fragment = measured_fragment()
    fragment["observations"][0]["derived_fields"].remove("desk_01.dimensions_m[2]")
    assert submit(client, fragment, revision=2).status_code == 422
    assert client.get("/api/scenes/scene_manual_room").json()["revision"] == 2
