"""UI/FR06 integration against an isolated database."""

import copy

import pytest
from app.main import create_app
from fastapi.testclient import TestClient
from sqlmodel import create_engine


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ui.db'}", connect_args={"check_same_thread": False}
    )
    with TestClient(create_app(engine)) as client:
        yield client
    engine.dispose()


@pytest.fixture
def payload():
    return {
        "id": "test-space",
        "building": "테스트 건물",
        "floor": "2층",
        "name": "서재",
        "width": 6,
        "depth": 5,
        "height": 3,
        "zone": "구역 1",
        "zones": ["구역 1"],
        "source": "sample",
        "revision": 1,
        "site": {
            "latitude": 37.5,
            "longitude": 127,
            "timezone": "Asia/Seoul",
            "northConfirmed": False,
        },
        "furniture": [
            {
                "id": "f1",
                "name": "책장",
                "zone": "구역 1",
                "x": 1,
                "y": 1,
                "width": 2,
                "depth": 1,
                "height": 2,
            }
        ],
        "regions": [
            {
                "id": "r1",
                "name": "보호면",
                "zone": "구역 1",
                "furnitureId": "f1",
                "x": 1,
                "y": 1,
                "width": 2,
                "depth": 1,
                "face": "남쪽 전면",
                "bottom": 0.2,
                "top": 2,
                "grade": "B",
            }
        ],
    }


@pytest.mark.parametrize(
    "face,normal",
    [
        ("남쪽 전면", [0, -1, 0]),
        ("북쪽 후면", [0, 1, 0]),
        ("동쪽 측면", [1, 0, 0]),
        ("서쪽 측면", [-1, 0, 0]),
        ("상단", [0, 0, 1]),
    ],
)
def test_roundtrip_and_enu_faces(client, payload, face, normal):
    payload["regions"][0]["face"] = face
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["serverRevision"] == 1
    assert saved["building"] == payload["building"]
    assert client.get("/api/ui/spaces/test-space").json() == saved
    assert client.get("/api/ui/spaces").json()["spaces"] == [saved]
    scene = client.get(f"/api/scenes/{saved['serverSceneId']}").json()
    assert scene["occluders"][0]["base_center_m"] == [2, 3.5, 0]
    assert scene["protected_regions"][0]["receiving_normal"] == normal
    assert scene["windows"] == []
    assert scene["data_kind"] == "synthetic_fixture"
    assert scene["observations"][0]["method"] == "manual"
    assert scene["north_alignment"]["confirmed"] is False


def test_update_and_stale_write_preserve_latest(client, payload):
    saved = client.post("/api/ui/spaces", json=payload).json()
    changed = copy.deepcopy(saved)
    changed["building"] = "변경 건물"
    changed["furniture"][0]["height"] = 2.5
    updated = client.put("/api/ui/spaces/test-space", json=changed)
    assert updated.status_code == 200, updated.text
    assert updated.json()["serverRevision"] == 2
    assert client.put("/api/ui/spaces/test-space", json=saved).status_code == 409
    assert client.get("/api/ui/spaces/test-space").json() == updated.json()
    scene = client.get(f"/api/scenes/{saved['serverSceneId']}").json()
    assert scene["revision"] == 2
    assert scene["occluders"][0]["dimensions_m"][2] == 2.5


def test_external_scene_change_is_never_overwritten(client, payload):
    saved = client.post("/api/ui/spaces", json=payload).json()
    scene_url = f"/api/scenes/{saved['serverSceneId']}"
    scene = client.get(scene_url).json()
    scene["revision"] += 1
    scene["room"]["name"] = "외부 수정"
    assert client.put(scene_url, json=scene).status_code == 200
    assert client.get("/api/ui/spaces/test-space").status_code == 409
    assert client.put("/api/ui/spaces/test-space", json=saved).status_code == 409
    listing = client.get("/api/ui/spaces").json()
    assert listing["spaces"] == []
    assert listing["unavailable"][0]["name"] == "외부 수정"
    assert client.get(scene_url).json()["room"]["name"] == "외부 수정"


