"""Live two-server E2E integration tests: SunSync Backend <-> Standalone Film & ThinQ Hub.

Spins up the real ``sunsync_hub.api:create_app(...)`` on an ephemeral loopback port with
``uvicorn`` and exercises real HTTP + HMAC-SHA256 + WebSocket communication from the
real ``server/backend`` FastAPI application.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import uvicorn
from app.api.capture_markers import CaptureMarkerSession
from app.db import init_db as init_backend_db
from app.main import create_app as create_backend_app
from app.models.film import FilmStateRecord, HubCommandRecord
from app.schemas.adaptive_control import AdaptiveShadingPlanRequest, ThinQHubSyncRequest
from app.services.film import generate_aruco_4x4_pattern
from app.services.hub_client import HubClient, HubTransportError
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine, select
from sunsync_hub.api import create_app as create_hub_app
from sunsync_hub.auth import sign_request
from sunsync_hub.db import create_hub_engine
from websockets.sync.client import connect as ws_connect

EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples"
E2E_SHARED_SECRET = "e2e-test-secret-32-bytes-minimum!"


def _make_scene_payload(scene_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8")
    )
    payload["scene_id"] = scene_id
    payload["revision"] = 1
    if payload.get("scenario") is not None:
        payload["scenario"]["scene_revision"] = 1
    win = payload["windows"][0]
    win["window_id"] = "win_south"
    win["vertices_m"] = [
        [2.0, 0.0, 1.0],
        [1.0, 0.0, 1.0],
        [1.0, 0.0, 2.0],
        [2.0, 0.0, 2.0],
    ]
    win["film_rows"] = 10
    win["film_cols"] = 10
    return payload


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LiveHubServer:
    base_url: str
    ws_url: str
    secret: str
    hub_id: str

    def signed_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        secret: str | None = None,
    ) -> httpx.Response:
        raw_body = (
            json.dumps(json_body, separators=(",", ":"), sort_keys=True).encode("utf-8")
            if json_body is not None
            else b""
        )
        headers = sign_request(
            secret=self.secret if secret is None else secret,
            method=method,
            path_with_query=path,
            raw_body=raw_body,
        )
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
            return client.request(method, path, content=raw_body, headers=headers)

    def set_fault(
        self,
        mode: str,
        *,
        delay_ms: int = 0,
        scene_id: str | None = None,
        window_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": mode, "delay_ms": delay_ms}
        if scene_id is not None:
            payload["scene_id"] = scene_id
        if window_id is not None:
            payload["window_id"] = window_id
        res = self.signed_request("POST", "/hub/v1/sim/faults", json_body=payload)
        assert res.status_code == 200, res.text
        return res.json()

    def get_shadow(self, scene_id: str, window_id: str) -> httpx.Response:
        return self.signed_request("GET", f"/hub/v1/windows/{scene_id}/{window_id}/shadow")


@pytest.fixture
def live_hub(tmp_path: Path) -> Iterator[LiveHubServer]:
    hub_db = tmp_path / "hub-e2e.db"
    hub_engine = create_hub_engine(f"sqlite:///{hub_db}")
    hub_app = create_hub_app(
        database_engine=hub_engine,
        shared_secret=E2E_SHARED_SECRET,
        hub_id="hub-e2e-01",
    )

    port = _pick_free_port()
    config = uvicorn.Config(
        hub_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    thread = threading.Thread(target=server.run, name="live-hub-e2e", daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    ready = False
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/hub/v1/health", timeout=0.5)
            if r.status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)

    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        hub_engine.dispose()
        pytest.fail("Live hub uvicorn server failed to start within 10s")

    try:
        yield LiveHubServer(
            base_url=base_url,
            ws_url=f"ws://127.0.0.1:{port}/hub/v1/ws",
            secret=E2E_SHARED_SECRET,
            hub_id="hub-e2e-01",
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        hub_engine.dispose()


@pytest.fixture
def backend_engine(tmp_path: Path) -> Iterator[Engine]:
    db_file = tmp_path / "backend-e2e.db"
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )
    init_backend_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def backend_client(
    backend_engine: Engine,
    live_hub: LiveHubServer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("SUNSYNC_HUB_BASE_URL", live_hub.base_url)
    monkeypatch.setenv("SUNSYNC_HUB_SHARED_SECRET", live_hub.secret)
    monkeypatch.setenv("SUNSYNC_HUB_TIMEOUT_SECONDS", "3.0")
    app = create_backend_app(database_engine=backend_engine)
    with TestClient(app) as client:
        yield client


def test_e2e_fr05_manual_film_commands_and_hub_shadow(
    backend_client: TestClient,
    live_hub: LiveHubServer,
) -> None:
    scene_id = "scene_e2e_fr05"
    assert backend_client.post("/api/scenes", json=_make_scene_payload(scene_id)).status_code == 201

    cmd_res = backend_client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={
            "window_id": "win_south",
            "commands": [
                {"row": 0, "col": 0, "opacity": 1.0},
                {"row": 1, "col": 3, "opacity": 1.0},
            ],
            "note": "e2e-manual",
        },
    )
    assert cmd_res.status_code == 200, cmd_res.text
    body = cmd_res.json()
    assert body["sync_mode"] == "remote_hub"
    assert body["sync_status"] == "synced"
    assert body["desired_version"] == 1
    assert body["reported_version"] == 1
    assert body["reported_cells"] == body["cells"]
    assert body["reported_cells"][0][0] == 1.0
    assert body["reported_cells"][1][3] == 1.0
    assert body["last_command_id"] is not None
    assert body["last_synced_at"] is not None

    # Directly inspect live hub shadow on :8002
    shadow_res = live_hub.get_shadow(scene_id, "win_south")
    assert shadow_res.status_code == 200, shadow_res.text
    shadow = shadow_res.json()
    assert shadow["sync_status"] == "in_sync"
    assert shadow["desired_version"] == 1
    assert shadow["reported_version"] == 1
    assert shadow["reported"]["effective_cells"] == body["cells"]
    assert shadow["delta"]["in_sync"] is True
    assert shadow["delta"]["changed_cells"] == []


def test_e2e_fr05_2_capture_marker_overlay_and_exact_base_restore(
    backend_client: TestClient,
    live_hub: LiveHubServer,
) -> None:
    scene_id = "scene_e2e_capture"
    assert backend_client.post("/api/scenes", json=_make_scene_payload(scene_id)).status_code == 201

    # Seed distinctive base_cells (both outside and inside the 6x6 marker region [2..7, 2..7])
    seed_res = backend_client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={
            "window_id": "win_south",
            "commands": [
                {"row": 0, "col": 0, "opacity": 1.0},
                {"row": 0, "col": 9, "opacity": 1.0},
                {"row": 4, "col": 4, "opacity": 1.0},
                {"row": 9, "col": 9, "opacity": 1.0},
            ],
            "note": "seed-before-capture",
        },
    )
    assert seed_res.status_code == 200, seed_res.text
    original_base_cells = seed_res.json()["cells"]

    # Start FR-05.2 capture marker session
    req_id = str(uuid4())
    cap_res = backend_client.post(
        f"/api/scenes/{scene_id}/capture-marker",
        json={"request_id": req_id, "window_id": "win_south"},
    )
    assert cap_res.status_code == 200, cap_res.text
    cap_data = cap_res.json()
    assert cap_data["display_status"] == "hub_applied"
    assert cap_data["hub_command_id"] is not None
    assert cap_data["hub_applied_at"] is not None

    code_id = int(cap_data["marker"]["code_id"])
    expected_pattern = generate_aruco_4x4_pattern(code_id)

    # Verify hub shadow has 6x6 ArUco overlay applied cleanly while preserving base_cells!
    hub_during = live_hub.get_shadow(scene_id, "win_south")
    assert hub_during.status_code == 200
    shadow_during = hub_during.json()
    assert shadow_during["sync_status"] == "in_sync"
    assert shadow_during["reported"]["marker_overlay"]["enabled"] is True
    assert shadow_during["reported"]["marker_overlay"]["code_id"] == code_id
    assert shadow_during["reported"]["base_cells"] == original_base_cells

    sr = int(shadow_during["reported"]["marker_overlay"]["start_row"])
    sc = int(shadow_during["reported"]["marker_overlay"]["start_col"])
    eff_during = shadow_during["reported"]["effective_cells"]
    for r in range(6):
        for c in range(6):
            assert eff_during[sr + r][sc + c] == expected_pattern[r][c]
    # Cells outside the 6x6 overlay remain unchanged
    assert eff_during[0][0] == 1.0
    assert eff_during[0][9] == 1.0
    assert eff_during[9][9] == 1.0

    # End capture session -> restores exact original base_cells on BOTH backend and hub!
    end_res = backend_client.post(f"/api/scenes/{scene_id}/capture-marker/{req_id}/end")
    assert end_res.status_code == 200, end_res.text
    assert end_res.json()["status"] == "ended"

    backend_after = backend_client.get(
        f"/api/scenes/{scene_id}/film-state",
        params={"window_id": "win_south"},
    )
    assert backend_after.status_code == 200
    win_after = backend_after.json()["windows"][0]
    assert win_after["marker_mode_active"] is False
    assert win_after["sync_status"] == "synced"
    assert win_after["cells"] == original_base_cells
    assert win_after["reported_cells"] == original_base_cells

    hub_after = live_hub.get_shadow(scene_id, "win_south")
    assert hub_after.status_code == 200
    shadow_after = hub_after.json()
    assert shadow_after["sync_status"] == "in_sync"
    assert shadow_after["reported"]["marker_overlay"]["enabled"] is False
    assert shadow_after["reported"]["base_cells"] == original_base_cells
    assert shadow_after["reported"]["effective_cells"] == original_base_cells


def test_e2e_fr04_thinq_hub_sync(
    backend_client: TestClient,
    live_hub: LiveHubServer,
) -> None:
    scene_id = "scene_e2e_thinq"
    assert backend_client.post("/api/scenes", json=_make_scene_payload(scene_id)).status_code == 201

    sync_req = ThinQHubSyncRequest(
        hub_device_id="thinq_on_hub_e2e",
        target_timestamp="2026-10-14T04:50:00Z",
        hardware_mode="LG_SGF",
        plan_request=AdaptiveShadingPlanRequest(
            mode="replace",
            start_time="2026-10-14T04:30:00Z",
            duration_minutes=30,
            interval_seconds=60,
            hardware_mode="LG_SGF",
        ),
    )

    res1 = backend_client.post(
        f"/api/scenes/{scene_id}/thinq-hub-sync",
        json=sync_req.model_dump(),
    )
    assert res1.status_code == 200, res1.text
    data1 = res1.json()
    assert data1["sync_mode"] == "remote_hub"
    assert data1["hub_url"] == live_hub.base_url
    assert data1["sync_status"] == "synced"
    dev1 = data1["devices"][0]
    assert dev1["sync_status"] == "synced"
    assert dev1["reported_version"] == 1
    assert dev1["hub_ack_at"] is not None
    assert dev1["command_id"] is not None

    # Second sync increments reported_version monotonically on live hub
    res2 = backend_client.post(
        f"/api/scenes/{scene_id}/thinq-hub-sync",
        json=sync_req.model_dump(),
    )
    assert res2.status_code == 200, res2.text
    dev2 = res2.json()["devices"][0]
    assert dev2["sync_status"] == "synced"
    assert dev2["reported_version"] == 2
    assert dev2["hub_ack_at"] is not None

    # Live hub shadow reflects LG_SGF matrix and version 2
    hub_shadow = live_hub.get_shadow(scene_id, "win_south")
    assert hub_shadow.status_code == 200
    shadow = hub_shadow.json()
    assert shadow["sync_status"] == "in_sync"
    assert shadow["hardware_mode"] == "LG_SGF"
    assert shadow["reported_version"] == 2
    assert shadow["reported"]["effective_cells"] == dev2["cell_opacity_matrix"]


def test_e2e_fault_resilience_offline_pending_and_drift_reconciliation(
    backend_client: TestClient,
    backend_engine: Engine,
    live_hub: LiveHubServer,
) -> None:
    scene_id = "scene_e2e_faults"
    assert backend_client.post("/api/scenes", json=_make_scene_payload(scene_id)).status_code == 201

    # 1. Inject offline fault -> backend stays alive, persists desired state + pending outbox
    live_hub.set_fault("offline", scene_id=scene_id, window_id="win_south")
    cmd_res = backend_client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={
            "window_id": "win_south",
            "commands": [{"row": 2, "col": 5, "opacity": 1.0}],
            "note": "while-offline",
        },
    )
    assert cmd_res.status_code == 200, cmd_res.text
    cmd_body = cmd_res.json()
    assert cmd_body["sync_mode"] == "remote_hub"
    assert cmd_body["sync_status"] == "pending"
    assert cmd_body["desired_version"] == 1
    assert cmd_body["reported_version"] == 0
    assert cmd_body["cells"][2][5] == 1.0

    with Session(backend_engine) as session:
        pending_rows = session.exec(
            select(HubCommandRecord)
            .where(HubCommandRecord.scene_id == scene_id)
            .where(HubCommandRecord.status == "pending")
        ).all()
        assert len(pending_rows) == 1

    # 2. Clear fault -> reconcile flushes outbox and converges to synced on both servers
    live_hub.set_fault("none")
    rec1 = backend_client.post(f"/api/scenes/{scene_id}/hub-reconcile")
    assert rec1.status_code == 200, rec1.text
    rec1_data = rec1.json()
    assert rec1_data["dispatched_commands"] >= 1
    assert rec1_data["failed_windows"] == 0
    assert rec1_data["windows"][0]["sync_status"] == "synced"

    shadow_synced = live_hub.get_shadow(scene_id, "win_south").json()
    assert shadow_synced["sync_status"] == "in_sync"
    assert shadow_synced["reported_version"] == 1
    assert shadow_synced["reported"]["effective_cells"][2][5] == 1.0

    # 3. Inject drift fault on live hub -> hub reports drifted shadow
    drift_info = live_hub.set_fault("drift", scene_id=scene_id, window_id="win_south")
    assert len(drift_info["drifted_windows"]) == 1
    shadow_drifted = live_hub.get_shadow(scene_id, "win_south").json()
    assert shadow_drifted["sync_status"] == "drifted"
    assert shadow_drifted["delta"]["in_sync"] is False

    # Clear fault mode back to none (shadow remains drifted in hub.db) and run reconcile
    live_hub.set_fault("none")
    rec2 = backend_client.post(f"/api/scenes/{scene_id}/hub-reconcile")
    assert rec2.status_code == 200, rec2.text
    rec2_data = rec2.json()
    assert rec2_data["healed_windows"] == 1
    assert rec2_data["failed_windows"] == 0
    assert rec2_data["windows"][0]["action"] == "healed"
    assert rec2_data["windows"][0]["sync_status"] == "synced"

    # Verify hub shadow is healed back to in_sync and matches backend desired cells
    shadow_healed = live_hub.get_shadow(scene_id, "win_south").json()
    assert shadow_healed["sync_status"] == "in_sync"
    assert shadow_healed["delta"]["in_sync"] is True
    assert shadow_healed["reported"]["effective_cells"] == cmd_body["cells"]


def test_e2e_security_wrong_hmac_secret_rejected_with_401(
    backend_client: TestClient,
    backend_engine: Engine,
    live_hub: LiveHubServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_id = "scene_e2e_auth"
    assert backend_client.post("/api/scenes", json=_make_scene_payload(scene_id)).status_code == 201

    wrong_secret = "wrong-secret-must-be-rejected-401!"

    # 1. Direct HubClient call with wrong secret raises HubTransportError(status_code=401)
    bad_client = HubClient(base_url=live_hub.base_url, shared_secret=wrong_secret)
    with pytest.raises(HubTransportError) as exc_info:
        bad_client.post_commands(
            scene_id,
            "win_south",
            desired_version=1,
            commands=[{"row": 0, "col": 0, "opacity": 1.0}],
            rows=10,
            cols=10,
            hardware_mode="PDLC",
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False

    # 2. Backend configured with wrong secret must NOT silently mark command as synced
    monkeypatch.setenv("SUNSYNC_HUB_SHARED_SECRET", wrong_secret)
    cmd_res = backend_client.post(
        f"/api/scenes/{scene_id}/film-commands",
        json={
            "window_id": "win_south",
            "commands": [{"row": 0, "col": 0, "opacity": 1.0}],
        },
    )
    assert cmd_res.status_code == 200
    body = cmd_res.json()
    assert body["sync_status"] == "failed"
    assert body["reported_version"] == 0
    assert "Unauthorized" in (body["sync_error"] or "")

    # Hub never created a shadow for the unauthorized request
    assert live_hub.get_shadow(scene_id, "win_south").status_code == 404

    # 3. Capture marker under wrong secret fails closed (503) and rolls back atomically
    cap_res = backend_client.post(
        f"/api/scenes/{scene_id}/capture-marker",
        json={"request_id": str(uuid4()), "window_id": "win_south"},
    )
    assert cap_res.status_code == 503
    with Session(backend_engine) as session:
        assert session.get(CaptureMarkerSession, scene_id) is None
        film_rec = session.get(FilmStateRecord, f"{scene_id}:win_south")
        assert film_rec is not None and film_rec.marker_mode_active is False


def test_e2e_websocket_telemetry_receives_shadow_applied(
    backend_client: TestClient,
    live_hub: LiveHubServer,
) -> None:
    scene_id = "scene_e2e_ws"
    assert backend_client.post("/api/scenes", json=_make_scene_payload(scene_id)).status_code == 201

    ws_headers = sign_request(
        secret=live_hub.secret,
        method="GET",
        path_with_query="/hub/v1/ws",
        raw_body=b"",
    )

    with ws_connect(live_hub.ws_url, additional_headers=ws_headers, open_timeout=5.0) as ws:
        # Trigger a real command from backend -> live hub
        cmd_res = backend_client.post(
            f"/api/scenes/{scene_id}/film-commands",
            json={
                "window_id": "win_south",
                "commands": [{"row": 3, "col": 4, "opacity": 1.0}],
                "note": "ws-telemetry-check",
            },
        )
        assert cmd_res.status_code == 200, cmd_res.text
        cmd_body = cmd_res.json()
        assert cmd_body["sync_status"] == "synced"

        raw_msg = ws.recv(timeout=5.0)
        event = json.loads(raw_msg)
        assert event["event"] == "shadow_applied"
        assert event["scene_id"] == scene_id
        assert event["window_id"] == "win_south"
        assert event["command_id"] == cmd_body["last_command_id"]
        assert event["desired_version"] == 1
        assert event["reported_version"] == 1
        assert event["effective_cells"][3][4] == 1.0
