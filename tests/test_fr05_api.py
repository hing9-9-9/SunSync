"""API integration tests for FR-05 Virtual Film REST protocol, Simulation Clock, and Multi-user PPT demo."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import create_engine

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def installed_film_payload(scene_payload: dict[str, Any]) -> dict[str, Any]:
    # The generic simulation fixture is 3m x 1.5m with arbitrary cells. Physical
    # marker pairing instead uses three complete 1m films with 10cm cells.
    window = scene_payload["windows"][0]
    window["vertices_m"][2][2] = window["vertices_m"][3][2] = 2.0
    window["film_rows"], window["film_cols"] = 10, 30
    return scene_payload


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    db_file = tmp_path / "test_film.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


def create_test_scene(client: TestClient, payload: dict[str, Any]) -> str:
    resp = client.post("/api/scenes", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["scene_id"]


def test_film_commands_and_state(client: TestClient, scene_payload: dict[str, Any]) -> None:
    scene_id = create_test_scene(client, scene_payload)

    # 1. Query initial state
    resp = client.get(f"/api/scenes/{scene_id}/film-state")
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["scene_id"] == scene_id
    assert len(state["windows"]) >= 1
    win0 = state["windows"][0]
    assert win0["marker_mode_active"] is False
    assert win0["cells"][0][0] == 0.0

    # 2. Out of bounds cell command returns 422
    invalid_cmd = {
        "window_id": win0["window_id"],
        "commands": [{"row": 999, "col": 0, "opacity": 1.0}],
    }
    resp = client.post(f"/api/scenes/{scene_id}/film-commands", json=invalid_cmd)
    assert resp.status_code == 422

    # 3. Valid batch commands
    valid_cmd = {
        "window_id": win0["window_id"],
        "commands": [
            {"row": 0, "col": 0, "state": "opaque"},
            {"row": 1, "col": 2, "opacity": 0.4},
        ],
        "timestamp": "2026-11-04T10:00:00Z",
        "note": "morning shading",
    }
    resp = client.post(f"/api/scenes/{scene_id}/film-commands", json=valid_cmd)
    assert resp.status_code == 200, resp.text
    cmd_res = resp.json()
    assert cmd_res["cells"][0][0] == 1.0
    assert cmd_res["states"][0][0] == "opaque"
    assert cmd_res["cells"][1][2] == 0.4
    assert cmd_res["states"][1][2] == "dimmed"

    # Query state again
    resp2 = client.get(f"/api/scenes/{scene_id}/film-state?window_id={win0['window_id']}")
    assert resp2.status_code == 200
    state2 = resp2.json()
    assert state2["windows"][0]["cells"][0][0] == 1.0
    assert state2["windows"][0]["cells"][1][2] == 0.4


def test_film_marker_mode(client: TestClient, scene_payload: dict[str, Any]) -> None:
    scene_id = create_test_scene(client, scene_payload)

    # First set a manual cell
    client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={"commands": [{"row": 0, "col": 0, "opacity": 0.7}]},
    )

    # Enable marker mode
    enable_req = {"enabled": True, "code_id": 10, "start_row": 2, "start_col": 2}
    resp = client.post(f"/api/scenes/{scene_id}/film-marker-mode", json=enable_req)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["marker_mode_active"] is True
    assert body["marker_code_id"] == 10
    # Outer border is 1.0
    assert body["cells"][2][2] == 1.0
    assert body["cells"][7][7] == 1.0
    # Outside cell preserved
    assert body["cells"][0][0] == 0.7

    # Disable marker mode restores previous cells
    disable_req = {"enabled": False}
    resp2 = client.post(f"/api/scenes/{scene_id}/film-marker-mode", json=disable_req)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["marker_mode_active"] is False
    assert body2["cells"][0][0] == 0.7
    # Cells at (2,2) restored to 0.0
    assert body2["cells"][2][2] == 0.0


def test_schedule_apply_and_simulation_clock(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    scene_id = create_test_scene(client, scene_payload)

    # Construct an explicit ShadingPlan with opaque, transparent, infeasible, and unknown segments
    plan = {
        "plan_version": "0.1.0",
        "scene_id": scene_id,
        "scene_revision": 1,
        "start_time": "2026-10-14T08:00:00Z",
        "end_time": "2026-10-14T12:00:00Z",
        "windows": [
            {
                "window_id": "window_001",
                "film_rows": 10,
                "film_cols": 10,
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "segments": [
                            {
                                "start_time": "2026-10-14T08:30:00Z",
                                "end_time": "2026-10-14T09:30:00Z",
                                "state": "opaque",
                                "reasons": [{"region_id": "region_desk", "grade": "A"}],
                            },
                            {
                                "start_time": "2026-10-14T09:30:00Z",
                                "end_time": "2026-10-14T10:30:00Z",
                                "state": "infeasible",
                                "reasons": [{"region_id": "region_desk", "grade": "B"}],
                            },
                        ],
                    },
                    {
                        "row": 1,
                        "col": 1,
                        "segments": [
                            {
                                "start_time": "2026-10-14T09:00:00Z",
                                "end_time": "2026-10-14T10:00:00Z",
                                "state": "unknown",
                                "reasons": [{"region_id": "region_desk", "grade": "C"}],
                            }
                        ],
                    },
                ],
            }
        ],
    }

    # Apply schedule
    apply_resp = client.post(
        f"/api/scenes/{scene_id}/film-schedule-apply",
        json={"plan": plan, "plan_id": "custom_plan_1"},
    )
    assert apply_resp.status_code == 200, apply_resp.text
    apply_body = apply_resp.json()
    assert apply_body["plan_id"] == "custom_plan_1"
    assert apply_body["target_unmet_count"] >= 1  # From infeasible segment

    # Verify Simulation Clock querying via GET ?at=...
    # 1. At 08:00:00Z (before any segment) -> both cells are 0.0 (transparent)
    r_before = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-10-14T08:00:00Z")
    assert r_before.status_code == 200
    w_before = r_before.json()["windows"][0]
    assert w_before["cells"][0][0] == 0.0
    assert w_before["cells"][1][1] == 0.0

    # 2. At 09:00:00Z: (0, 0) is opaque (1.0), (1, 1) is unknown (1.0)
    r_mid = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-10-14T09:00:00Z")
    assert r_mid.status_code == 200
    w_mid = r_mid.json()["windows"][0]
    assert w_mid["cells"][0][0] == 1.0
    assert w_mid["cells"][1][1] == 1.0

    # 3. At 10:15:00Z: (0, 0) is infeasible (1.0), (1, 1) segment ended (0.0)
    r_mid2 = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-10-14T10:15:00Z")
    assert r_mid2.status_code == 200
    w_mid2 = r_mid2.json()["windows"][0]
    assert w_mid2["cells"][0][0] == 1.0
    assert w_mid2["cells"][1][1] == 0.0

    # 4. At 11:30:00Z: all segments ended -> cells are 0.0
    r_after = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-10-14T11:30:00Z")
    assert r_after.status_code == 200
    w_after = r_after.json()["windows"][0]
    assert w_after["cells"][0][0] == 0.0
    assert w_after["cells"][1][1] == 0.0

    # Non-timezone-aware timestamp returns 422
    r_invalid_at = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-10-14T09:00:00")
    assert r_invalid_at.status_code == 422


def test_film_logs_and_target_unmet_notes(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    scene_id = create_test_scene(client, scene_payload)

    plan = {
        "plan_version": "0.1.0",
        "scene_id": scene_id,
        "scene_revision": 1,
        "start_time": "2026-10-14T08:00:00Z",
        "end_time": "2026-10-14T12:00:00Z",
        "windows": [
            {
                "window_id": "window_001",
                "film_rows": 10,
                "film_cols": 10,
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "segments": [
                            {
                                "start_time": "2026-10-14T09:00:00Z",
                                "end_time": "2026-10-14T10:00:00Z",
                                "state": "infeasible",
                                "reasons": [{"region_id": "region_desk", "grade": "B"}],
                            }
                        ],
                    },
                    {
                        "row": 1,
                        "col": 1,
                        "segments": [
                            {
                                "start_time": "2026-10-14T09:00:00Z",
                                "end_time": "2026-10-14T10:00:00Z",
                                "state": "unknown",
                                "reasons": [{"region_id": "region_desk", "grade": "C"}],
                            }
                        ],
                    },
                ],
            }
        ],
    }

    client.post(f"/api/scenes/{scene_id}/film-schedule-apply", json={"plan": plan})

    # Query logs
    logs_resp = client.get(f"/api/scenes/{scene_id}/film-logs")
    assert logs_resp.status_code == 200
    log_data = logs_resp.json()
    assert log_data["total"] >= 2

    # Check for target_unmet log
    target_unmet_logs = [log for log in log_data["logs"] if log["status"] == "target_unmet"]
    assert len(target_unmet_logs) >= 1
    tu = target_unmet_logs[0]
    assert tu["row"] == 0 and tu["col"] == 0
    assert tu["note"] == "목표 미달"
    assert tu["new_opacity"] == 1.0
    assert len(tu["reasons"]) == 1

    # Check for unknown_intensity log
    unknown_logs = [log for log in log_data["logs"] if log["status"] == "unknown_intensity"]
    assert len(unknown_logs) >= 1
    un = unknown_logs[0]
    assert un["row"] == 1 and un["col"] == 1
    assert un["note"] == "조도 미상"
    assert un["new_opacity"] == 1.0

    # Query with filters
    filtered = client.get(f"/api/scenes/{scene_id}/film-logs?status=target_unmet")
    assert filtered.status_code == 200
    assert all(item["status"] == "target_unmet" for item in filtered.json()["logs"])


def test_server_restart_persistence(tmp_path: Path, scene_payload: dict[str, Any]) -> None:
    db_file = tmp_path / "persistent_film.db"
    db_url = f"sqlite:///{db_file}"

    # First app run: create scene, apply commands, and apply marker
    engine1 = create_engine(db_url, connect_args={"check_same_thread": False})
    with TestClient(create_app(engine1)) as client1:
        scene_id = create_test_scene(client1, scene_payload)
        client1.post(
            f"/api/scenes/{scene_id}/film-commands",
            json={"commands": [{"row": 3, "col": 3, "opacity": 0.8}]},
        )
        client1.post(
            f"/api/scenes/{scene_id}/film-marker-mode",
            json={"enabled": True, "code_id": 7, "start_row": 2, "start_col": 2},
        )
    engine1.dispose()

    # Second app run (simulating server restart on the same SQLite database)
    engine2 = create_engine(db_url, connect_args={"check_same_thread": False})
    with TestClient(create_app(engine2)) as client2:
        resp = client2.get(f"/api/scenes/{scene_id}/film-state")
        assert resp.status_code == 200
        state = resp.json()
        win = state["windows"][0]
        # State remained intact
        assert win["marker_mode_active"] is True
        assert win["marker_code_id"] == 7
        assert win["cells"][2][2] == 1.0

        # Application logs remained intact
        logs_resp = client2.get(f"/api/scenes/{scene_id}/film-logs")
        assert logs_resp.status_code == 200
        logs = logs_resp.json()["logs"]
        assert len(logs) > 0
    engine2.dispose()


def test_multi_user_simultaneous_ppt_aruco_safeguards(
    client: TestClient, installed_film_payload: dict[str, Any]
) -> None:
    """Acceptance test: 5 users with 5 different scenes photograph the same PPT slide (code_id=0).

    Each user sends out-of-room furniture coordinates. All 5 must succeed with 200 OK,
    furniture must be safely clamped inside the room without 422 errors, and cell/marker
    operations in one user's scene must never affect the other 4 scenes.
    """
    user_scenes: list[str] = []
    num_users = 5

    # 1. Create 5 distinct scenes for 5 concurrent users
    for i in range(num_users):
        user_payload = copy.deepcopy(installed_film_payload)
        user_payload["scene_id"] = f"scene_user_{i + 1}"
        s_id = create_test_scene(client, user_payload)
        user_scenes.append(s_id)

    # 2. All 5 users call marker-pairing simultaneously with code_id=0 and out-of-room furniture
    pairing_responses: list[dict[str, Any]] = []
    for i, s_id in enumerate(user_scenes):
        pairing_req = {
            "code_id": 0,
            "auto_provision_window": True,
            "enable_marker_display": True,
            "clamp_furniture_to_room": True,
            "furniture": [
                {
                    "occluder_id": f"desk_user_{i + 1}",
                    "name": f"Desk {i + 1}",
                    "type": "desk",
                    "base_center_m": [12.0, -5.0, 0.0],  # Out of bounds!
                    "dimensions_m": [1.2, 0.8, 0.75],
                    "yaw_deg_ccw_from_east": 15.0,
                }
            ],
        }
        res = client.post(f"/api/scenes/{s_id}/marker-pairing", json=pairing_req)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["code_id"] == 0
        assert f"desk_user_{i + 1}" in data["clamped_furniture_ids"]
        assert data["film_state"]["marker_mode_active"] is True
        pairing_responses.append(data)

    # 3. Verify that all 5 scenes are valid FR-06 scenes with clamped furniture
    for i, s_id in enumerate(user_scenes):
        sc_resp = client.get(f"/api/scenes/{s_id}")
        assert sc_resp.status_code == 200
        sc_data = sc_resp.json()
        occ = next(o for o in sc_data["occluders"] if o["occluder_id"] == f"desk_user_{i + 1}")
        # Footprint is clamped inside room [0..8, 0..6]
        cx, cy, _ = occ["base_center_m"]
        assert 0.0 <= cx <= 8.0
        assert 0.0 <= cy <= 6.0

    # 4. Modify User 1's film (disable marker mode, apply manual cell commands)
    u1_scene = user_scenes[0]
    client.post(f"/api/scenes/{u1_scene}/film-marker-mode", json={"enabled": False})
    client.post(
        f"/api/scenes/{u1_scene}/film-commands",
        json={"commands": [{"row": 0, "col": 0, "opacity": 0.33}]},
    )

    # 5. Verify User 1's state updated
    u1_state = client.get(f"/api/scenes/{u1_scene}/film-state").json()["windows"][0]
    assert u1_state["marker_mode_active"] is False
    assert u1_state["cells"][0][0] == 0.33

    # 6. Verify User 2..5 states remained completely untouched (isolation guarantee)
    for other_scene in user_scenes[1:]:
        st = client.get(f"/api/scenes/{other_scene}/film-state").json()["windows"][0]
        assert st["marker_mode_active"] is True
        assert st["marker_code_id"] == 0
        assert st["cells"][0][0] == 0.0  # untouched


def test_schedule_apply_idempotency_and_replacement(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    scene_id = create_test_scene(client, scene_payload)

    plan_a = {
        "plan_version": "0.1.0",
        "scene_id": scene_id,
        "scene_revision": 1,
        "start_time": "2026-10-14T08:00:00Z",
        "end_time": "2026-10-14T10:00:00Z",
        "windows": [
            {
                "window_id": "window_001",
                "film_rows": 10,
                "film_cols": 10,
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "segments": [
                            {
                                "start_time": "2026-10-14T08:30:00Z",
                                "end_time": "2026-10-14T09:30:00Z",
                                "state": "opaque",
                                "reasons": [{"region_id": "region_desk", "grade": "A"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    # First apply of Plan A
    resp1 = client.post(
        f"/api/scenes/{scene_id}/film-schedule-apply",
        json={"plan": plan_a, "plan_id": "plan_alpha"},
    )
    assert resp1.status_code == 200
    logs1 = client.get(f"/api/scenes/{scene_id}/film-logs").json()["logs"]
    count1 = len(logs1)
    assert count1 >= 1

    # Re-apply Plan A with same plan_id (idempotency check)
    resp2 = client.post(
        f"/api/scenes/{scene_id}/film-schedule-apply",
        json={"plan": plan_a, "plan_id": "plan_alpha"},
    )
    assert resp2.status_code == 200
    logs2 = client.get(f"/api/scenes/{scene_id}/film-logs").json()["logs"]
    assert len(logs2) == count1  # Exactly the same, not doubled!

    # Apply Plan B with a different plan_id (audit history preserved)
    plan_b = copy.deepcopy(plan_a)
    plan_b["windows"][0]["cells"][0]["segments"][0]["start_time"] = "2026-10-14T08:45:00Z"
    resp3 = client.post(
        f"/api/scenes/{scene_id}/film-schedule-apply",
        json={"plan": plan_b, "plan_id": "plan_beta"},
    )
    assert resp3.status_code == 200
    logs3 = client.get(f"/api/scenes/{scene_id}/film-logs").json()["logs"]
    # Contains logs from both plans
    plan_ids = {log["plan_id"] for log in logs3}
    assert "plan_alpha" in plan_ids
    assert "plan_beta" in plan_ids


def test_simulation_clock_manual_playback_and_timezone_tolerance(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    scene_id = create_test_scene(client, scene_payload)

    # Command 1 at 09:00:00Z
    client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={
            "commands": [{"row": 0, "col": 0, "opacity": 0.3}],
            "timestamp": "2026-11-04T09:00:00Z",
        },
    )

    # Command 2 at 10:00:00+09:00 (which is 01:00:00Z UTC)
    client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={
            "commands": [{"row": 1, "col": 1, "opacity": 0.6}],
            "timestamp": "2026-11-04T10:00:00+09:00",
        },
    )

    # Simulation clock queries:
    # 1. At 00:30:00Z (before any command): all cells 0.0
    r0 = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-11-04T00:30:00Z")
    assert r0.status_code == 200
    w0 = r0.json()["windows"][0]
    assert w0["cells"][0][0] == 0.0
    assert w0["cells"][1][1] == 0.0

    # 2. At 05:00:00Z (after cmd2 at 01:00Z, before cmd1 at 09:00Z):
    r1 = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-11-04T05:00:00Z")
    assert r1.status_code == 200
    w1 = r1.json()["windows"][0]
    assert w1["cells"][0][0] == 0.0
    assert w1["cells"][1][1] == 0.6

    # 3. At 09:30:00Z (after both commands):
    r2 = client.get(f"/api/scenes/{scene_id}/film-state?at=2026-11-04T09:30:00Z")
    assert r2.status_code == 200
    w2 = r2.json()["windows"][0]
    assert w2["cells"][0][0] == 0.3
    assert w2["cells"][1][1] == 0.6

    # 4. Filter logs using since / until in different timezones:
    # since 00:00:00Z, until 02:00:00Z -> should find only command 2 (01:00Z)
    logs_filtered = client.get(
        f"/api/scenes/{scene_id}/film-logs?since=2026-11-04T00:00:00Z&until=2026-11-04T02:00:00Z"
    ).json()["logs"]
    assert len(logs_filtered) == 1
    assert logs_filtered[0]["row"] == 1 and logs_filtered[0]["col"] == 1


def test_marker_pairing_with_rotated_furniture_clamping(
    client: TestClient, installed_film_payload: dict[str, Any]
) -> None:
    scene_id = create_test_scene(client, installed_film_payload)

    # Provide furniture rotated at 45.0 degrees and located far outside room
    pairing_req = {
        "code_id": 12,
        "auto_provision_window": True,
        "enable_marker_display": True,
        "clamp_furniture_to_room": True,
        "furniture": [
            {
                "occluder_id": "rotated_desk_45",
                "name": "Rotated Desk",
                "type": "desk",
                "base_center_m": [-10.0, 25.0, -1.0],  # Out of bounds
                "dimensions_m": [2.0, 1.0, 4.5],  # Out of bounds height
                "yaw_deg_ccw_from_east": 45.0,  # Rotated
            }
        ],
    }

    res = client.post(f"/api/scenes/{scene_id}/marker-pairing", json=pairing_req)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "rotated_desk_45" in data["clamped_furniture_ids"]

    # Verify scene is valid and footprint is inside room
    scene_resp = client.get(f"/api/scenes/{scene_id}")
    assert scene_resp.status_code == 200
    occ = next(o for o in scene_resp.json()["occluders"] if o["occluder_id"] == "rotated_desk_45")
    # Base height is clamped to 0.0 and height is <= room height
    assert occ["base_center_m"][2] >= 0.0
    assert occ["dimensions_m"][2] <= 3.0


def test_marker_pairing_uispace_synchronization(
    client: TestClient, database_engine: Engine
) -> None:
    # 1. Create a space via /api/ui/spaces
    space_payload = {
        "id": "space_test_sync",
        "building": "Test Building",
        "floor": "2F",
        "name": "Sync Test Room",
        "width": 6.0,
        "depth": 5.0,
        "height": 2.8,
        "zone": "zone_1",
        "zones": ["zone_1"],
        "source": "sample",
        "revision": 1,
        "site": {
            "latitude": 37.5,
            "longitude": 127.0,
            "timezone": "Asia/Seoul",
            "northConfirmed": False,
        },
        "furniture": [],
        "regions": [],
    }
    create_res = client.post("/api/ui/spaces", json=space_payload)
    assert create_res.status_code == 201, create_res.text
    created_space = create_res.json()
    assert created_space["serverRevision"] == 1
    server_scene_id = created_space["serverSceneId"]

    # 2. Call marker-pairing on the underlying scene
    pairing_res = client.post(
        f"/api/scenes/{server_scene_id}/marker-pairing",
        json={"code_id": 3, "auto_provision_window": True},
    )
    assert pairing_res.status_code == 200, pairing_res.text
    assert pairing_res.json()["scene_revision"] == 2

    # 3. Read space via /api/ui/spaces/{space_id} -> serverRevision updated in lockstep!
    get_res = client.get("/api/ui/spaces/space_test_sync")
    assert get_res.status_code == 200
    updated_space = get_res.json()
    assert updated_space["serverRevision"] == 2

    # 4. Modify space via PUT /api/ui/spaces/{space_id} with serverRevision=2 -> succeeds without 409!
    updated_space["name"] = "Renamed Sync Test Room"
    put_res = client.put("/api/ui/spaces/space_test_sync", json=updated_space)
    assert put_res.status_code == 200, put_res.text
    assert put_res.json()["serverRevision"] == 3


def test_ui_save_keeps_paired_marker_until_window_moves(client: TestClient) -> None:
    space = {
        "id": "space_marker_keep",
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
            "width": 2.0,
            "height": 2.0,
            "wall": "south",
            "offset": 2.0,
            "sill": 0.8,
        },
        "furniture": [],
        "regions": [],
    }
    created = client.post("/api/ui/spaces", json=space).json()
    scene_id = created["serverSceneId"]
    assert (
        client.post(f"/api/scenes/{scene_id}/marker-pairing", json={"code_id": 5}).status_code
        == 200
    )

    def markers() -> list[dict[str, object]]:
        return client.get(f"/api/scenes/{scene_id}").json()["markers"]

    assert [m["code_id"] for m in markers()] == [5]
    current = client.get("/api/ui/spaces/space_marker_keep").json()
    current["name"] = "Renamed"
    saved = client.put("/api/ui/spaces/space_marker_keep", json=current)
    assert saved.status_code == 200, saved.text
    assert [m["code_id"] for m in markers()] == [5]

    moved = saved.json()
    moved["windowDimensions"]["offset"] = 3.0
    assert client.put("/api/ui/spaces/space_marker_keep", json=moved).status_code == 200
    assert markers() == []


@pytest.mark.parametrize("wall", ["south", "north", "east", "west"])
def test_standard_film_marker_axes_match_window_on_all_walls(client: TestClient, wall: str) -> None:
    import numpy as np

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
    paired = client.post(f"/api/scenes/{scene_id}/marker-pairing", json={"code_id": 0})
    assert paired.status_code == 200, paired.text
    scene = client.get(f"/api/scenes/{scene_id}").json()
    marker, window = scene["markers"][0], scene["windows"][0]
    matrix = np.array(marker["T_ENU_marker"]).reshape(4, 4)
    assert marker["side_length_m"] == 0.6
    assert (marker["start_row"], marker["start_col"]) == (2, 2)
    assert matrix[:3, 0] == pytest.approx(window["u_axis"])
    assert matrix[:3, 1] == pytest.approx(window["v_axis"])
    assert matrix[:3, 2] == pytest.approx(window["inward_normal"])
    expected = (
        np.array(window["vertices_m"][0])
        + 0.5 * np.array(window["u_axis"])
        + 0.5 * np.array(window["v_axis"])
    )
    assert matrix[:3, 3] == pytest.approx(expected)


def test_nonexistent_scene_returns_404(client: TestClient) -> None:
    non_existent = "scene_does_not_exist_999"
    assert client.get(f"/api/scenes/{non_existent}/film-state").status_code == 404
    assert client.get(f"/api/scenes/{non_existent}/film-logs").status_code == 404
    assert (
        client.post(
            f"/api/scenes/{non_existent}/film-commands",
            json={"commands": [{"row": 0, "col": 0, "opacity": 0.5}]},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/scenes/{non_existent}/film-marker-mode",
            json={"enabled": True},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/scenes/{non_existent}/film-schedule-apply",
            json={
                "plan": {
                    "plan_version": "0.1.0",
                    "scene_id": non_existent,
                    "scene_revision": 1,
                    "start_time": "2026-10-14T08:00:00Z",
                    "end_time": "2026-10-14T10:00:00Z",
                    "windows": [],
                }
            },
        ).status_code
        == 404
    )