@pytest.mark.parametrize("bad", ["site", "geometry", "top"])
def test_invalid_payload_does_not_partially_save(client, payload, bad):
    if bad == "site":
        del payload["site"]
    elif bad == "geometry":
        payload["furniture"][0]["width"] = 99
    else:
        payload["regions"][0].update(face="상단", top=1)
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 422, response.text
    assert client.get("/api/scenes").json() == []
    assert client.get("/api/ui/spaces").json()["spaces"] == []


def test_duplicate_create_is_atomic(client, payload):
    assert client.post("/api/ui/spaces", json=payload).status_code == 201
    assert client.post("/api/ui/spaces", json=payload).status_code == 409
    assert len(client.get("/api/scenes").json()) == 1
    assert len(client.get("/api/ui/spaces").json()["spaces"]) == 1


def test_window_dimensions_round_trip(client, payload):
    payload["windowDimensions"] = {"width": 4, "height": 2}
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201
    saved = response.json()
    assert saved["windowDimensions"]["width"] == 4
    assert saved["windowDimensions"]["height"] == 2
    assert (
        client.get(f"/api/ui/spaces/{saved['id']}").json()["windowDimensions"]
        == saved["windowDimensions"]
    )


def test_window_position_marker_and_furniture_type_round_trip(client, payload):
    window = {
        "width": 4,
        "height": 2,
        "wall": "east",
        "offset": 0.5,
        "sill": 0.6,
        "marker": {"size": 0.3, "x": 2, "y": 1},
    }
    payload["windowDimensions"] = window
    payload["furniture"][0]["type"] = "bookcase"
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    saved = client.get(f"/api/ui/spaces/{response.json()['id']}").json()
    assert saved["windowDimensions"] == {**window, "captureModule": None}
    assert saved["furniture"][0]["type"] == "bookcase"


@pytest.mark.parametrize(
    "window",
    [
        {"width": 4, "height": 2, "wall": "south", "offset": 99},
        {"width": 4, "height": 2, "sill": 99},
        {"width": 4, "height": 2, "marker": {"size": 0.4, "x": 0.1, "y": 1}},
        {"width": 4, "height": 2, "wall": "up"},
    ],
)
def test_window_outside_wall_or_marker_outside_window_is_rejected(client, payload, window):
    payload["windowDimensions"] = window
    assert client.post("/api/ui/spaces", json=payload).status_code == 422


def test_window_saved_before_position_fields_stays_readable(client, payload):
    payload["windowDimensions"] = {"width": 9, "height": 2}
    assert client.post("/api/ui/spaces", json=payload).status_code == 201
    assert client.get("/api/ui/spaces").status_code == 200


def test_unknown_furniture_type_is_rejected(client, payload):
    payload["furniture"][0]["type"] = "sofa"
    assert client.post("/api/ui/spaces", json=payload).status_code == 422


@pytest.mark.parametrize("size", [0, -1])
def test_window_dimensions_reject_invalid_size(client, payload, size):
    payload["windowDimensions"] = {"width": size, "height": 2}
    assert client.post("/api/ui/spaces", json=payload).status_code == 422


def test_device_handoff_round_trip(client, payload):
    saved = client.post("/api/ui/spaces", json=payload).json()
    handoff = client.post("/api/ui/handoffs", json={"space_id": saved["id"]})
    assert handoff.status_code == 201
    data = handoff.json()
    token = data["token"]
    assert client.get(f"/api/ui/handoffs/{token}").json()["status"] == "waiting"
    assert (
        client.post("/api/ui/handoffs/claim", json={"code": data["code"]}).json()["status"]
        == "connected"
    )
    assert client.post(f"/api/ui/handoffs/{token}/connect").json()["space"]["id"] == saved["id"]
    saved["furniture"][0]["x"] = 2
    saved["regions"][0]["x"] = 2
    completed = client.post(f"/api/ui/handoffs/{token}/complete", json=saved)
    assert completed.status_code == 200
    result = client.get(f"/api/ui/handoffs/{token}").json()
    assert result["status"] == "completed"
    assert result["space"]["furniture"][0]["x"] == 2
    assert client.post(f"/api/ui/handoffs/{token}/complete", json=saved).status_code == 409


