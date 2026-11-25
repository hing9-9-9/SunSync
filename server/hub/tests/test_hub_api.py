"""Comprehensive test suite for the standalone SunSync Film & ThinQ ON Hub Simulator."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sunsync_hub.api import create_app
from sunsync_hub.auth import NonceReplayCache, sign_request
from sunsync_hub.db import create_hub_engine
from sunsync_hub.driver import compute_matter_level_pct_x100
from sunsync_hub.shadow import (
    compose_effective_cells,
    compute_state_digest,
    format_etag,
    generate_aruco_4x4_pattern,
)


def _make_grid(rows: int, cols: int, fill: float = 0.0) -> list[list[float]]:
    return [[fill for _ in range(cols)] for _ in range(rows)]


@pytest.fixture
def hub_engine(tmp_path: Path):
    db_file = tmp_path / "hub-test.db"
    eng = create_hub_engine(f"sqlite:///{db_file}")
    yield eng
    eng.dispose()


@pytest.fixture
def client(hub_engine) -> TestClient:
    app = create_app(database_engine=hub_engine, shared_secret="", hub_id="hub-test-01")
    with TestClient(app) as c:
        yield c


def test_health_endpoints_and_managed_window_count(client: TestClient) -> None:
    r1 = client.get("/health")
    r2 = client.get("/hub/v1/health")
    assert r1.status_code == 200
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "ok"
    assert body["hub_id"] == "hub-test-01"
    assert isinstance(body["hub_boot_id"], str) and len(body["hub_boot_id"]) > 8
    assert body["uptime_s"] >= 0.0
    assert body["managed_windows"] == 0
    assert body["simulated_fault_mode"] == "none"

    grid = _make_grid(10, 10, 0.0)
    put_res = client.put(
        "/hub/v1/windows/scene-1/win-1/shadow",
        headers={"Idempotency-Key": "cmd-health-1"},
        json={
            "desired_version": 1,
            "hardware_mode": "PDLC",
            "rows": 10,
            "cols": 10,
            "base_cells": grid,
        },
    )
    assert put_res.status_code == 200
    assert client.get("/hub/v1/health").json()["managed_windows"] == 1


def test_device_shadow_desired_reported_delta_and_etag_if_match(client: TestClient) -> None:
    # Missing shadow returns 404
    assert client.get("/hub/v1/windows/s1/w1/shadow").status_code == 404

    grid = _make_grid(10, 10, 0.0)
    grid[0][0] = 1.0
    grid[0][1] = 1.0

    put_res = client.put(
        "/hub/v1/windows/s1/w1/shadow",
        headers={"Idempotency-Key": "cmd-shadow-1"},
        json={
            "desired_version": 1,
            "hardware_mode": "PDLC",
            "rows": 10,
            "cols": 10,
            "base_cells": grid,
        },
    )
    assert put_res.status_code == 200
    data = put_res.json()
    expected_digest = compute_state_digest(grid, None)
    expected_etag = format_etag(1, expected_digest)

    assert data["status"] == "applied"
    assert data["desired_version"] == 1
    assert data["reported_version"] == 1
    assert data["state_digest"] == expected_digest
    assert data["matter_level_pct_x100"] == 200  # 2 / 100 = 0.02 -> 200
    assert put_res.headers["ETag"] == expected_etag

    get_res = client.get("/hub/v1/windows/s1/w1/shadow")
    assert get_res.status_code == 200
    assert get_res.headers["ETag"] == expected_etag
    shadow = get_res.json()
    assert shadow["sync_status"] == "in_sync"
    assert shadow["desired"]["effective_cells"] == grid
    assert shadow["reported"]["effective_cells"] == grid
    assert shadow["delta"] == {"in_sync": True, "changed_cells": [], "marker_diff": {}}

    # Stale If-Match returns 412
    stale_res = client.put(
        "/hub/v1/windows/s1/w1/shadow",
        headers={"Idempotency-Key": "cmd-shadow-stale", "If-Match": '"v0-000000000000"'},
        json={
            "desired_version": 2,
            "hardware_mode": "PDLC",
            "rows": 10,
            "cols": 10,
            "base_cells": grid,
        },
    )
    assert stale_res.status_code == 412

    # Matching If-Match succeeds
    grid2 = _make_grid(10, 10, 1.0)
    ok_res = client.put(
        "/hub/v1/windows/s1/w1/shadow",
        headers={"Idempotency-Key": "cmd-shadow-2", "If-Match": expected_etag},
        json={
            "desired_version": 2,
            "hardware_mode": "PDLC",
            "rows": 10,
            "cols": 10,
            "base_cells": grid2,
        },
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["reported_version"] == 2
    assert ok_res.json()["matter_level_pct_x100"] == 10000


def test_layered_base_cells_and_marker_overlay_round_trip_without_drift(
    client: TestClient,
) -> None:
    base = _make_grid(10, 10, 0.25)
    base[0][0] = 0.75
    base[3][3] = 0.6  # Under the future 6x6 marker box [2..7, 2..7]
    original_base_copy = [row[:] for row in base]

    # Step 1: Set SPD base cells
    res1 = client.put(
        "/hub/v1/windows/s-layer/w-layer/shadow",
        headers={"Idempotency-Key": "layer-1"},
        json={
            "desired_version": 1,
            "hardware_mode": "SPD",
            "rows": 10,
            "cols": 10,
            "base_cells": base,
        },
    )
    assert res1.status_code == 200
    digest_v1 = res1.json()["state_digest"]

    # Step 2: Enable 6x6 ArUco marker overlay (code_id=7 at (2, 2))
    res2 = client.post(
        "/hub/v1/windows/s-layer/w-layer/marker-overlay",
        headers={"Idempotency-Key": "layer-2"},
        json={
            "desired_version": 2,
            "enabled": True,
            "code_id": 7,
            "start_row": 2,
            "start_col": 2,
        },
    )
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["status"] == "applied"
    assert d2["marker_overlay"] == {
        "enabled": True,
        "code_id": 7,
        "start_row": 2,
        "start_col": 2,
    }
    pattern = generate_aruco_4x4_pattern(7)
    for r in range(6):
        for c in range(6):
            assert d2["effective_cells"][2 + r][2 + c] == pattern[r][c]
    # Outside marker remains intact
    assert d2["effective_cells"][0][0] == 0.75
    assert d2["effective_cells"][9][9] == 0.25

    # Verify base_cells was NOT mutated
    shadow_mid = client.get("/hub/v1/windows/s-layer/w-layer/shadow").json()
    assert shadow_mid["reported"]["base_cells"] == original_base_copy

    # Step 3: Sparse cell commands while marker is active (one outside, one under marker!)
    res3 = client.post(
        "/hub/v1/windows/s-layer/w-layer/commands",
        headers={"Idempotency-Key": "layer-3"},
        json={
            "desired_version": 3,
            "commands": [
                {"row": 0, "col": 9, "opacity": 0.9},
                {"row": 4, "col": 4, "opacity": 0.8},  # underneath marker
            ],
            "note": "manual patch while marker active",
        },
    )
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["status"] == "applied"
    assert d3["effective_cells"][0][9] == 0.9
    # Marker remains cleanly on top of (4, 4)
    assert d3["effective_cells"][4][4] == pattern[2][2]

    # Step 4: Disable marker overlay -> restores base_cells INCLUDING patch under marker!
    res4 = client.post(
        "/hub/v1/windows/s-layer/w-layer/marker-overlay",
        headers={"Idempotency-Key": "layer-4"},
        json={"desired_version": 4, "enabled": False},
    )
    assert res4.status_code == 200
    d4 = res4.json()
    assert d4["status"] == "applied"
    assert d4["marker_overlay"]["enabled"] is False

    expected_restored = [row[:] for row in original_base_copy]
    expected_restored[0][9] = 0.9
    expected_restored[4][4] = 0.8
    assert d4["effective_cells"] == expected_restored
    assert d4["state_digest"] == compute_state_digest(expected_restored, None)
    assert d4["state_digest"] != digest_v1

    # Also verify pure compose_effective_cells does not mutate caller's base_cells
    caller_base = _make_grid(8, 8, 0.4)
    composed = compose_effective_cells(
        caller_base, {"enabled": True, "code_id": 3, "start_row": 1, "start_col": 1}
    )
    assert caller_base == _make_grid(8, 8, 0.4)
    assert composed != caller_base


def test_pdlc_binary_validation_and_idempotency_and_superseded(client: TestClient) -> None:
    # PDLC rejects fractional opacity 0.5
    bad_grid = _make_grid(6, 6, 0.0)
    bad_grid[1][1] = 0.5
    rej = client.put(
        "/hub/v1/windows/s-val/w-pdlc/shadow",
        headers={"Idempotency-Key": "pdlc-bad-1"},
        json={
            "desired_version": 1,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": bad_grid,
        },
    )
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    assert "binary" in rej.json()["rejection_reason"].lower()

    # Valid PDLC v5 succeeds
    good_grid = _make_grid(6, 6, 1.0)
    ok = client.put(
        "/hub/v1/windows/s-val/w-pdlc/shadow",
        headers={"Idempotency-Key": "pdlc-ok-v5"},
        json={
            "desired_version": 5,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": good_grid,
        },
    )
    assert ok.status_code == 200
    ok_body = ok.json()
    assert ok_body["status"] == "applied"
    assert ok_body["reported_version"] == 5

    # Out-of-order v3 (< 5) returns superseded without mutating shadow
    stale_grid = _make_grid(6, 6, 0.0)
    sup = client.put(
        "/hub/v1/windows/s-val/w-pdlc/shadow",
        headers={"Idempotency-Key": "pdlc-stale-v3"},
        json={
            "desired_version": 3,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": stale_grid,
        },
    )
    assert sup.status_code == 200
    assert sup.json()["status"] == "superseded"
    assert sup.json()["reported_version"] == 5
    assert sup.json()["effective_cells"] == good_grid

    # Advance to v6
    v6_grid = _make_grid(6, 6, 0.0)
    res_v6 = client.put(
        "/hub/v1/windows/s-val/w-pdlc/shadow",
        headers={"Idempotency-Key": "pdlc-ok-v6"},
        json={
            "desired_version": 6,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": v6_grid,
        },
    )
    assert res_v6.status_code == 200
    assert res_v6.json()["reported_version"] == 6

    # Strict Idempotency-Key replay for pdlc-ok-v5 returns exact cached v5 response
    # without rolling back the stored v6 shadow!
    replay = client.put(
        "/hub/v1/windows/s-val/w-pdlc/shadow",
        headers={"Idempotency-Key": "pdlc-ok-v5"},
        json={
            "desired_version": 5,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": good_grid,
        },
    )
    assert replay.status_code == 200
    assert replay.json() == ok_body
    # Stored shadow is still v6
    assert client.get("/hub/v1/windows/s-val/w-pdlc/shadow").json()["reported_version"] == 6


def test_hmac_signing_tampering_expiry_replay_and_key_rotation(hub_engine) -> None:
    secret_v1 = "super-secret-key-v1-32bytes-minimum!"
    secret_v2 = "rotated-secret-key-v2-32bytes-minimum!"

    app = create_app(
        database_engine=hub_engine,
        shared_secret=secret_v2,
        prev_secret=secret_v1,
        hub_id="hub-auth-test",
    )

    with TestClient(app) as c:
        # Health endpoints remain open without HMAC headers
        assert c.get("/health").status_code == 200
        assert c.get("/hub/v1/health").status_code == 200

        path = "/hub/v1/windows/s-auth/w-auth/shadow"
        payload = {
            "desired_version": 1,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": _make_grid(6, 6, 0.0),
        }
        raw_body = json.dumps(payload).encode("utf-8")

        # 1. Missing headers -> 401
        r_missing = c.put(path, content=raw_body, headers={"Content-Type": "application/json"})
        assert r_missing.status_code == 401
        assert r_missing.json()["reason"] == "missing_auth_headers"

        # 2. Bad secret -> 401
        bad_headers = sign_request("wrong-secret", "PUT", path, raw_body, key_id="v2")
        bad_headers["Content-Type"] = "application/json"
        r_bad = c.put(path, content=raw_body, headers=bad_headers)
        assert r_bad.status_code == 401
        assert r_bad.json()["reason"] == "invalid_signature"

        # 3. Tampered body -> 401
        tamper_headers = sign_request(secret_v2, "PUT", path, raw_body, key_id="v2")
        tamper_headers["Content-Type"] = "application/json"
        tampered_body = raw_body.replace(b'"desired_version": 1', b'"desired_version": 2')
        r_tamper = c.put(path, content=tampered_body, headers=tamper_headers)
        assert r_tamper.status_code == 401
        assert r_tamper.json()["reason"] == "invalid_signature"

        # 4. Expired timestamp (> 60s old) -> 401
        expired_headers = sign_request(
            secret_v2,
            "PUT",
            path,
            raw_body,
            key_id="v2",
            timestamp=int(time.time()) - 120,
        )
        expired_headers["Content-Type"] = "application/json"
        r_expired = c.put(path, content=raw_body, headers=expired_headers)
        assert r_expired.status_code == 401
        assert r_expired.json()["reason"] == "timestamp_skew_exceeded"

        # 5. Valid signed request with primary secret -> 200
        ok_headers = sign_request(secret_v2, "PUT", path, raw_body, key_id="v2")
        ok_headers["Content-Type"] = "application/json"
        ok_headers["Idempotency-Key"] = "auth-cmd-1"
        r_ok = c.put(path, content=raw_body, headers=ok_headers)
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "applied"

        # 6. Nonce replay (exact same signed headers sent again) -> 401
        r_replay = c.put(path, content=raw_body, headers=ok_headers)
        assert r_replay.status_code == 401
        assert r_replay.json()["reason"] == "replay_detected"

        # 7. Key rotation: prev_secret (v1) accepted on GET with query string
        get_path_q = f"{path}?view=full"
        rot_headers = sign_request(secret_v1, "GET", get_path_q, b"", key_id="v1")
        r_rot = c.get(get_path_q, headers=rot_headers)
        assert r_rot.status_code == 200
        assert r_rot.json()["reported_version"] == 1


def test_nonce_replay_cache_ttl_and_max_entries() -> None:
    cache = NonceReplayCache(ttl_seconds=10.0, max_entries=3)
    assert cache.check_and_store("v1", "n1", now=100.0) is True
    assert cache.check_and_store("v1", "n1", now=105.0) is False  # replay within TTL
    assert cache.check_and_store("v1", "n2", now=106.0) is True
    assert cache.check_and_store("v1", "n3", now=107.0) is True
    assert len(cache) == 3
    # Adding 4th evicts oldest (n1)
    assert cache.check_and_store("v1", "n4", now=108.0) is True
    assert len(cache) == 3
    # After TTL expires, n2 can be reused
    assert cache.check_and_store("v1", "n2", now=120.0) is True


def test_thinq_on_sgf_sync_and_matter_level(client: TestClient) -> None:
    matrix = [
        [0.0, 0.25, 0.5, 1.0],
        [0.0, 0.35, 0.75, 1.0],
    ]
    res = client.post(
        "/hub/v1/thinq/sync",
        headers={
            "Idempotency-Key": "thinq-cmd-1",
            "X-Client-Id": "client-uuid-1234",
            "X-Message-Id": "msg-trace-5678",
            "X-Protocol-Version": "LG_THINQ_ON_SGF_V1",
        },
        json={
            "hub_device_id": "thinq_on_living_01",
            "protocol_version": "LG_THINQ_ON_SGF_V1",
            "scene_id": "scene-thinq",
            "window_id": "win-south",
            "hardware_mode": "LG_SGF",
            "desired_version": 1,
            "cell_opacity_matrix": matrix,
            "energy_daylight_score": 0.72,
            "decision_summary_ko": "LG ThinQ ON SGF 제어",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["hub_device_id"] == "thinq_on_living_01"
    assert body["command_id"] == "thinq-cmd-1"
    assert body["status"] == "applied"
    assert body["desired_version"] == 1
    assert body["reported_version"] == 1
    assert body["effective_cells"] == matrix
    assert body["active_shaded_cells"] == 6  # > 0.01
    assert body["dimmed_cells"] == 4  # 0.01 < v < 0.99
    assert body["matter_level_pct_x100"] == compute_matter_level_pct_x100(matrix)
    assert body["latency_ms"] >= 1
    assert body["rejection_reason"] is None
    assert "ETag" in res.headers

    # Replay returns identical response
    replay = client.post(
        "/hub/v1/thinq/sync",
        headers={"Idempotency-Key": "thinq-cmd-1"},
        json={
            "hub_device_id": "thinq_on_living_01",
            "scene_id": "scene-thinq",
            "window_id": "win-south",
            "desired_version": 1,
            "cell_opacity_matrix": matrix,
        },
    )
    assert replay.status_code == 200
    assert replay.json() == body

    # Unsupported protocol version is rejected
    bad_proto = client.post(
        "/hub/v1/thinq/sync",
        headers={"Idempotency-Key": "thinq-bad-proto"},
        json={
            "hub_device_id": "thinq_on_living_01",
            "protocol_version": "LEGACY_V0",
            "scene_id": "scene-thinq",
            "window_id": "win-south",
            "desired_version": 2,
            "cell_opacity_matrix": matrix,
        },
    )
    assert bad_proto.status_code == 200
    assert bad_proto.json()["status"] == "rejected"
    assert "LEGACY_V0" in bad_proto.json()["rejection_reason"]


def test_fault_injection_reject_offline_timeout_drift_and_reconcile(client: TestClient) -> None:
    grid_a = _make_grid(6, 6, 0.0)
    grid_a[0][0] = 1.0
    grid_b = _make_grid(6, 6, 0.5)

    r_a = client.put(
        "/hub/v1/windows/s-rec/w-a/shadow",
        headers={"Idempotency-Key": "rec-a-1"},
        json={
            "desired_version": 1,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": grid_a,
        },
    )
    r_b = client.put(
        "/hub/v1/windows/s-rec/w-b/shadow",
        headers={"Idempotency-Key": "rec-b-1"},
        json={
            "desired_version": 1,
            "hardware_mode": "LG_SGF",
            "rows": 6,
            "cols": 6,
            "base_cells": grid_b,
        },
    )
    digest_a = r_a.json()["state_digest"]
    digest_b = r_b.json()["state_digest"]

    # 1. Fault mode: reject
    f_rej = client.post("/hub/v1/sim/faults", json={"mode": "reject"})
    assert f_rej.status_code == 200
    rej_res = client.post(
        "/hub/v1/windows/s-rec/w-a/commands",
        headers={"Idempotency-Key": "rec-rej-1"},
        json={"desired_version": 2, "commands": [{"row": 1, "col": 1, "opacity": 1.0}]},
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["status"] == "rejected"
    assert "reject" in rej_res.json()["rejection_reason"].lower()

    # 2. Fault mode: offline -> 503
    client.post("/hub/v1/sim/faults", json={"mode": "offline"})
    assert client.get("/hub/v1/health").json()["simulated_fault_mode"] == "offline"
    off_res = client.get("/hub/v1/windows/s-rec/w-a/shadow")
    assert off_res.status_code == 503

    # 3. Fault mode: timeout -> 504
    client.post("/hub/v1/sim/faults", json={"mode": "timeout", "delay_ms": 0})
    to_res = client.get("/hub/v1/windows/s-rec/w-a/shadow")
    assert to_res.status_code == 504

    # Reset fault to none and re-apply w-a cleanly so sync_status is in_sync
    client.post("/hub/v1/sim/faults", json={"mode": "none"})
    heal_a = client.put(
        "/hub/v1/windows/s-rec/w-a/shadow",
        headers={"Idempotency-Key": "rec-a-reset"},
        json={
            "desired_version": 1,
            "hardware_mode": "PDLC",
            "rows": 6,
            "cols": 6,
            "base_cells": grid_a,
        },
    )
    assert heal_a.json()["status"] == "applied"

    # 4. Fault mode: drift scoped ONLY to w-b
    drift_res = client.post(
        "/hub/v1/sim/faults",
        json={"mode": "drift", "scene_id": "s-rec", "window_id": "w-b"},
    )
    assert drift_res.status_code == 200
    assert len(drift_res.json()["drifted_windows"]) == 1
    assert drift_res.json()["drifted_windows"][0]["window_id"] == "w-b"

    shadow_b = client.get("/hub/v1/windows/s-rec/w-b/shadow").json()
    assert shadow_b["sync_status"] == "drifted"
    assert shadow_b["delta"]["in_sync"] is False
    assert len(shadow_b["delta"]["changed_cells"]) >= 1

    # 5. Bulk /reconcile/digests detects in_sync (w-a), drifted (w-b), missing (w-missing)
    reconcile = client.post(
        "/hub/v1/reconcile/digests",
        json={
            "windows": [
                {
                    "scene_id": "s-rec",
                    "window_id": "w-a",
                    "desired_version": 1,
                    "state_digest": digest_a,
                },
                {
                    "scene_id": "s-rec",
                    "window_id": "w-b",
                    "desired_version": 1,
                    "state_digest": digest_b,
                },
                {
                    "scene_id": "s-rec",
                    "window_id": "w-missing",
                    "desired_version": 1,
                    "state_digest": "0" * 64,
                },
            ]
        },
    )
    assert reconcile.status_code == 200
    rec_body = reconcile.json()
    assert isinstance(rec_body["hub_boot_id"], str)
    assert [w["window_id"] for w in rec_body["in_sync"]] == ["w-a"]
    assert [w["window_id"] for w in rec_body["drifted"]] == ["w-b"]
    assert [w["window_id"] for w in rec_body["missing"]] == ["w-missing"]

    # 6. Healing push via PUT /shadow at same desired_version restores w-b to in_sync
    heal_b = client.put(
        "/hub/v1/windows/s-rec/w-b/shadow",
        headers={"Idempotency-Key": "rec-b-heal"},
        json={
            "desired_version": 1,
            "hardware_mode": "LG_SGF",
            "rows": 6,
            "cols": 6,
            "base_cells": grid_b,
        },
    )
    assert heal_b.status_code == 200
    assert heal_b.json()["status"] == "applied"
    assert heal_b.json()["state_digest"] == digest_b

    reconcile_after = client.post(
        "/hub/v1/reconcile/digests",
        json={
            "windows": [
                {
                    "scene_id": "s-rec",
                    "window_id": "w-a",
                    "desired_version": 1,
                    "state_digest": digest_a,
                },
                {
                    "scene_id": "s-rec",
                    "window_id": "w-b",
                    "desired_version": 1,
                    "state_digest": digest_b,
                },
            ]
        },
    ).json()
    assert len(reconcile_after["in_sync"]) == 2
    assert reconcile_after["drifted"] == []
    assert reconcile_after["missing"] == []


def test_websocket_telemetry_stream_and_ws_hmac(hub_engine) -> None:
    app = create_app(database_engine=hub_engine, shared_secret="", hub_id="hub-ws-test")
    with TestClient(app) as c, c.websocket_connect("/hub/v1/ws") as ws:
        # Heartbeat ping
        ws.send_text("ping")
        hb = ws.receive_json()
        assert hb["event"] == "heartbeat"
        assert hb["hub_id"] == "hub-ws-test"

        # Trigger shadow_applied
        grid = _make_grid(6, 6, 0.0)
        r1 = c.put(
            "/hub/v1/windows/s-ws/w-ws/shadow",
            headers={"Idempotency-Key": "ws-cmd-1"},
            json={
                "desired_version": 1,
                "hardware_mode": "PDLC",
                "rows": 6,
                "cols": 6,
                "base_cells": grid,
            },
        )
        assert r1.status_code == 200
        ev1 = ws.receive_json()
        assert ev1["event"] == "shadow_applied"
        assert ev1["scene_id"] == "s-ws"
        assert ev1["reported_version"] == 1

        # Trigger thinq_synced
        r2 = c.post(
            "/hub/v1/thinq/sync",
            headers={
                "Idempotency-Key": "ws-thinq-1",
                "X-Client-Id": "ws-client",
                "X-Message-Id": "ws-msg",
            },
            json={
                "scene_id": "s-ws",
                "window_id": "w-ws",
                "hardware_mode": "LG_SGF",
                "desired_version": 2,
                "cell_opacity_matrix": _make_grid(6, 6, 0.5),
            },
        )
        assert r2.status_code == 200
        ev2 = ws.receive_json()
        assert ev2["event"] == "thinq_synced"
        assert ev2["reported_version"] == 2
        assert ev2["client_id"] == "ws-client"

        # Trigger fault_injected
        r3 = c.post("/hub/v1/sim/faults", json={"mode": "delay", "delay_ms": 5})
        assert r3.status_code == 200
        ev3 = ws.receive_json()
        assert ev3["event"] == "fault_injected"
        assert ev3["mode"] == "delay"

    # With HMAC enabled, unsigned WS is rejected and signed WS succeeds
    secret = "ws-hmac-secret-32bytes-long-value!"
    secured_app = create_app(database_engine=hub_engine, shared_secret=secret, hub_id="hub-ws-sec")
    with TestClient(secured_app) as c_sec:
        with pytest.raises(WebSocketDisconnect), c_sec.websocket_connect("/hub/v1/ws") as ws_bad:
            ws_bad.receive_json()

        ws_headers = sign_request(secret, "GET", "/hub/v1/ws", b"", key_id="v1")
        with c_sec.websocket_connect("/hub/v1/ws", headers=ws_headers) as ws_ok:
            ws_ok.send_text("ping")
            msg: dict[str, Any] = ws_ok.receive_json()
            assert msg["event"] == "heartbeat"


def test_hub_persistence_and_new_boot_id_across_restart(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'restart.db'}"

    eng1 = create_hub_engine(db_url)
    app1 = create_app(database_engine=eng1, shared_secret="", hub_id="hub-persist")
    with TestClient(app1) as c1:
        boot_1 = c1.get("/hub/v1/health").json()["hub_boot_id"]
        put_res = c1.put(
            "/hub/v1/windows/s-boot/w-boot/shadow",
            headers={"Idempotency-Key": "boot-cmd-1"},
            json={
                "desired_version": 1,
                "hardware_mode": "PDLC",
                "rows": 6,
                "cols": 6,
                "base_cells": _make_grid(6, 6, 1.0),
            },
        )
        assert put_res.status_code == 200
        digest_1 = put_res.json()["state_digest"]
    eng1.dispose()

    eng2 = create_hub_engine(db_url)
    app2 = create_app(database_engine=eng2, shared_secret="", hub_id="hub-persist")
    with TestClient(app2) as c2:
        health_2 = c2.get("/hub/v1/health").json()
        assert health_2["hub_boot_id"] != boot_1
        assert health_2["managed_windows"] == 1
        shadow = c2.get("/hub/v1/windows/s-boot/w-boot/shadow").json()
        assert shadow["reported_version"] == 1
        assert shadow["state_digest"] == digest_1
    eng2.dispose()
