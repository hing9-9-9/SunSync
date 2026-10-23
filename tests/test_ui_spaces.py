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
    assert client.get(f"/api/ui/spaces/{saved['id']}").json()["windowDimensions"] == saved["windowDimensions"]


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
    assert saved["windowDimensions"] == window
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
    assert client.post("/api/ui/handoffs/claim", json={"code": data["code"]}).json()["status"] == "connected"
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
    assert client.post("/api/ui/measurements", json={"space_id": saved["id"]}).json()["token"] == job["token"]
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
    assert client.post("/api/ui/measurements", json={"space_id": saved["id"]}).json()["token"] != token


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