def test_handoff_rejects_wrong_space_expiry_and_stale_result(client, payload):
    saved = client.post("/api/ui/spaces", json=payload).json()
    token = client.post("/api/ui/handoffs", json={"space_id": saved["id"]}).json()["token"]
    assert client.post(f"/api/ui/handoffs/{token}/complete", json=saved).status_code == 409
    client.post(f"/api/ui/handoffs/{token}/connect")
    wrong = {**saved, "id": "wrong"}
    assert client.post(f"/api/ui/handoffs/{token}/complete", json=wrong).status_code == 409
    assert client.put(f"/api/ui/spaces/{saved['id']}", json=saved).status_code == 200
    assert client.post(f"/api/ui/handoffs/{token}/complete", json=saved).status_code == 409
    assert client.get(f"/api/ui/handoffs/{token}").json()["status"] == "connected"
    assert client.post("/api/ui/handoffs/claim", json={"code": "NOTACODE"}).status_code == 404


def test_expired_handoff_cannot_connect_or_save(client, payload, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.api import handoffs

    saved = client.post("/api/ui/spaces", json=payload).json()
    data = client.post("/api/ui/handoffs", json={"space_id": saved["id"]}).json()
    future = datetime.now(UTC) + timedelta(hours=1)

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(handoffs, "datetime", FutureClock)
    assert client.get(f"/api/ui/handoffs/{data['token']}").status_code == 410
    assert client.post("/api/ui/handoffs/claim", json={"code": data["code"]}).status_code == 410
    assert client.post(f"/api/ui/handoffs/{data['token']}/complete", json=saved).status_code == 410


def test_measurement_jobs_sync_without_connection_code(client, payload):
    saved = client.post("/api/ui/spaces", json=payload).json()
    first = client.post("/api/ui/measurements", json={"space_id": saved["id"]})
    assert first.status_code == 201
    job = first.json()
    assert "code" not in job
    assert job["status"] == "waiting"
    assert (
        client.post("/api/ui/measurements", json={"space_id": saved["id"]}).json()["token"]
        == job["token"]
    )
    assert client.get("/api/ui/measurements").json()[0]["token"] == job["token"]
    token = job["token"]
    assert client.post(f"/api/ui/measurements/{token}/complete", json=saved).status_code == 409
    assert client.post(f"/api/ui/measurements/{token}/connect").json()["status"] == "connected"
    saved["name"] = "측정 완료 공간"
    assert client.post(f"/api/ui/measurements/{token}/complete", json=saved).status_code == 200
    result = client.get(f"/api/ui/measurements/{token}").json()
    assert result["status"] == "completed"
    assert result["space"]["name"] == "측정 완료 공간"
    assert client.get("/api/ui/measurements").json()[0]["status"] == "completed"
    assert client.post(f"/api/ui/measurements/{token}/connect").status_code == 409
    assert (
        client.post("/api/ui/measurements", json={"space_id": saved["id"]}).json()["token"] != token
    )


def test_standalone_protection_area_survives_server_save(client, payload):
    payload["furniture"] = []
    payload["regions"][0]["furnitureId"] = None
    payload["regions"][0]["face"] = "상단"
    saved = client.post("/api/ui/spaces", json=payload)
    assert saved.status_code == 201
    result = client.get(f"/api/ui/spaces/{payload['id']}").json()
    assert result["furniture"] == []
    assert result["regions"][0]["furnitureId"] is None
    assert result["regions"][0]["grade"] == payload["regions"][0]["grade"]


def test_user_shading_schedule_round_trip(client, payload):
    payload["shadingSchedule"] = {"enabled": True, "start": "22:30", "end": "06:00"}
    created = client.post("/api/ui/spaces", json=payload)
    assert created.status_code == 201, created.text
    saved = client.get("/api/ui/spaces/test-space").json()
    assert saved["shadingSchedule"] == payload["shadingSchedule"]
    saved["shadingSchedule"] = {"enabled": False, "start": "09:00", "end": "18:00"}
    saved["revision"] += 1
    updated = client.put("/api/ui/spaces/test-space", json=saved)
    assert updated.status_code == 200, updated.text
    assert updated.json()["shadingSchedule"]["enabled"] is False


@pytest.mark.parametrize("start,end", [("25:00", "18:00"), ("09:00", "09:00"), ("9:00", "18:00")])
def test_invalid_shading_schedule_is_rejected(client, payload, start, end):
    payload["shadingSchedule"] = {"enabled": True, "start": start, "end": end}
    assert client.post("/api/ui/spaces", json=payload).status_code == 422


def test_region_own_shading_time_round_trip(client, payload):
    payload["shadingSchedule"] = {"enabled": True, "start": "12:00", "end": "15:00"}
    payload["regions"][0]["shadingSchedule"] = {"enabled": True, "start": "11:00", "end": "16:00"}
    assert client.post("/api/ui/spaces", json=payload).status_code == 201
    saved = client.get("/api/ui/spaces/test-space").json()
    assert saved["regions"][0]["shadingSchedule"] == {"enabled": True, "start": "11:00", "end": "16:00"}
    del saved["regions"][0]["shadingSchedule"]
    saved["revision"] += 1
    updated = client.put("/api/ui/spaces/test-space", json=saved)
    assert updated.status_code == 200, updated.text
    assert "shadingSchedule" not in updated.json()["regions"][0] or updated.json()["regions"][0]["shadingSchedule"] is None


def test_invalid_region_shading_time_is_rejected(client, payload):
    payload["regions"][0]["shadingSchedule"] = {"enabled": True, "start": "09:00", "end": "09:00"}
    assert client.post("/api/ui/spaces", json=payload).status_code == 422


def test_delete_removes_space_and_blocks_reupload(client, payload):
    assert client.post("/api/ui/spaces", json=payload).status_code == 201
    assert client.delete("/api/ui/spaces/test-space").status_code == 204
    listed = client.get("/api/ui/spaces").json()
    assert listed["spaces"] == []
    assert listed["deleted"] == ["test-space"]
    assert client.get("/api/scenes").json() == []
    assert client.get("/api/ui/spaces/test-space").status_code == 404
    # A copy left on another device must not come back.
    assert client.post("/api/ui/spaces", json=payload).status_code == 409
    assert client.delete("/api/ui/spaces/test-space").status_code == 204


@pytest.mark.parametrize(
    "wall,normal,azimuth",
    [
        ("south", [0, 1, 0], 180),
        ("north", [0, -1, 0], 0),
        ("east", [-1, 0, 0], 90),
        ("west", [1, 0, 0], 270),
    ],
)
def test_positioned_window_is_shared_with_fr01(client, payload, wall, normal, azimuth):
    payload["windowDimensions"] = {
        "width": 2,
        "height": 1.5,
        "wall": wall,
        "offset": 1,
        "sill": 0.5,
    }
    payload["furniture"][0]["type"] = "bookcase"
    saved = client.post("/api/ui/spaces", json=payload)
    assert saved.status_code == 201, saved.text
    scene = client.get(f"/api/scenes/{saved.json()['serverSceneId']}").json()
    window = scene["windows"][0]
    assert window["inward_normal"] == normal
    assert window["azimuth_deg_true"] == azimuth
    assert (window["film_rows"], window["film_cols"]) == (15, 20)
    assert scene["room"]["floor_level"] == 2
    assert scene["occluders"][0]["type"] == "bookshelf"
    assert "sample_points_m" not in scene["protected_regions"][0]


def test_ui_save_predict_edit_and_stale_prediction(client, payload):
    payload["site"]["northConfirmed"] = True
    payload["windowDimensions"] = {
        "width": 4,
        "height": 2.5,
        "wall": "south",
        "offset": 1,
        "sill": 0,
    }
    created = client.post("/api/ui/spaces", json=payload)
    assert created.status_code == 201, created.text
    saved = created.json()
    url = f"/api/scenes/{saved['serverSceneId']}/sunpath-predictions"
    request = {
        "start_time": "2026-11-01T10:00:00+09:00",
        "duration_minutes": 120,
        "interval_seconds": 1800,
        "expected_scene_revision": 1,
    }
    result = client.post(url, json=request)
    assert result.status_code == 200, result.text
    prediction = result.json()
    assert any(t["status"] == "exposed" for t in prediction["regions"][0]["timeline"])
    assert len(prediction["irradiance"]) == len(prediction["timestamps"])
    saved["name"] = "수정한 서재"
    updated = client.put("/api/ui/spaces/test-space", json=saved)
    assert updated.status_code == 200, updated.text
    assert client.post(url, json=request).status_code == 409
    request["expected_scene_revision"] = 2
    assert client.post(url, json=request).status_code == 200


def test_saved_ui_space_runs_fr02_and_rejects_old_revision(client, payload):
    payload["site"]["northConfirmed"] = True
    payload["windowDimensions"] = {
        "width": 4,
        "height": 2.5,
        "wall": "south",
        "offset": 1,
        "sill": 0,
    }
    saved_response = client.post("/api/ui/spaces", json=payload)
    assert saved_response.status_code == 201, saved_response.text
    saved = saved_response.json()
    url = f"/api/scenes/{saved['serverSceneId']}/shading-plans"
    request = {
        "start_time": "2026-11-02T00:00:00+09:00",
        "duration_minutes": 1440,
        "interval_seconds": 1800,
        "expected_scene_revision": 1,
    }
    response = client.post(url, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scene_revision"] == 1
    assert any(
        s["state"] == "opaque" for w in result["windows"] for c in w["cells"] for s in c["segments"]
    )
    assert client.put("/api/ui/spaces/test-space", json=saved).status_code == 200
    assert client.post(url, json=request).status_code == 409


def test_ui_save_keeps_registration_calibration(client, payload):
    """A web save must not erase the board/ENU calibration that furniture registration needs."""
    from app.api.ui_spaces import keep_paired_markers
    from app.schemas.scene import RegistrationCalibration, Scene
    from app.ui_scene import UISpace, to_scene

    calibration = RegistrationCalibration.model_validate(
        {
            "calibration_id": "a" * 64,
            "marker": {
                "marker_id": "board_m",
                "family": "aruco",
                "dictionary": "DICT_4X4_50",
                "code_id": 7,
                "side_length_m": 0.24,
                "plane": "vertical",
                "T_ENU_marker": [1, 0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 1.2, 0, 0, 0, 1],
            },
        }
    )
    space = UISpace.model_validate(payload)
    previous = to_scene(space, 1).model_copy(update={"registration_calibration": calibration})
    renamed = UISpace.model_validate({**payload, "name": "이름 변경"})
    kept = keep_paired_markers(to_scene(renamed, 2), previous)
    assert isinstance(kept, Scene)
    assert kept.registration_calibration == calibration
    assert kept.room.name == "이름 변경"


@pytest.mark.parametrize(
    "wall,face,normal",
    [
        ("east", "남쪽 전면", [1, 0, 0]),
        ("north", "서쪽 측면", [0, 1, 0]),
        ("west", "상단", [0, 0, 1]),
    ],
)
def test_furniture_protects_the_side_facing_the_window(client, payload, wall, face, normal):
    # People never pick a face: a furniture side region is planned on the side toward the window;
    # a top region stays the top. The saved UI value itself is left as sent.
    payload["windowDimensions"] = {
        "width": 2,
        "height": 1.5,
        "wall": wall,
        "offset": 0.5,
        "sill": 0.6,
    }
    payload["regions"][0]["face"] = face
    if face == "상단":
        payload["regions"][0]["bottom"] = 1.9
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    saved = response.json()
    scene = client.get(f"/api/scenes/{saved['serverSceneId']}").json()
    assert scene["protected_regions"][0]["receiving_normal"] == normal
    assert saved["regions"][0]["face"] == face


def test_overlapping_furniture_in_same_zone_is_rejected_with_korean_names(client, payload):
    payload["width"] = 8.0
    payload["depth"] = 6.0
    payload["height"] = 3.0
    payload["furniture"] = [
        {
            "id": "table-1",
            "name": "테이블",
            "zone": "구역 1",
            "type": "desk",
            "x": 5.8,
            "y": 2.5,
            "width": 1.6,
            "depth": 0.8,
            "height": 0.75,
        },
        {
            "id": "case-1",
            "name": "진열장",
            "zone": "구역 1",
            "type": "display",
            "x": 6.8,
            "y": 3.0,
            "width": 1.0,
            "depth": 0.5,
            "height": 1.5,
        },
    ]
    payload["regions"] = []
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "테이블" in detail
    assert "진열장" in detail
    assert "겹칩니다" in detail


def test_touching_furniture_and_different_zone_furniture_are_allowed(client, payload):
    payload["zones"] = ["구역 1", "구역 2"]
    payload["furniture"] = [
        {
            "id": "f1",
            "name": "책장 1",
            "zone": "구역 1",
            "type": "bookcase",
            "x": 1.0,
            "y": 1.0,
            "width": 2.0,
            "depth": 0.8,
            "height": 1.8,
        },
        {
            "id": "f2",
            "name": "책장 2",
            "zone": "구역 1",
            "type": "bookcase",
            "x": 3.0,
            "y": 1.0,
            "width": 1.0,
            "depth": 0.8,
            "height": 1.8,
        },
        {
            "id": "f3",
            "name": "구역2 책장",
            "zone": "구역 2",
            "type": "bookcase",
            "x": 1.0,
            "y": 1.0,
            "width": 2.0,
            "depth": 0.8,
            "height": 1.8,
        },
    ]
    payload["regions"] = []
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text


def test_already_saved_overlapping_space_remains_readable_and_can_be_repaired(
    client, payload, tmp_path
):
    created = client.post("/api/ui/spaces", json=payload)
    assert created.status_code == 201, created.text
    saved = created.json()

    overlapping = {
        **saved,
        "furniture": [
            {
                "id": "f1",
                "name": "테이블",
                "zone": "구역 1",
                "type": "desk",
                "x": 1.0,
                "y": 1.0,
                "width": 2.0,
                "depth": 1.0,
                "height": 0.8,
            },
            {
                "id": "f2",
                "name": "진열장",
                "zone": "구역 1",
                "type": "display",
                "x": 2.0,
                "y": 1.2,
                "width": 1.0,
                "depth": 0.8,
                "height": 1.5,
            },
        ],
        "regions": [],
    }
    # Update fails while overlapping
    rejected = client.put(f"/api/ui/spaces/{saved['id']}", json=overlapping)
    assert rejected.status_code == 422
    assert "테이블" in str(rejected.json()["detail"])
    assert "진열장" in str(rejected.json()["detail"])

    # Simulate a legacy space already stored in the DB with overlapping furniture
    import json

    from app.models import UISpaceRecord
    from app.ui_scene import UISpace, furniture_overlap_message
    from sqlmodel import Session

    legacy_model = UISpace.model_validate(overlapping)
    assert furniture_overlap_message(legacy_model) is not None
    db_engine = create_engine(f"sqlite:///{tmp_path / 'ui.db'}")
    with Session(db_engine) as session:
        record = session.get(UISpaceRecord, saved["id"])
        assert record is not None
        record.payload_json = json.dumps(overlapping, ensure_ascii=False)
        session.add(record)
        session.commit()
    db_engine.dispose()

    listed = client.get("/api/ui/spaces")
    assert listed.status_code == 200
    assert len(listed.json()["spaces"]) == 1
    assert listed.json()["unavailable"] == []

    fetched = client.get(f"/api/ui/spaces/{saved['id']}")
    assert fetched.status_code == 200
    assert len(fetched.json()["furniture"]) == 2

    # Moving the second furniture out of overlap allows saving again
    repaired = {
        **overlapping,
        "furniture": [
            overlapping["furniture"][0],
            {**overlapping["furniture"][1], "x": 3.2},
        ],
    }
    updated = client.put(f"/api/ui/spaces/{saved['id']}", json=repaired)
    assert updated.status_code == 200, updated.text


def test_handoff_and_measurement_complete_reject_overlapping_furniture(client, payload):
    created = client.post("/api/ui/spaces", json=payload)
    assert created.status_code == 201
    saved = created.json()

    # 1. Handoff complete with overlapping furniture
    handoff = client.post("/api/ui/handoffs", json={"space_id": saved["id"]}).json()
    client.post(f"/api/ui/handoffs/{handoff['token']}/connect")
    overlapping = {
        **saved,
        "furniture": [
            {
                "id": "f1",
                "name": "책상 A",
                "zone": "구역 1",
                "type": "desk",
                "x": 1.0,
                "y": 1.0,
                "width": 2.0,
                "depth": 1.0,
                "height": 0.8,
            },
            {
                "id": "f2",
                "name": "책상 B",
                "zone": "구역 1",
                "type": "desk",
                "x": 1.5,
                "y": 1.2,
                "width": 1.5,
                "depth": 1.0,
                "height": 0.8,
            },
        ],
    }
    rejected_handoff = client.post(
        f"/api/ui/handoffs/{handoff['token']}/complete", json=overlapping
    )
    assert rejected_handoff.status_code == 422
    detail_h = str(rejected_handoff.json()["detail"])
    assert "책상 A" in detail_h and "책상 B" in detail_h and "겹칩니다" in detail_h
    # Handoff session remains connected (not completed)
    assert client.get(f"/api/ui/handoffs/{handoff['token']}").json()["status"] == "connected"

    # 2. Measurement job complete with overlapping furniture
    job = client.post("/api/ui/measurements", json={"space_id": saved["id"]}).json()
    client.post(f"/api/ui/measurements/{job['token']}/connect")
    rejected_job = client.post(f"/api/ui/measurements/{job['token']}/complete", json=overlapping)
    assert rejected_job.status_code == 422
    detail_m = str(rejected_job.json()["detail"])
    assert "책상 A" in detail_m and "책상 B" in detail_m and "겹칩니다" in detail_m
    # Measurement job remains connected (not completed)
    assert client.get(f"/api/ui/measurements/{job['token']}").json()["status"] == "connected"


@pytest.mark.parametrize("yaw", [0, 30, 90, 215])
def test_rotated_furniture_dimensions_and_protection_survive_ui_save(client, payload, yaw):
    payload["furniture"][0]["yaw"] = yaw
    created = client.post("/api/ui/spaces", json=payload)
    assert created.status_code == 201, created.text
    scene_id = created.json()["serverSceneId"]
    before = client.get(f"/api/scenes/{scene_id}").json()
    assert before["occluders"][0]["yaw_deg_ccw_from_east"] == yaw
    assert before["occluders"][0]["dimensions_m"] == [2, 1, 2]
    changed = created.json()
    changed["name"] = "Renamed"
    saved = client.put("/api/ui/spaces/test-space", json=changed)
    assert saved.status_code == 200, saved.text
    after = client.get(f"/api/scenes/{scene_id}").json()
    assert after["occluders"][0]["base_center_m"] == before["occluders"][0]["base_center_m"]
    assert after["occluders"][0]["dimensions_m"] == [2, 1, 2]
    assert after["occluders"][0]["yaw_deg_ccw_from_east"] == yaw


def test_rotated_outside_room_and_overlap_are_rejected(client, payload):
    payload["regions"] = []
    payload["furniture"][0].update(x=0, y=0, yaw=45)
    assert client.post("/api/ui/spaces", json=payload).status_code == 422
    payload["furniture"][0].update(x=1, y=1)
    payload["furniture"].append({**payload["furniture"][0], "id": "f2", "yaw": -45})
    assert client.post("/api/ui/spaces", json=payload).status_code == 422


def test_rotated_host_near_wall_uses_physical_bounds(client, payload):
    payload["furniture"][0].update(x=-0.7, y=2, width=2, depth=0.2, yaw=90)
    payload["regions"][0].update(x=-0.7, y=2, width=2, depth=0.2, face="상단")
    result = client.post("/api/ui/spaces", json=payload)
    assert result.status_code == 201, result.text


def test_furniture_top_is_protected_with_the_side_facing_the_window(client, payload):
    payload["windowDimensions"] = {"width": 2, "height": 1.5, "wall": "east", "offset": 0.5, "sill": 0.6}
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    scene = client.get(f"/api/scenes/{response.json()['serverSceneId']}").json()
    side, top = scene["protected_regions"]
    assert side["receiving_normal"] == [1, 0, 0]
    assert top["receiving_normal"] == [0, 0, 1]
    assert {v[2] for v in top["vertices_m"]} == {2}, "the top lies at the furniture height"
    assert top["grade"] == side["grade"] == "B"
    assert top["host_occluder_id"] == side["host_occluder_id"]
    assert top["region_id"] != side["region_id"]
    assert top["name"] == "보호면 윗면"


def test_grade_d_and_top_regions_get_no_second_top(client, payload):
    payload["regions"][0]["grade"] = "D"
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    scene = client.get(f"/api/scenes/{response.json()['serverSceneId']}").json()
    assert len(scene["protected_regions"]) == 1
    payload["id"] = "top-space"
    payload["regions"][0].update({"id": "r2", "face": "상단", "bottom": 1.9, "top": 2, "grade": "A"})
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    scene = client.get(f"/api/scenes/{response.json()['serverSceneId']}").json()
    assert len(scene["protected_regions"]) == 1


def test_region_without_furniture_has_no_top(client, payload):
    payload["regions"][0]["furnitureId"] = None
    response = client.post("/api/ui/spaces", json=payload)
    assert response.status_code == 201, response.text
    scene = client.get(f"/api/scenes/{response.json()['serverSceneId']}").json()
    assert len(scene["protected_regions"]) == 1


@pytest.mark.parametrize("wall", ["south", "east", "north", "west"])
@pytest.mark.parametrize("yaw", [0, 30, 90, 180, 270, 335])
def test_protected_face_points_at_the_window_wall_after_rotation(wall, yaw):
    from types import SimpleNamespace as NS

    from app.ui_scene import region_surface

    toward = {"south": (0, -1), "north": (0, 1), "east": (1, 0), "west": (-1, 0)}[wall]
    space = NS(
        depth=5,
        windowDimensions=NS(wall=wall),
        furniture=[NS(id="f", yaw=yaw, width=1.5, depth=0.6, x=1, y=1, baseHeight=0)],
    )
    region = NS(furnitureId="f", face="남쪽 전면", x=1, y=1, width=1.5, depth=0.6, bottom=0, top=1)
    _, normal = region_surface(space, region)
    assert normal[0] * toward[0] + normal[1] * toward[1] > 0.8
