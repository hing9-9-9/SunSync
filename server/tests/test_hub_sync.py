"""Unit and API integration tests for Backend <-> Film & ThinQ Hub sync, outbox, and migration v7."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import pytest
from app.api.capture_markers import CaptureMarkerSession
from app.db import init_db
from app.main import create_app
from app.migrations.runner import LATEST_VERSION, MIGRATIONS
from app.models.film import FilmApplicationLogRecord, FilmStateRecord, HubCommandRecord
from app.schemas.adaptive_control import AdaptiveShadingPlanRequest, ThinQHubSyncRequest
from app.services.film import generate_aruco_4x4_pattern
from app.services.hub_client import (
    build_signed_headers,
    compute_hub_signature,
    compute_state_digest,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, create_engine, select

EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples"


def _make_scene_payload(scene_id: str = "scene_hub_test") -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8")
    )
    payload["scene_id"] = scene_id
    payload["revision"] = 1
    if payload.get("scenario") is not None:
        payload["scenario"]["scene_revision"] = 1
    # Ensure standard 1.0m x 1.0m 10x10 film module geometry so capture-marker also accepts it.
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


def _make_shading_plan(scene_id: str, window_id: str = "win_south") -> dict[str, Any]:
    return {
        "plan_version": "0.1.0",
        "scene_id": scene_id,
        "scene_revision": 1,
        "start_time": "2026-10-14T04:00:00Z",
        "end_time": "2026-10-14T05:00:00Z",
        "windows": [
            {
                "window_id": window_id,
                "film_rows": 10,
                "film_cols": 10,
                "cells": [
                    {
                        "row": 1,
                        "col": 2,
                        "segments": [
                            {
                                "start_time": "2026-10-14T04:00:00Z",
                                "end_time": "2026-10-14T05:00:00Z",
                                "state": "opaque",
                                "reasons": [{"region_id": "region_A", "grade": "A"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }


class FakeHubServer:
    """In-process loopback HTTP hub simulator verifying HMAC signatures and shadow semantics."""

    def __init__(self, shared_secret: str = "test-hub-secret-32bytes-minimum") -> None:
        self.shared_secret = shared_secret
        self.hub_id = "hub-test-01"
        self.hub_boot_id = "boot-aaa-111"
        self.fault_mode: str = "none"
        self.requests: list[dict[str, Any]] = []
        self.shadows: dict[str, dict[str, Any]] = {}
        self.force_status: str | None = None
        self.force_reported_version: int | None = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def _compose_effective(
        self, base_cells: list[list[float]], marker_overlay: dict[str, Any]
    ) -> list[list[float]]:
        cells = [row[:] for row in base_cells]
        if marker_overlay.get("enabled"):
            code_id = int(marker_overlay["code_id"])
            sr = int(marker_overlay["start_row"])
            sc = int(marker_overlay["start_col"])
            pattern = generate_aruco_4x4_pattern(code_id)
            for r in range(6):
                for c in range(6):
                    cells[sr + r][sc + c] = pattern[r][c]
        return cells

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, code: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _verify_hmac(self, method: str, raw_body: bytes) -> bool:
                if not hub.shared_secret or self.path == "/hub/v1/health":
                    return True
                sig = self.headers.get("X-Hub-Signature", "")
                ts = self.headers.get("X-Hub-Timestamp", "")
                nonce = self.headers.get("X-Hub-Nonce", "")
                expected = compute_hub_signature(
                    method=method,
                    path_with_query=self.path,
                    timestamp=ts,
                    nonce=nonce,
                    raw_body=raw_body,
                    secret=hub.shared_secret,
                )
                return hmac.compare_digest(sig, expected)

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length > 0 else b""
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                headers_lower = {k.lower(): v for k, v in self.headers.items()}
                hub.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "headers": headers_lower,
                        "body": body,
                    }
                )
                if not self._verify_hmac(method, raw_body):
                    self._send(401, {"detail": "Invalid HMAC signature"})
                    return

                if method == "GET" and self.path == "/hub/v1/health":
                    if hub.fault_mode == "offline":
                        self._send(503, {"detail": "Hub offline"})
                        return
                    self._send(
                        200,
                        {
                            "status": "ok",
                            "hub_id": hub.hub_id,
                            "hub_boot_id": hub.hub_boot_id,
                            "uptime_s": 12.3,
                            "managed_windows": len(hub.shadows),
                            "simulated_fault_mode": hub.fault_mode,
                        },
                    )
                    return

                if hub.fault_mode == "offline":
                    self._send(503, {"detail": "Simulated hub offline"})
                    return
                if hub.fault_mode == "timeout":
                    self._send(504, {"detail": "Simulated hub timeout"})
                    return
                if hub.fault_mode == "reject":
                    self._send(
                        200,
                        {
                            "command_id": headers_lower.get("idempotency-key", ""),
                            "status": "rejected",
                            "rejection_reason": "Simulated PDLC binary violation",
                        },
                    )
                    return

                if method == "POST" and self.path == "/hub/v1/reconcile/digests":
                    in_sync: list[dict[str, Any]] = []
                    drifted: list[dict[str, Any]] = []
                    missing: list[dict[str, Any]] = []
                    for item in body.get("windows", []):
                        key = f"{item['scene_id']}:{item['window_id']}"
                        shadow = hub.shadows.get(key)
                        if shadow is None:
                            missing.append(
                                {
                                    "scene_id": item["scene_id"],
                                    "window_id": item["window_id"],
                                    "desired_version": item["desired_version"],
                                    "expected_digest": item["state_digest"],
                                }
                            )
                        elif shadow["state_digest"] == item["state_digest"] and (
                            shadow["reported_version"] == item["desired_version"]
                        ):
                            in_sync.append(
                                {
                                    "scene_id": item["scene_id"],
                                    "window_id": item["window_id"],
                                    "reported_version": shadow["reported_version"],
                                    "state_digest": shadow["state_digest"],
                                }
                            )
                        else:
                            drifted.append(
                                {
                                    "scene_id": item["scene_id"],
                                    "window_id": item["window_id"],
                                    "desired_version": item["desired_version"],
                                    "reported_version": shadow["reported_version"],
                                    "expected_digest": item["state_digest"],
                                    "reported_digest": shadow["state_digest"],
                                    "sync_status": "drifted",
                                }
                            )
                    self._send(
                        200,
                        {
                            "hub_boot_id": hub.hub_boot_id,
                            "in_sync": in_sync,
                            "drifted": drifted,
                            "missing": missing,
                        },
                    )
                    return

                if method == "POST" and self.path == "/hub/v1/thinq/sync":
                    scene_id = body["scene_id"]
                    window_id = body["window_id"]
                    key = f"{scene_id}:{window_id}"
                    ver = int(body.get("desired_version", 1))
                    cells = body["cell_opacity_matrix"]
                    overlay = {
                        "enabled": False,
                        "code_id": None,
                        "start_row": None,
                        "start_col": None,
                    }
                    digest = compute_state_digest(cells, overlay)
                    hub.shadows[key] = {
                        "base_cells": cells,
                        "marker_overlay": overlay,
                        "effective_cells": cells,
                        "reported_version": ver,
                        "state_digest": digest,
                    }
                    self._send(
                        200,
                        {
                            "hub_device_id": body.get("hub_device_id", "thinq_on_hub_01"),
                            "command_id": body.get(
                                "command_id", headers_lower.get("idempotency-key")
                            ),
                            "status": "applied",
                            "desired_version": ver,
                            "reported_version": ver,
                            "state_digest": digest,
                            "effective_cells": cells,
                            "active_shaded_cells": body.get("active_shaded_cells", 0),
                            "dimmed_cells": body.get("dimmed_cells", 0),
                            "matter_level_pct_x100": 1500,
                            "latency_ms": 4,
                            "applied_at": "2026-10-14T04:50:01Z",
                            "rejection_reason": None,
                        },
                    )
                    return

                parts = [unquote(p) for p in self.path.strip("/").split("/")]
                if len(parts) == 6 and parts[:3] == ["hub", "v1", "windows"]:
                    scene_id, window_id, action = parts[3], parts[4], parts[5]
                    key = f"{scene_id}:{window_id}"
                    cmd_id = headers_lower.get("idempotency-key", body.get("command_id", ""))
                    if hub.force_status is not None:
                        self._send(
                            200,
                            {
                                "command_id": cmd_id,
                                "status": hub.force_status,
                                "reported_version": hub.force_reported_version or 99,
                                "rejection_reason": f"forced {hub.force_status}",
                            },
                        )
                        return

                    if method == "PUT" and action == "shadow":
                        ver = int(body["desired_version"])
                        base_cells = body["base_cells"]
                        overlay = body.get("marker_overlay") or {
                            "enabled": False,
                            "code_id": None,
                            "start_row": None,
                            "start_col": None,
                        }
                        eff = hub._compose_effective(base_cells, overlay)
                        digest = compute_state_digest(eff, overlay)
                        hub.shadows[key] = {
                            "base_cells": base_cells,
                            "marker_overlay": overlay,
                            "effective_cells": eff,
                            "reported_version": ver,
                            "state_digest": digest,
                        }
                        self._send(
                            200,
                            {
                                "command_id": cmd_id,
                                "status": "applied",
                                "desired_version": ver,
                                "reported_version": ver,
                                "state_digest": digest,
                                "effective_cells": eff,
                                "applied_at": "2026-10-14T04:00:01Z",
                                "rejection_reason": None,
                            },
                        )
                        return

                    if method == "POST" and action == "commands":
                        ver = int(body["desired_version"])
                        rows = int(body.get("rows", 10))
                        cols = int(body.get("cols", 10))
                        existing = hub.shadows.get(key)
                        base_cells = (
                            [r[:] for r in existing["base_cells"]]
                            if existing
                            else [[0.0 for _ in range(cols)] for _ in range(rows)]
                        )
                        overlay = (
                            dict(existing["marker_overlay"])
                            if existing
                            else {
                                "enabled": False,
                                "code_id": None,
                                "start_row": None,
                                "start_col": None,
                            }
                        )
                        for cmd in body["commands"]:
                            base_cells[cmd["row"]][cmd["col"]] = float(cmd["opacity"])
                        eff = hub._compose_effective(base_cells, overlay)
                        digest = compute_state_digest(eff, overlay)
                        hub.shadows[key] = {
                            "base_cells": base_cells,
                            "marker_overlay": overlay,
                            "effective_cells": eff,
                            "reported_version": ver,
                            "state_digest": digest,
                        }
                        self._send(
                            200,
                            {
                                "command_id": cmd_id,
                                "status": "applied",
                                "desired_version": ver,
                                "reported_version": ver,
                                "state_digest": digest,
                                "effective_cells": eff,
                                "applied_at": "2026-10-14T04:01:00Z",
                                "rejection_reason": None,
                            },
                        )
                        return

                    if method == "POST" and action == "marker-overlay":
                        ver = int(body["desired_version"])
                        rows = int(body.get("rows", 10))
                        cols = int(body.get("cols", 10))
                        existing = hub.shadows.get(key)
                        base_cells = (
                            [r[:] for r in existing["base_cells"]]
                            if existing
                            else [[0.0 for _ in range(cols)] for _ in range(rows)]
                        )
                        overlay = {
                            "enabled": bool(body["enabled"]),
                            "code_id": body.get("code_id"),
                            "start_row": body.get("start_row"),
                            "start_col": body.get("start_col"),
                        }
                        eff = hub._compose_effective(base_cells, overlay)
                        digest = compute_state_digest(eff, overlay)
                        hub.shadows[key] = {
                            "base_cells": base_cells,
                            "marker_overlay": overlay,
                            "effective_cells": eff,
                            "reported_version": ver,
                            "state_digest": digest,
                        }
                        self._send(
                            200,
                            {
                                "command_id": cmd_id,
                                "status": "applied",
                                "desired_version": ver,
                                "reported_version": ver,
                                "state_digest": digest,
                                "effective_cells": eff,
                                "marker_overlay": overlay,
                                "applied_at": "2026-10-14T04:02:00Z",
                                "rejection_reason": None,
                            },
                        )
                        return

                self._send(404, {"detail": f"Unknown route {method} {self.path}"})

            def do_GET(self) -> None:
                self._handle("GET")

            def do_POST(self) -> None:
                self._handle("POST")

            def do_PUT(self) -> None:
                self._handle("PUT")

        return Handler


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'hub_sync.db'}", connect_args={"check_same_thread": False}
    )
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine)) as test_client:
        yield test_client


@pytest.fixture
def fake_hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHubServer]:
    hub = FakeHubServer()
    monkeypatch.setenv("SUNSYNC_HUB_BASE_URL", hub.base_url)
    monkeypatch.setenv("SUNSYNC_HUB_SHARED_SECRET", hub.shared_secret)
    monkeypatch.setenv("SUNSYNC_HUB_KEY_ID", "v1")
    monkeypatch.setenv("SUNSYNC_HUB_TIMEOUT_SECONDS", "2.0")
    try:
        yield hub
    finally:
        hub.close()


def test_migration_v7_upgrades_v6_db_preserving_rows_and_handling_missing_capture_table(
    tmp_path: Path,
) -> None:
    """Verify migration v7 upgrades a v6 DB safely both with and without capturemarkersession."""
    # Case 1: v6 DB containing legacy film_states, film_application_logs, and capturemarkersession
    db_with_capture = tmp_path / "v6_with_capture.db"
    engine = create_engine(f"sqlite:///{db_with_capture}")
    with engine.begin() as conn:
        for ver in range(1, 7):
            if ver in (5, 6):
                # Ensure legacy scenes/ui_deleted_spaces exist so _upgrade_5/_upgrade_6 run cleanly
                conn.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS scenes (scene_id TEXT PRIMARY KEY, revision INTEGER, scene_json TEXT)"
                )
                conn.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS ui_deleted_spaces (space_id TEXT PRIMARY KEY)"
                )
            MIGRATIONS[ver](conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {ver}")

        conn.exec_driver_sql(
            """
            CREATE TABLE film_states (
              state_key TEXT PRIMARY KEY,
              scene_id TEXT NOT NULL,
              window_id TEXT NOT NULL,
              film_type TEXT NOT NULL,
              film_rows INTEGER NOT NULL,
              film_cols INTEGER NOT NULL,
              cells_json TEXT NOT NULL,
              marker_mode_active BOOLEAN NOT NULL DEFAULT 0,
              marker_code_id INTEGER,
              marker_start_row INTEGER,
              marker_start_col INTEGER,
              marker_backup_json TEXT,
              active_plan_id TEXT,
              active_plan_json TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE film_application_logs (
              log_id INTEGER PRIMARY KEY,
              scene_id TEXT NOT NULL,
              window_id TEXT NOT NULL,
              timestamp TEXT NOT NULL,
              row INTEGER NOT NULL,
              col INTEGER NOT NULL,
              previous_opacity REAL NOT NULL,
              new_opacity REAL NOT NULL,
              previous_state TEXT NOT NULL,
              new_state TEXT NOT NULL,
              plan_id TEXT,
              status TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              reasons_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE capturemarkersession (
              scene_id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              window_id TEXT NOT NULL,
              marker_json TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              active BOOLEAN NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            text(
                "INSERT INTO film_states(state_key, scene_id, window_id, film_type, film_rows, film_cols, cells_json, updated_at) "
                "VALUES ('sc1:w1', 'sc1', 'w1', 'PDLC', 2, 2, '[[0,0],[0,0]]', '2026-10-14T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO capturemarkersession(scene_id, session_id, window_id, marker_json, expires_at, active) "
                "VALUES ('sc1', 'sess-1', 'w1', '{}', '2026-10-14T01:00:00Z', 1)"
            )
        )

    init_db(engine)

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == LATEST_VERSION == 7
        row = conn.execute(
            text(
                "SELECT desired_version, reported_version, sync_status FROM film_states WHERE state_key = 'sc1:w1'"
            )
        ).one()
        assert tuple(row) == (0, 0, "local_only")
        cap_row = conn.execute(
            text(
                "SELECT display_status, hub_command_id FROM capturemarkersession WHERE scene_id = 'sc1'"
            )
        ).one()
        assert tuple(cap_row) == ("virtual_applied", None)

    insp = inspect(engine)
    assert "hub_commands" in insp.get_table_names()
    log_cols = {c["name"] for c in insp.get_columns("film_application_logs")}
    assert {"command_id", "delivery_status", "hub_ack_at"}.issubset(log_cols)
    engine.dispose()

    # Case 2: v6 DB where capturemarkersession table does not exist yet
    db_no_capture = tmp_path / "v6_no_capture.db"
    engine2 = create_engine(f"sqlite:///{db_no_capture}")
    with engine2.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE scenes (scene_id TEXT PRIMARY KEY, revision INTEGER, scene_json TEXT)"
        )
        for ver in range(1, 7):
            MIGRATIONS[ver](conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {ver}")
        # Run _upgrade_7 directly without capturemarkersession present
        MIGRATIONS[7](conn)
        conn.exec_driver_sql("PRAGMA user_version = 7")
    assert "hub_commands" in inspect(engine2).get_table_names()
    engine2.dispose()


def test_hmac_signature_and_canonical_state_digest() -> None:
    """Verify exact RFC-9421-style canonical signing string and quantized state digest."""
    body = b'{"desired_version":3,"enabled":true}'
    ts = "1730000000"
    nonce = "fixed-nonce-123"
    secret = "super-secret-key"

    sig = compute_hub_signature(
        "post", "/hub/v1/windows/s1/w1/marker-overlay?v=1", ts, nonce, body, secret
    )
    body_hash = hashlib.sha256(body).hexdigest()
    expected_canonical = (
        f"POST\n/hub/v1/windows/s1/w1/marker-overlay?v=1\n{ts}\n{nonce}\n{body_hash}"
    )
    expected_sig = (
        "v1=" + hmac.new(secret.encode(), expected_canonical.encode(), hashlib.sha256).hexdigest()
    )
    assert sig == expected_sig

    headers = build_signed_headers(
        "POST",
        "/hub/v1/windows/s1/w1/marker-overlay?v=1",
        body,
        shared_secret=secret,
        key_id="v2",
        idempotency_key="cmd-abc",
        timestamp=ts,
        nonce=nonce,
    )
    assert headers["X-Hub-Key-Id"] == "v2"
    assert headers["X-Hub-Timestamp"] == ts
    assert headers["X-Hub-Nonce"] == nonce
    assert headers["X-Hub-Signature"] == expected_sig
    assert headers["Idempotency-Key"] == "cmd-abc"

    # Digest quantizes floats to 3 decimals and ignores marker coords when disabled
    d1 = compute_state_digest(
        [[0.1234, 1.0]], {"enabled": False, "code_id": 7, "start_row": 2, "start_col": 2}
    )
    d2 = compute_state_digest(
        [[0.1231, 1.0002]],
        {"enabled": False, "code_id": None, "start_row": None, "start_col": None},
    )
    assert d1 == d2
    d3 = compute_state_digest(
        [[0.123, 1.0]], {"enabled": True, "code_id": 7, "start_row": 2, "start_col": 2}
    )
    assert d3 != d1


def test_local_fallback_preserves_behavior_when_hub_url_unset(
    client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUNSYNC_HUB_BASE_URL", raising=False)
    created = client.post("/api/scenes", json=_make_scene_payload("scene_local"))
    assert created.status_code == 201

    cmd_res = client.post(
        "/api/scenes/scene_local/film-commands",
        json={"commands": [{"row": 0, "col": 0, "opacity": 1.0}]},
    )
    assert cmd_res.status_code == 200
    data = cmd_res.json()
    assert data["sync_mode"] == "local"
    assert data["sync_status"] == "local_only"
    assert data["desired_version"] == 0
    assert data["reported_version"] == 0

    cap_res = client.post(
        "/api/scenes/scene_local/capture-marker",
        json={"request_id": str(uuid4())},
    )
    assert cap_res.status_code == 200
    assert cap_res.json()["display_status"] == "virtual_applied"

    status_res = client.get("/api/scenes/scene_local/hub-status")
    assert status_res.status_code == 200
    assert status_res.json()["sync_mode"] == "local"
    assert status_res.json()["hub_status"] == "local_only"

    rec_res = client.post("/api/scenes/scene_local/hub-reconcile")
    assert rec_res.status_code == 200
    assert rec_res.json()["sync_mode"] == "local"

    with Session(database_engine) as session:
        assert session.exec(select(HubCommandRecord)).all() == []


def test_remote_hub_film_commands_schedule_and_marker_mode_flows(
    client: TestClient, database_engine: Engine, fake_hub: FakeHubServer
) -> None:
    assert client.post("/api/scenes", json=_make_scene_payload("scene_remote")).status_code == 201

    # 1. Manual cell command -> POST /hub/v1/windows/.../commands with HMAC
    cmd_res = client.post(
        "/api/scenes/scene_remote/film-commands",
        json={"commands": [{"row": 0, "col": 1, "opacity": 1.0}], "note": "manual"},
    )
    assert cmd_res.status_code == 200, cmd_res.text
    body = cmd_res.json()
    assert body["sync_mode"] == "remote_hub"
    assert body["sync_status"] == "synced"
    assert body["desired_version"] == 1
    assert body["reported_version"] == 1
    assert body["last_command_id"] is not None
    assert body["last_synced_at"] is not None
    assert body["reported_cells"][0][1] == 1.0

    # Verify log delivery metadata
    logs = client.get("/api/scenes/scene_remote/film-logs").json()["logs"]
    assert logs[0]["command_id"] == body["last_command_id"]
    assert logs[0]["delivery_status"] == "applied"
    assert logs[0]["hub_ack_at"] is not None

    # 2. Schedule apply -> PUT /hub/v1/windows/.../shadow
    sched_res = client.post(
        "/api/scenes/scene_remote/film-schedule-apply",
        json={"plan": _make_shading_plan("scene_remote"), "at": "2026-10-14T04:30:00Z"},
    )
    assert sched_res.status_code == 200, sched_res.text
    win_state = sched_res.json()["windows"][0]
    assert win_state["sync_status"] == "synced"
    assert win_state["desired_version"] == 2
    assert win_state["reported_version"] == 2
    assert win_state["reported_cells"][1][2] == 1.0

    # 3. Marker mode toggle -> POST /hub/v1/windows/.../marker-overlay
    marker_on = client.post(
        "/api/scenes/scene_remote/film-marker-mode",
        json={"enabled": True, "code_id": 12, "start_row": 2, "start_col": 2},
    )
    assert marker_on.status_code == 200, marker_on.text
    assert marker_on.json()["sync_status"] == "synced"
    assert marker_on.json()["desired_version"] == 3
    assert marker_on.json()["reported_version"] == 3

    marker_off = client.post(
        "/api/scenes/scene_remote/film-marker-mode",
        json={"enabled": False},
    )
    assert marker_off.status_code == 200
    assert marker_off.json()["desired_version"] == 4
    assert marker_off.json()["reported_version"] == 4
    assert marker_off.json()["reported_cells"][1][2] == 1.0  # base_cells restored!

    # Verify hub status reports online & 0 pending
    status = client.get("/api/scenes/scene_remote/hub-status").json()
    assert status["reachable"] is True
    assert status["hub_status"] == "online"
    assert status["hub_boot_id"] == fake_hub.hub_boot_id
    assert status["pending_outbox_count"] == 0

    with Session(database_engine) as session:
        cmds = session.exec(
            select(HubCommandRecord).where(HubCommandRecord.scene_id == "scene_remote")
        ).all()
        assert len(cmds) == 4
        assert all(c.status == "applied" for c in cmds)


def test_remote_hub_offline_pending_superseded_reject_and_reconcile(
    client: TestClient, database_engine: Engine, fake_hub: FakeHubServer
) -> None:
    assert (
        client.post("/api/scenes", json=_make_scene_payload("scene_resilience")).status_code == 201
    )

    # 1. Hub goes offline -> commands degrade to pending, newer command supersedes older outbox row
    fake_hub.fault_mode = "offline"
    r1 = client.post(
        "/api/scenes/scene_resilience/film-commands",
        json={"commands": [{"row": 0, "col": 0, "opacity": 1.0}]},
    )
    assert r1.status_code == 200
    assert r1.json()["sync_status"] == "pending"
    cmd1_id = r1.json()["last_command_id"]

    r2 = client.post(
        "/api/scenes/scene_resilience/film-commands",
        json={"commands": [{"row": 0, "col": 1, "opacity": 1.0}]},
    )
    assert r2.status_code == 200
    assert r2.json()["sync_status"] == "pending"
    assert r2.json()["desired_version"] == 2
    cmd2_id = r2.json()["last_command_id"]

    with Session(database_engine) as session:
        c1 = session.get(HubCommandRecord, cmd1_id)
        c2 = session.get(HubCommandRecord, cmd2_id)
        assert c1 is not None and c1.status == "superseded"
        assert c2 is not None and c2.status == "pending"

    status_offline = client.get("/api/scenes/scene_resilience/hub-status").json()
    assert status_offline["reachable"] is False
    assert status_offline["hub_status"] == "offline"
    assert status_offline["pending_outbox_count"] == 1

    # 2. Hub comes back online, but hub shadow is missing cell (0,0) because cmd1 was superseded!
    # Reconcile flushes cmd2 AND detects digest drift via /reconcile/digests, healing with full PUT /shadow!
    fake_hub.fault_mode = "none"
    rec = client.post("/api/scenes/scene_resilience/hub-reconcile")
    assert rec.status_code == 200, rec.text
    rec_data = rec.json()
    assert rec_data["dispatched_commands"] >= 2  # flushed cmd2 + healed via shadow_put
    assert rec_data["healed_windows"] == 1
    assert rec_data["windows"][0]["action"] == "healed"
    assert rec_data["windows"][0]["sync_status"] == "synced"

    # Hub now has BOTH (0,0)=1.0 and (0,1)=1.0 after full shadow healing!
    shadow = fake_hub.shadows["scene_resilience:win_south"]
    assert shadow["effective_cells"][0][0] == 1.0
    assert shadow["effective_cells"][0][1] == 1.0

    # 3. Second reconcile reports pure in_sync with zero drift
    rec2 = client.post("/api/scenes/scene_resilience/hub-reconcile").json()
    assert rec2["in_sync_windows"] == 1
    assert rec2["healed_windows"] == 0
    assert rec2["windows"][0]["action"] == "in_sync"

    # 4. Rejection flow (e.g. hub returns status="rejected")
    fake_hub.fault_mode = "reject"
    rej = client.post(
        "/api/scenes/scene_resilience/film-commands",
        json={"commands": [{"row": 2, "col": 2, "opacity": 0.5}]},
    )
    assert rej.status_code == 200
    assert rej.json()["sync_status"] == "rejected"
    assert "PDLC" in (rej.json()["sync_error"] or "")

    # 5. Superseded response from hub when hub has a higher reported_version
    fake_hub.fault_mode = "none"
    fake_hub.force_status = "superseded"
    fake_hub.force_reported_version = 50
    sup = client.post(
        "/api/scenes/scene_resilience/film-commands",
        json={"commands": [{"row": 3, "col": 3, "opacity": 1.0}]},
    )
    assert sup.status_code == 200
    with Session(database_engine) as session:
        sup_cmd = session.get(HubCommandRecord, sup.json()["last_command_id"])
        assert sup_cmd is not None and sup_cmd.status == "superseded"


def test_remote_hub_capture_marker_applied_and_atomic_rollback_on_failure(
    client: TestClient, database_engine: Engine, fake_hub: FakeHubServer
) -> None:
    assert client.post("/api/scenes", json=_make_scene_payload("scene_cap")).status_code == 201
    initial_scene = client.get("/api/scenes/scene_cap").json()

    # 1. When hub is offline, start_capture returns 503 and rolls back scene + film + session
    fake_hub.fault_mode = "offline"
    failed = client.post(
        "/api/scenes/scene_cap/capture-marker",
        json={"request_id": str(uuid4())},
    )
    assert failed.status_code == 503
    assert client.get("/api/scenes/scene_cap").json() == initial_scene
    with Session(database_engine) as session:
        assert session.get(CaptureMarkerSession, "scene_cap") is None
        assert session.get(FilmStateRecord, "scene_cap:win_south") is None

    # 2. When hub rejects marker overlay, also returns 503 and rolls back
    fake_hub.fault_mode = "reject"
    rejected = client.post(
        "/api/scenes/scene_cap/capture-marker",
        json={"request_id": str(uuid4())},
    )
    assert rejected.status_code == 503
    with Session(database_engine) as session:
        assert session.get(CaptureMarkerSession, "scene_cap") is None

    # 3. When hub ACKs, persists display_status="hub_applied" and is idempotent across GET/POST
    fake_hub.fault_mode = "none"
    req_id = str(uuid4())
    ok = client.post(
        "/api/scenes/scene_cap/capture-marker",
        json={"request_id": req_id},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["display_status"] == "hub_applied"
    assert data["hub_command_id"] is not None
    assert data["hub_applied_at"] is not None
    # Idempotent retry & GET return identical payload
    assert (
        client.post("/api/scenes/scene_cap/capture-marker", json={"request_id": req_id}).json()
        == data
    )
    assert client.get(f"/api/scenes/scene_cap/capture-marker/{req_id}").json() == data


def test_remote_hub_thinq_sync_headers_success_and_offline_degraded(
    client: TestClient, database_engine: Engine, fake_hub: FakeHubServer
) -> None:
    assert client.post("/api/scenes", json=_make_scene_payload("scene_thinq")).status_code == 201

    sync_req = ThinQHubSyncRequest(
        hub_device_id="thinq_on_hub_living_room",
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

    # 1. Online ThinQ sync
    res = client.post(
        "/api/scenes/scene_thinq/thinq-hub-sync",
        json=sync_req.model_dump(),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["sync_mode"] == "remote_hub"
    assert data["hub_url"] == fake_hub.base_url
    assert data["sync_status"] == "synced"
    dev = data["devices"][0]
    assert dev["sync_status"] == "synced"
    assert dev["command_id"] is not None
    assert dev["reported_version"] == 1
    assert dev["hub_ack_at"] == "2026-10-14T04:50:01Z"

    # Verify ThinQ headers & HMAC signature were sent
    thinq_reqs = [r for r in fake_hub.requests if r["path"] == "/hub/v1/thinq/sync"]
    assert len(thinq_reqs) == 1
    headers = thinq_reqs[0]["headers"]
    assert headers["x-protocol-version"] == "LG_THINQ_ON_SGF_V1"
    assert headers["x-client-id"] == "sunsync-backend-scene_thinq"
    assert len(headers["x-message-id"]) >= 20
    assert headers["idempotency-key"] == dev["command_id"]
    assert headers["x-hub-signature"].startswith("v1=")

    # 2. Offline ThinQ sync degrades gracefully to pending with outbox entry
    fake_hub.fault_mode = "offline"
    res_off = client.post(
        "/api/scenes/scene_thinq/thinq-hub-sync",
        json=sync_req.model_dump(),
    )
    assert res_off.status_code == 200
    data_off = res_off.json()
    assert data_off["sync_mode"] == "remote_hub"
    assert data_off["sync_status"] == "pending"
    assert data_off["devices"][0]["sync_status"] == "pending"

    with Session(database_engine) as session:
        rec = session.get(FilmStateRecord, "scene_thinq:win_south")
        assert rec is not None
        assert rec.desired_version == 2
        assert rec.reported_version == 1
        assert rec.sync_status == "pending"
        logs = session.exec(
            select(FilmApplicationLogRecord).where(
                FilmApplicationLogRecord.scene_id == "scene_thinq"
            )
        ).all()
        assert all(log.delivery_status in ("applied", "pending", "local_only") for log in logs)
