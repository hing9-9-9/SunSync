"""FastAPI application for the standalone SunSync Smart Film & ThinQ ON Hub Simulator."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlmodel import Session, select
from starlette.datastructures import Headers

from sunsync_hub.auth import HubHMACVerifier
from sunsync_hub.db import create_hub_engine, init_db
from sunsync_hub.driver import (
    FaultMode,
    HardwareDriverProtocol,
    HardwareMode,
    SimulatedActuatorDriver,
    compute_matter_level_pct_x100,
)
from sunsync_hub.models import HubCommandReceipt, HubWindowShadow, utc_now_iso
from sunsync_hub.shadow import (
    compose_effective_cells,
    compute_shadow_delta,
    compute_state_digest,
    default_marker_overlay,
    format_etag,
    normalize_marker_overlay,
    quantize_cells,
)

MAX_REQUEST_BODY_BYTES = 16_000_000


class HubBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MarkerOverlayPayload(HubBaseModel):
    enabled: bool = False
    code_id: int | None = None
    start_row: int | None = None
    start_col: int | None = None


class PutShadowRequest(HubBaseModel):
    desired_version: int = Field(ge=0)
    hardware_mode: HardwareMode = "PDLC"
    rows: int = Field(ge=1, le=200)
    cols: int = Field(ge=1, le=200)
    base_cells: list[list[float]]
    marker_overlay: MarkerOverlayPayload | None = None
    command_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CellPatch(HubBaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    opacity: float


class WindowCommandsRequest(HubBaseModel):
    desired_version: int = Field(ge=0)
    commands: list[CellPatch]
    hardware_mode: HardwareMode | None = None
    rows: int | None = Field(default=None, ge=1, le=200)
    cols: int | None = Field(default=None, ge=1, le=200)
    timestamp: str | None = None
    note: str | None = None
    command_id: str | None = None


class MarkerOverlayRequest(HubBaseModel):
    desired_version: int = Field(ge=0)
    enabled: bool
    code_id: int | None = None
    start_row: int | None = None
    start_col: int | None = None
    hardware_mode: HardwareMode | None = None
    rows: int | None = Field(default=None, ge=1, le=200)
    cols: int | None = Field(default=None, ge=1, le=200)
    command_id: str | None = None


class ThinQSyncRequest(HubBaseModel):
    hub_device_id: str = "thinq_on_hub_01"
    protocol_version: str = "LG_THINQ_ON_SGF_V1"
    scene_id: str
    window_id: str
    timestamp: str | None = None
    hardware_mode: HardwareMode = "LG_SGF"
    cell_opacity_matrix: list[list[float]]
    cell_transmittance_matrix: list[list[float]] | None = None
    active_shaded_cells: int | None = None
    dimmed_cells: int | None = None
    energy_daylight_score: float | None = None
    decision_summary_ko: str | None = None
    desired_version: int = Field(default=1, ge=0)
    command_id: str | None = None
    marker_overlay: MarkerOverlayPayload | None = None


class WindowDigestCheck(HubBaseModel):
    scene_id: str
    window_id: str
    desired_version: int = Field(ge=0)
    state_digest: str


class ReconcileDigestsRequest(HubBaseModel):
    windows: list[WindowDigestCheck] = Field(default_factory=list)


class FaultInjectionRequest(HubBaseModel):
    mode: FaultMode = "none"
    delay_ms: int = Field(default=0, ge=0, le=30_000)
    scene_id: str | None = None
    window_id: str | None = None


class EventBroadcaster:
    """In-memory WebSocket broadcast hub for real-time telemetry events."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_json(event)
            except (WebSocketDisconnect, RuntimeError, OSError):
                stale.append(client)
        for client in stale:
            self._clients.discard(client)


class HubHMACMiddleware:
    """ASGI middleware enforcing HMAC-SHA256 on /hub/v1/* (except /hub/v1/health)."""

    def __init__(self, app: Any, verifier: HubHMACVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if (
            not self.verifier.is_enabled
            or not path.startswith("/hub/v1/")
            or path == "/hub/v1/health"
        ):
            await self.app(scope, receive, send)
            return

        messages: list[dict[str, Any]] = []
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body_part = message.get("body", b"")
            total += len(body_part)
            if total > MAX_REQUEST_BODY_BYTES:
                response = JSONResponse(
                    {"detail": "Request body exceeds 16 MB"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            chunks.append(body_part)
            messages.append(message)
            if not message.get("more_body", False):
                break

        raw_body = b"".join(chunks)
        query = scope.get("query_string", b"").decode("latin-1")
        headers = Headers(scope=scope)
        verification = self.verifier.verify(
            method=scope.get("method", "GET"),
            path=path,
            query=query,
            headers=headers,
            raw_body=raw_body,
        )
        if not verification.ok:
            response = JSONResponse(
                {
                    "detail": f"Unauthorized: {verification.reason}",
                    "reason": verification.reason,
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        async def replay() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)


def _shadow_key(scene_id: str, window_id: str) -> str:
    return f"{scene_id}:{window_id}"


def _resolve_command_id(
    idempotency_key: str | None,
    body_command_id: str | None = None,
    message_id: str | None = None,
) -> str:
    for candidate in (idempotency_key, body_command_id, message_id):
        if candidate and candidate.strip():
            return candidate.strip()
    return str(uuid.uuid4())


def _etag_matches(if_match: str, current_etag: str) -> bool:
    raw = if_match.strip()
    if raw == "*":
        return True
    candidates = [token.strip() for token in raw.split(",")]
    unquoted_current = current_etag.strip('"')
    for token in candidates:
        normalized = token[2:].strip() if token.startswith("W/") else token
        if normalized == current_etag or normalized.strip('"') == unquoted_current:
            return True
    return False


def _response_with_etag(payload: dict[str, Any], etag: str | None = None) -> JSONResponse:
    headers: dict[str, str] = {}
    if etag:
        headers["ETag"] = etag
    return JSONResponse(content=payload, status_code=200, headers=headers)


def _replay_receipt_if_present(session: Session, command_id: str) -> JSONResponse | None:
    receipt = session.get(HubCommandReceipt, command_id)
    if receipt is None:
        return None
    cached = json.loads(receipt.response_json)
    etag: str | None = None
    digest = cached.get("state_digest")
    reported_ver = cached.get("reported_version")
    if isinstance(digest, str) and digest and isinstance(reported_ver, int):
        etag = format_etag(reported_ver, digest)
    return _response_with_etag(cached, etag)


def _count_shaded_and_dimmed(cells: list[list[float]]) -> tuple[int, int]:
    active = sum(1 for row in cells for v in row if v > 0.01)
    dimmed = sum(1 for row in cells for v in row if 0.01 < v < 0.99)
    return active, dimmed


def _validate_matrix_shape(matrix: list[list[float]], rows: int, cols: int) -> str | None:
    if len(matrix) != rows:
        return f"Matrix row count {len(matrix)} does not match expected rows={rows}"
    for r, row in enumerate(matrix):
        if len(row) != cols:
            return f"Matrix col count {len(row)} at row {r} does not match expected cols={cols}"
    return None


def _build_shadow_view(shadow: HubWindowShadow) -> dict[str, Any]:
    desired_base = json.loads(shadow.desired_base_cells_json)
    desired_marker = normalize_marker_overlay(json.loads(shadow.desired_marker_json))
    reported_base = json.loads(shadow.reported_base_cells_json)
    reported_marker = normalize_marker_overlay(json.loads(shadow.reported_marker_json))
    reported_effective = json.loads(shadow.effective_cells_json)
    try:
        desired_effective = compose_effective_cells(desired_base, desired_marker)
    except ValueError:
        desired_effective = desired_base

    delta = compute_shadow_delta(
        desired_effective=desired_effective,
        reported_effective=reported_effective,
        desired_marker=desired_marker,
        reported_marker=reported_marker,
    )
    return {
        "scene_id": shadow.scene_id,
        "window_id": shadow.window_id,
        "hardware_mode": shadow.hardware_mode,
        "rows": shadow.rows,
        "cols": shadow.cols,
        "desired_version": shadow.desired_version,
        "reported_version": shadow.reported_version,
        "sync_status": shadow.sync_status,
        "rejection_reason": shadow.rejection_reason,
        "state_digest": shadow.state_digest,
        "matter_level_pct_x100": compute_matter_level_pct_x100(reported_effective),
        "desired": {
            "base_cells": desired_base,
            "marker_overlay": desired_marker,
            "effective_cells": desired_effective,
        },
        "reported": {
            "base_cells": reported_base,
            "marker_overlay": reported_marker,
            "effective_cells": reported_effective,
        },
        "delta": delta,
        "updated_at": shadow.updated_at,
    }


def create_app(
    database_engine: Engine | None = None,
    shared_secret: str | None = None,
    prev_secret: str | None = None,
    hub_id: str | None = None,
    driver: HardwareDriverProtocol | None = None,
) -> FastAPI:
    """Create and configure a standalone SunSync Film & ThinQ ON Hub Simulator FastAPI app."""
    target_engine = database_engine if database_engine is not None else create_hub_engine()
    db_initialized = False

    def _ensure_db() -> None:
        nonlocal db_initialized
        if not db_initialized:
            init_db(target_engine)
            db_initialized = True

    if database_engine is not None:
        _ensure_db()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _ensure_db()
        yield

    verifier = HubHMACVerifier(
        shared_secret=shared_secret,
        prev_secret=prev_secret,
    )
    actuator: HardwareDriverProtocol = driver if driver is not None else SimulatedActuatorDriver()
    broadcaster = EventBroadcaster()

    resolved_hub_id = (
        hub_id if hub_id is not None else (os.environ.get("SUNSYNC_HUB_ID") or "sunsync-hub-01")
    )
    hub_boot_id = str(uuid.uuid4())
    started_monotonic = time.monotonic()

    app = FastAPI(
        title="SunSync Smart Film & ThinQ ON Hub Simulator",
        version="0.1.0",
        description=(
            "Standalone Device Shadow, Layered ArUco Overlay, and ThinQ ON SGF Hub Simulator."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(HubHMACMiddleware, verifier=verifier)

    def _open_session() -> Session:
        _ensure_db()
        return Session(target_engine)

    async def _check_transport_fault(scene_id: str | None, window_id: str | None) -> None:
        fault = actuator.get_fault_config()
        if not fault.matches(scene_id, window_id):
            return
        if fault.mode == "offline":
            raise HTTPException(
                status_code=503,
                detail="Simulated hub offline (fault_mode=offline)",
            )
        if fault.mode == "timeout":
            if fault.delay_ms > 0:
                await asyncio.sleep(fault.delay_ms / 1000.0)
            raise HTTPException(
                status_code=504,
                detail="Simulated hub timeout (fault_mode=timeout)",
            )

    def _health_payload() -> dict[str, Any]:
        with _open_session() as session:
            managed = len(session.exec(select(HubWindowShadow.shadow_key)).all())
        return {
            "status": "ok",
            "hub_id": resolved_hub_id,
            "hub_boot_id": hub_boot_id,
            "uptime_s": round(time.monotonic() - started_monotonic, 3),
            "managed_windows": managed,
            "simulated_fault_mode": actuator.get_fault_config().mode,
        }

    @app.get("/health")
    async def root_health() -> dict[str, Any]:
        return _health_payload()

    @app.get("/hub/v1/health")
    async def hub_health() -> dict[str, Any]:
        return _health_payload()

    @app.get("/hub/v1/windows/{scene_id}/{window_id}/shadow")
    async def get_window_shadow(scene_id: str, window_id: str) -> JSONResponse:
        await _check_transport_fault(scene_id, window_id)
        with _open_session() as session:
            shadow = session.get(HubWindowShadow, _shadow_key(scene_id, window_id))
            if shadow is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Shadow not found for {scene_id}:{window_id}",
                )
            view = _build_shadow_view(shadow)
            etag = format_etag(shadow.reported_version, shadow.state_digest)
            return _response_with_etag(view, etag)

    @app.put("/hub/v1/windows/{scene_id}/{window_id}/shadow")
    async def put_window_shadow(
        scene_id: str,
        window_id: str,
        payload: PutShadowRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> JSONResponse:
        command_id = _resolve_command_id(idempotency_key, payload.command_id)
        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

        await _check_transport_fault(scene_id, window_id)

        key = _shadow_key(scene_id, window_id)
        now_iso = utc_now_iso()
        ws_event: dict[str, Any] | None = None

        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

            shadow = session.get(HubWindowShadow, key)

            if if_match is not None:
                if shadow is None:
                    if if_match.strip() != "*":
                        raise HTTPException(
                            status_code=412,
                            detail="Precondition failed: shadow does not exist",
                        )
                else:
                    current_etag = format_etag(shadow.reported_version, shadow.state_digest)
                    if not _etag_matches(if_match, current_etag):
                        raise HTTPException(
                            status_code=412,
                            detail=f"Precondition failed: ETag mismatch (current={current_etag})",
                        )

            if shadow is not None and payload.desired_version < shadow.reported_version:
                current_effective = json.loads(shadow.effective_cells_json)
                resp: dict[str, Any] = {
                    "command_id": command_id,
                    "status": "superseded",
                    "desired_version": payload.desired_version,
                    "reported_version": shadow.reported_version,
                    "state_digest": shadow.state_digest,
                    "effective_cells": current_effective,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(current_effective),
                    "applied_at": shadow.updated_at,
                    "latency_ms": 0,
                    "rejection_reason": (
                        f"Superseded: desired_version {payload.desired_version} "
                        f"< reported_version {shadow.reported_version}"
                    ),
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=scene_id,
                        window_id=window_id,
                        operation="put_shadow",
                        desired_version=payload.desired_version,
                        status="superseded",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(
                    resp, format_etag(shadow.reported_version, shadow.state_digest)
                )

            marker_dict = normalize_marker_overlay(
                payload.marker_overlay.model_dump() if payload.marker_overlay is not None else None
            )

            rejection_reason: str | None = _validate_matrix_shape(
                payload.base_cells, payload.rows, payload.cols
            )
            q_base: list[list[float]] = []
            effective_cells: list[list[float]] = []
            if rejection_reason is None:
                try:
                    q_base = quantize_cells(payload.base_cells)
                    effective_cells = compose_effective_cells(q_base, marker_dict)
                except (ValueError, TypeError) as exc:
                    rejection_reason = str(exc)

            latency_ms = 1
            if rejection_reason is None:
                actuation = actuator.actuate(
                    scene_id=scene_id,
                    window_id=window_id,
                    hardware_mode=payload.hardware_mode,
                    effective_cells=effective_cells,
                )
                latency_ms = actuation.latency_ms
                if not actuation.accepted:
                    rejection_reason = actuation.rejection_reason or "Actuator rejected command"

            if rejection_reason is not None:
                if shadow is not None:
                    shadow.desired_version = max(shadow.desired_version, payload.desired_version)
                    shadow.sync_status = "rejected"
                    shadow.rejection_reason = rejection_reason
                    shadow.updated_at = now_iso
                    session.add(shadow)
                    rep_effective = json.loads(shadow.effective_cells_json)
                    rep_version = shadow.reported_version
                    rep_digest = shadow.state_digest
                    etag_val: str | None = format_etag(rep_version, rep_digest)
                else:
                    rep_effective = []
                    rep_version = 0
                    rep_digest = ""
                    etag_val = None

                resp = {
                    "command_id": command_id,
                    "status": "rejected",
                    "desired_version": payload.desired_version,
                    "reported_version": rep_version,
                    "state_digest": rep_digest,
                    "effective_cells": rep_effective,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(rep_effective),
                    "applied_at": now_iso,
                    "latency_ms": latency_ms,
                    "rejection_reason": rejection_reason,
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=scene_id,
                        window_id=window_id,
                        operation="put_shadow",
                        desired_version=payload.desired_version,
                        status="rejected",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(resp, etag_val)

            state_digest = compute_state_digest(effective_cells, marker_dict)
            matter_level = compute_matter_level_pct_x100(effective_cells)
            base_json = json.dumps(q_base)
            marker_json = json.dumps(marker_dict)
            effective_json = json.dumps(effective_cells)

            if shadow is None:
                shadow = HubWindowShadow(
                    shadow_key=key,
                    scene_id=scene_id,
                    window_id=window_id,
                    hardware_mode=payload.hardware_mode,
                    rows=payload.rows,
                    cols=payload.cols,
                    desired_version=payload.desired_version,
                    reported_version=payload.desired_version,
                    desired_base_cells_json=base_json,
                    desired_marker_json=marker_json,
                    reported_base_cells_json=base_json,
                    reported_marker_json=marker_json,
                    effective_cells_json=effective_json,
                    state_digest=state_digest,
                    sync_status="in_sync",
                    rejection_reason=None,
                    updated_at=now_iso,
                )
            else:
                shadow.hardware_mode = payload.hardware_mode
                shadow.rows = payload.rows
                shadow.cols = payload.cols
                shadow.desired_version = payload.desired_version
                shadow.reported_version = payload.desired_version
                shadow.desired_base_cells_json = base_json
                shadow.desired_marker_json = marker_json
                shadow.reported_base_cells_json = base_json
                shadow.reported_marker_json = marker_json
                shadow.effective_cells_json = effective_json
                shadow.state_digest = state_digest
                shadow.sync_status = "in_sync"
                shadow.rejection_reason = None
                shadow.updated_at = now_iso

            resp = {
                "command_id": command_id,
                "status": "applied",
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "matter_level_pct_x100": matter_level,
                "applied_at": now_iso,
                "latency_ms": latency_ms,
                "rejection_reason": None,
            }
            session.add(shadow)
            session.add(
                HubCommandReceipt(
                    command_id=command_id,
                    scene_id=scene_id,
                    window_id=window_id,
                    operation="put_shadow",
                    desired_version=payload.desired_version,
                    status="applied",
                    response_json=json.dumps(resp),
                    created_at=now_iso,
                )
            )
            session.commit()
            ws_event = {
                "event": "shadow_applied",
                "scene_id": scene_id,
                "window_id": window_id,
                "command_id": command_id,
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "matter_level_pct_x100": matter_level,
                "timestamp": now_iso,
            }
            etag = format_etag(payload.desired_version, state_digest)

        if ws_event is not None:
            await broadcaster.broadcast(ws_event)
        return _response_with_etag(resp, etag)

    @app.post("/hub/v1/windows/{scene_id}/{window_id}/commands")
    async def post_window_commands(
        scene_id: str,
        window_id: str,
        payload: WindowCommandsRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        command_id = _resolve_command_id(idempotency_key, payload.command_id)
        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

        await _check_transport_fault(scene_id, window_id)

        key = _shadow_key(scene_id, window_id)
        now_iso = utc_now_iso()
        ws_event: dict[str, Any] | None = None

        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

            shadow = session.get(HubWindowShadow, key)

            if shadow is not None and payload.desired_version < shadow.reported_version:
                current_effective = json.loads(shadow.effective_cells_json)
                resp: dict[str, Any] = {
                    "command_id": command_id,
                    "status": "superseded",
                    "desired_version": payload.desired_version,
                    "reported_version": shadow.reported_version,
                    "state_digest": shadow.state_digest,
                    "effective_cells": current_effective,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(current_effective),
                    "applied_at": shadow.updated_at,
                    "latency_ms": 0,
                    "rejection_reason": (
                        f"Superseded: desired_version {payload.desired_version} "
                        f"< reported_version {shadow.reported_version}"
                    ),
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=scene_id,
                        window_id=window_id,
                        operation="commands",
                        desired_version=payload.desired_version,
                        status="superseded",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(
                    resp, format_etag(shadow.reported_version, shadow.state_digest)
                )

            if shadow is None:
                rows = payload.rows if payload.rows is not None else 10
                cols = payload.cols if payload.cols is not None else 10
                hardware_mode: str = payload.hardware_mode or "PDLC"
                base_cells = [[0.0 for _ in range(cols)] for _ in range(rows)]
                marker_dict = default_marker_overlay()
            else:
                rows = shadow.rows
                cols = shadow.cols
                hardware_mode = payload.hardware_mode or shadow.hardware_mode
                base_cells = [row[:] for row in json.loads(shadow.reported_base_cells_json)]
                marker_dict = normalize_marker_overlay(json.loads(shadow.reported_marker_json))

            rejection_reason: str | None = None
            for cmd in payload.commands:
                if not (0 <= cmd.row < rows and 0 <= cmd.col < cols):
                    rejection_reason = (
                        f"Cell coordinate ({cmd.row}, {cmd.col}) out of bounds for {rows}x{cols}"
                    )
                    break
                base_cells[cmd.row][cmd.col] = cmd.opacity

            q_base: list[list[float]] = []
            effective_cells: list[list[float]] = []
            if rejection_reason is None:
                try:
                    q_base = quantize_cells(base_cells)
                    effective_cells = compose_effective_cells(q_base, marker_dict)
                except (ValueError, TypeError) as exc:
                    rejection_reason = str(exc)

            latency_ms = 1
            if rejection_reason is None:
                # Validate base_cells too so PDLC rejects invalid opacities even under a marker.
                base_validation = actuator.validate_cells(hardware_mode, q_base)
                if base_validation is not None:
                    rejection_reason = base_validation
                else:
                    actuation = actuator.actuate(
                        scene_id=scene_id,
                        window_id=window_id,
                        hardware_mode=hardware_mode,
                        effective_cells=effective_cells,
                    )
                    latency_ms = actuation.latency_ms
                    if not actuation.accepted:
                        rejection_reason = actuation.rejection_reason or "Actuator rejected command"

            if rejection_reason is not None:
                if shadow is not None:
                    shadow.desired_version = max(shadow.desired_version, payload.desired_version)
                    shadow.sync_status = "rejected"
                    shadow.rejection_reason = rejection_reason
                    shadow.updated_at = now_iso
                    session.add(shadow)
                    rep_effective = json.loads(shadow.effective_cells_json)
                    rep_version = shadow.reported_version
                    rep_digest = shadow.state_digest
                    etag_val: str | None = format_etag(rep_version, rep_digest)
                else:
                    rep_effective = []
                    rep_version = 0
                    rep_digest = ""
                    etag_val = None

                resp = {
                    "command_id": command_id,
                    "status": "rejected",
                    "desired_version": payload.desired_version,
                    "reported_version": rep_version,
                    "state_digest": rep_digest,
                    "effective_cells": rep_effective,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(rep_effective),
                    "applied_at": now_iso,
                    "latency_ms": latency_ms,
                    "rejection_reason": rejection_reason,
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=scene_id,
                        window_id=window_id,
                        operation="commands",
                        desired_version=payload.desired_version,
                        status="rejected",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(resp, etag_val)

            state_digest = compute_state_digest(effective_cells, marker_dict)
            matter_level = compute_matter_level_pct_x100(effective_cells)
            base_json = json.dumps(q_base)
            marker_json = json.dumps(marker_dict)
            effective_json = json.dumps(effective_cells)

            if shadow is None:
                shadow = HubWindowShadow(
                    shadow_key=key,
                    scene_id=scene_id,
                    window_id=window_id,
                    hardware_mode=hardware_mode,
                    rows=rows,
                    cols=cols,
                    desired_version=payload.desired_version,
                    reported_version=payload.desired_version,
                    desired_base_cells_json=base_json,
                    desired_marker_json=marker_json,
                    reported_base_cells_json=base_json,
                    reported_marker_json=marker_json,
                    effective_cells_json=effective_json,
                    state_digest=state_digest,
                    sync_status="in_sync",
                    rejection_reason=None,
                    updated_at=now_iso,
                )
            else:
                shadow.hardware_mode = hardware_mode
                shadow.desired_version = payload.desired_version
                shadow.reported_version = payload.desired_version
                shadow.desired_base_cells_json = base_json
                shadow.desired_marker_json = marker_json
                shadow.reported_base_cells_json = base_json
                shadow.reported_marker_json = marker_json
                shadow.effective_cells_json = effective_json
                shadow.state_digest = state_digest
                shadow.sync_status = "in_sync"
                shadow.rejection_reason = None
                shadow.updated_at = now_iso

            resp = {
                "command_id": command_id,
                "status": "applied",
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "matter_level_pct_x100": matter_level,
                "applied_at": now_iso,
                "latency_ms": latency_ms,
                "rejection_reason": None,
            }
            session.add(shadow)
            session.add(
                HubCommandReceipt(
                    command_id=command_id,
                    scene_id=scene_id,
                    window_id=window_id,
                    operation="commands",
                    desired_version=payload.desired_version,
                    status="applied",
                    response_json=json.dumps(resp),
                    created_at=now_iso,
                )
            )
            session.commit()
            ws_event = {
                "event": "shadow_applied",
                "scene_id": scene_id,
                "window_id": window_id,
                "command_id": command_id,
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "matter_level_pct_x100": matter_level,
                "timestamp": now_iso,
            }
            etag = format_etag(payload.desired_version, state_digest)

        if ws_event is not None:
            await broadcaster.broadcast(ws_event)
        return _response_with_etag(resp, etag)

    @app.post("/hub/v1/windows/{scene_id}/{window_id}/marker-overlay")
    async def post_marker_overlay(
        scene_id: str,
        window_id: str,
        payload: MarkerOverlayRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        command_id = _resolve_command_id(idempotency_key, payload.command_id)
        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

        await _check_transport_fault(scene_id, window_id)

        key = _shadow_key(scene_id, window_id)
        now_iso = utc_now_iso()
        ws_event: dict[str, Any] | None = None

        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

            shadow = session.get(HubWindowShadow, key)

            if shadow is not None and payload.desired_version < shadow.reported_version:
                current_effective = json.loads(shadow.effective_cells_json)
                current_marker = normalize_marker_overlay(json.loads(shadow.reported_marker_json))
                resp: dict[str, Any] = {
                    "command_id": command_id,
                    "status": "superseded",
                    "desired_version": payload.desired_version,
                    "reported_version": shadow.reported_version,
                    "state_digest": shadow.state_digest,
                    "effective_cells": current_effective,
                    "marker_overlay": current_marker,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(current_effective),
                    "applied_at": shadow.updated_at,
                    "latency_ms": 0,
                    "rejection_reason": (
                        f"Superseded: desired_version {payload.desired_version} "
                        f"< reported_version {shadow.reported_version}"
                    ),
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=scene_id,
                        window_id=window_id,
                        operation="marker_overlay",
                        desired_version=payload.desired_version,
                        status="superseded",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(
                    resp, format_etag(shadow.reported_version, shadow.state_digest)
                )

            if shadow is None:
                rows = payload.rows if payload.rows is not None else 10
                cols = payload.cols if payload.cols is not None else 10
                hardware_mode: str = payload.hardware_mode or "PDLC"
                base_cells = [[0.0 for _ in range(cols)] for _ in range(rows)]
            else:
                rows = shadow.rows
                cols = shadow.cols
                hardware_mode = payload.hardware_mode or shadow.hardware_mode
                base_cells = json.loads(shadow.reported_base_cells_json)

            new_marker = normalize_marker_overlay(
                {
                    "enabled": payload.enabled,
                    "code_id": payload.code_id,
                    "start_row": payload.start_row,
                    "start_col": payload.start_col,
                }
            )

            rejection_reason: str | None = None
            effective_cells: list[list[float]] = []
            try:
                effective_cells = compose_effective_cells(base_cells, new_marker)
            except ValueError as exc:
                rejection_reason = str(exc)

            latency_ms = 1
            if rejection_reason is None:
                actuation = actuator.actuate(
                    scene_id=scene_id,
                    window_id=window_id,
                    hardware_mode=hardware_mode,
                    effective_cells=effective_cells,
                )
                latency_ms = actuation.latency_ms
                if not actuation.accepted:
                    rejection_reason = actuation.rejection_reason or "Actuator rejected marker"

            if rejection_reason is not None:
                if shadow is not None:
                    shadow.desired_version = max(shadow.desired_version, payload.desired_version)
                    shadow.sync_status = "rejected"
                    shadow.rejection_reason = rejection_reason
                    shadow.updated_at = now_iso
                    session.add(shadow)
                    rep_effective = json.loads(shadow.effective_cells_json)
                    rep_marker = normalize_marker_overlay(json.loads(shadow.reported_marker_json))
                    rep_version = shadow.reported_version
                    rep_digest = shadow.state_digest
                    etag_val: str | None = format_etag(rep_version, rep_digest)
                else:
                    rep_effective = []
                    rep_marker = default_marker_overlay()
                    rep_version = 0
                    rep_digest = ""
                    etag_val = None

                resp = {
                    "command_id": command_id,
                    "status": "rejected",
                    "desired_version": payload.desired_version,
                    "reported_version": rep_version,
                    "state_digest": rep_digest,
                    "effective_cells": rep_effective,
                    "marker_overlay": rep_marker,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(rep_effective),
                    "applied_at": now_iso,
                    "latency_ms": latency_ms,
                    "rejection_reason": rejection_reason,
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=scene_id,
                        window_id=window_id,
                        operation="marker_overlay",
                        desired_version=payload.desired_version,
                        status="rejected",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(resp, etag_val)

            state_digest = compute_state_digest(effective_cells, new_marker)
            matter_level = compute_matter_level_pct_x100(effective_cells)
            base_json = json.dumps(quantize_cells(base_cells))
            marker_json = json.dumps(new_marker)
            effective_json = json.dumps(effective_cells)

            if shadow is None:
                shadow = HubWindowShadow(
                    shadow_key=key,
                    scene_id=scene_id,
                    window_id=window_id,
                    hardware_mode=hardware_mode,
                    rows=rows,
                    cols=cols,
                    desired_version=payload.desired_version,
                    reported_version=payload.desired_version,
                    desired_base_cells_json=base_json,
                    desired_marker_json=marker_json,
                    reported_base_cells_json=base_json,
                    reported_marker_json=marker_json,
                    effective_cells_json=effective_json,
                    state_digest=state_digest,
                    sync_status="in_sync",
                    rejection_reason=None,
                    updated_at=now_iso,
                )
            else:
                shadow.hardware_mode = hardware_mode
                shadow.desired_version = payload.desired_version
                shadow.reported_version = payload.desired_version
                shadow.desired_base_cells_json = base_json
                shadow.desired_marker_json = marker_json
                shadow.reported_base_cells_json = base_json
                shadow.reported_marker_json = marker_json
                shadow.effective_cells_json = effective_json
                shadow.state_digest = state_digest
                shadow.sync_status = "in_sync"
                shadow.rejection_reason = None
                shadow.updated_at = now_iso

            resp = {
                "command_id": command_id,
                "status": "applied",
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "marker_overlay": new_marker,
                "matter_level_pct_x100": matter_level,
                "applied_at": now_iso,
                "latency_ms": latency_ms,
                "rejection_reason": None,
            }
            session.add(shadow)
            session.add(
                HubCommandReceipt(
                    command_id=command_id,
                    scene_id=scene_id,
                    window_id=window_id,
                    operation="marker_overlay",
                    desired_version=payload.desired_version,
                    status="applied",
                    response_json=json.dumps(resp),
                    created_at=now_iso,
                )
            )
            session.commit()
            ws_event = {
                "event": "shadow_applied",
                "scene_id": scene_id,
                "window_id": window_id,
                "command_id": command_id,
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "marker_overlay": new_marker,
                "matter_level_pct_x100": matter_level,
                "timestamp": now_iso,
            }
            etag = format_etag(payload.desired_version, state_digest)

        if ws_event is not None:
            await broadcaster.broadcast(ws_event)
        return _response_with_etag(resp, etag)

    @app.post("/hub/v1/thinq/sync")
    async def post_thinq_sync(
        payload: ThinQSyncRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
        x_message_id: str | None = Header(default=None, alias="X-Message-Id"),
    ) -> JSONResponse:
        command_id = _resolve_command_id(idempotency_key, payload.command_id, x_message_id)
        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

        await _check_transport_fault(payload.scene_id, payload.window_id)

        key = _shadow_key(payload.scene_id, payload.window_id)
        now_iso = utc_now_iso()
        ws_event: dict[str, Any] | None = None

        with _open_session() as session:
            cached = _replay_receipt_if_present(session, command_id)
            if cached is not None:
                return cached

            shadow = session.get(HubWindowShadow, key)

            if shadow is not None and payload.desired_version < shadow.reported_version:
                current_effective = json.loads(shadow.effective_cells_json)
                active_cnt, dimmed_cnt = _count_shaded_and_dimmed(current_effective)
                resp: dict[str, Any] = {
                    "hub_device_id": payload.hub_device_id,
                    "command_id": command_id,
                    "status": "superseded",
                    "desired_version": payload.desired_version,
                    "reported_version": shadow.reported_version,
                    "state_digest": shadow.state_digest,
                    "effective_cells": current_effective,
                    "active_shaded_cells": active_cnt,
                    "dimmed_cells": dimmed_cnt,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(current_effective),
                    "latency_ms": 0,
                    "applied_at": shadow.updated_at,
                    "rejection_reason": (
                        f"Superseded: desired_version {payload.desired_version} "
                        f"< reported_version {shadow.reported_version}"
                    ),
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=payload.scene_id,
                        window_id=payload.window_id,
                        operation="thinq_sync",
                        desired_version=payload.desired_version,
                        status="superseded",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(
                    resp, format_etag(shadow.reported_version, shadow.state_digest)
                )

            rejection_reason: str | None = None
            if payload.protocol_version != "LG_THINQ_ON_SGF_V1":
                rejection_reason = f"Unsupported ThinQ protocol_version: {payload.protocol_version}"

            rows = len(payload.cell_opacity_matrix)
            cols = len(payload.cell_opacity_matrix[0]) if rows > 0 else 0
            if rejection_reason is None and (rows < 1 or cols < 1):
                rejection_reason = "cell_opacity_matrix must be a non-empty 2D matrix"
            if rejection_reason is None:
                rejection_reason = _validate_matrix_shape(payload.cell_opacity_matrix, rows, cols)

            if payload.marker_overlay is not None:
                marker_dict = normalize_marker_overlay(payload.marker_overlay.model_dump())
            elif shadow is not None:
                marker_dict = normalize_marker_overlay(json.loads(shadow.reported_marker_json))
            else:
                marker_dict = default_marker_overlay()

            q_base: list[list[float]] = []
            effective_cells: list[list[float]] = []
            if rejection_reason is None:
                try:
                    q_base = quantize_cells(payload.cell_opacity_matrix)
                    effective_cells = compose_effective_cells(q_base, marker_dict)
                except (ValueError, TypeError) as exc:
                    rejection_reason = str(exc)

            latency_ms = 1
            if rejection_reason is None:
                actuation = actuator.actuate(
                    scene_id=payload.scene_id,
                    window_id=payload.window_id,
                    hardware_mode=payload.hardware_mode,
                    effective_cells=effective_cells,
                )
                latency_ms = actuation.latency_ms
                if not actuation.accepted:
                    rejection_reason = actuation.rejection_reason or "Actuator rejected ThinQ sync"

            if rejection_reason is not None:
                if shadow is not None:
                    shadow.desired_version = max(shadow.desired_version, payload.desired_version)
                    shadow.sync_status = "rejected"
                    shadow.rejection_reason = rejection_reason
                    shadow.updated_at = now_iso
                    session.add(shadow)
                    rep_effective = json.loads(shadow.effective_cells_json)
                    rep_version = shadow.reported_version
                    rep_digest = shadow.state_digest
                    etag_val: str | None = format_etag(rep_version, rep_digest)
                else:
                    rep_effective = []
                    rep_version = 0
                    rep_digest = ""
                    etag_val = None

                active_cnt, dimmed_cnt = _count_shaded_and_dimmed(rep_effective)
                resp = {
                    "hub_device_id": payload.hub_device_id,
                    "command_id": command_id,
                    "status": "rejected",
                    "desired_version": payload.desired_version,
                    "reported_version": rep_version,
                    "state_digest": rep_digest,
                    "effective_cells": rep_effective,
                    "active_shaded_cells": active_cnt,
                    "dimmed_cells": dimmed_cnt,
                    "matter_level_pct_x100": compute_matter_level_pct_x100(rep_effective),
                    "latency_ms": latency_ms,
                    "applied_at": now_iso,
                    "rejection_reason": rejection_reason,
                }
                session.add(
                    HubCommandReceipt(
                        command_id=command_id,
                        scene_id=payload.scene_id,
                        window_id=payload.window_id,
                        operation="thinq_sync",
                        desired_version=payload.desired_version,
                        status="rejected",
                        response_json=json.dumps(resp),
                        created_at=now_iso,
                    )
                )
                session.commit()
                return _response_with_etag(resp, etag_val)

            state_digest = compute_state_digest(effective_cells, marker_dict)
            matter_level = compute_matter_level_pct_x100(effective_cells)
            active_cnt, dimmed_cnt = _count_shaded_and_dimmed(effective_cells)
            base_json = json.dumps(q_base)
            marker_json = json.dumps(marker_dict)
            effective_json = json.dumps(effective_cells)

            if shadow is None:
                shadow = HubWindowShadow(
                    shadow_key=key,
                    scene_id=payload.scene_id,
                    window_id=payload.window_id,
                    hardware_mode=payload.hardware_mode,
                    rows=rows,
                    cols=cols,
                    desired_version=payload.desired_version,
                    reported_version=payload.desired_version,
                    desired_base_cells_json=base_json,
                    desired_marker_json=marker_json,
                    reported_base_cells_json=base_json,
                    reported_marker_json=marker_json,
                    effective_cells_json=effective_json,
                    state_digest=state_digest,
                    sync_status="in_sync",
                    rejection_reason=None,
                    updated_at=now_iso,
                )
            else:
                shadow.hardware_mode = payload.hardware_mode
                shadow.rows = rows
                shadow.cols = cols
                shadow.desired_version = payload.desired_version
                shadow.reported_version = payload.desired_version
                shadow.desired_base_cells_json = base_json
                shadow.desired_marker_json = marker_json
                shadow.reported_base_cells_json = base_json
                shadow.reported_marker_json = marker_json
                shadow.effective_cells_json = effective_json
                shadow.state_digest = state_digest
                shadow.sync_status = "in_sync"
                shadow.rejection_reason = None
                shadow.updated_at = now_iso

            resp = {
                "hub_device_id": payload.hub_device_id,
                "command_id": command_id,
                "status": "applied",
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "active_shaded_cells": active_cnt,
                "dimmed_cells": dimmed_cnt,
                "matter_level_pct_x100": matter_level,
                "latency_ms": latency_ms,
                "applied_at": now_iso,
                "rejection_reason": None,
            }
            session.add(shadow)
            session.add(
                HubCommandReceipt(
                    command_id=command_id,
                    scene_id=payload.scene_id,
                    window_id=payload.window_id,
                    operation="thinq_sync",
                    desired_version=payload.desired_version,
                    status="applied",
                    response_json=json.dumps(resp),
                    created_at=now_iso,
                )
            )
            session.commit()
            ws_event = {
                "event": "thinq_synced",
                "hub_device_id": payload.hub_device_id,
                "scene_id": payload.scene_id,
                "window_id": payload.window_id,
                "command_id": command_id,
                "client_id": x_client_id,
                "message_id": x_message_id,
                "desired_version": payload.desired_version,
                "reported_version": payload.desired_version,
                "state_digest": state_digest,
                "effective_cells": effective_cells,
                "active_shaded_cells": active_cnt,
                "dimmed_cells": dimmed_cnt,
                "matter_level_pct_x100": matter_level,
                "timestamp": now_iso,
            }
            etag = format_etag(payload.desired_version, state_digest)

        if ws_event is not None:
            await broadcaster.broadcast(ws_event)
        return _response_with_etag(resp, etag)

    @app.post("/hub/v1/reconcile/digests")
    async def reconcile_digests(payload: ReconcileDigestsRequest) -> dict[str, Any]:
        await _check_transport_fault(None, None)

        in_sync: list[dict[str, Any]] = []
        drifted: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []

        with _open_session() as session:
            for item in payload.windows:
                shadow = session.get(HubWindowShadow, _shadow_key(item.scene_id, item.window_id))
                if shadow is None:
                    missing.append(
                        {
                            "scene_id": item.scene_id,
                            "window_id": item.window_id,
                            "desired_version": item.desired_version,
                            "expected_digest": item.state_digest,
                        }
                    )
                    continue

                if (
                    shadow.reported_version == item.desired_version
                    and shadow.state_digest == item.state_digest
                    and shadow.sync_status == "in_sync"
                ):
                    in_sync.append(
                        {
                            "scene_id": item.scene_id,
                            "window_id": item.window_id,
                            "reported_version": shadow.reported_version,
                            "state_digest": shadow.state_digest,
                        }
                    )
                else:
                    drifted.append(
                        {
                            "scene_id": item.scene_id,
                            "window_id": item.window_id,
                            "desired_version": item.desired_version,
                            "reported_version": shadow.reported_version,
                            "expected_digest": item.state_digest,
                            "reported_digest": shadow.state_digest,
                            "sync_status": shadow.sync_status,
                        }
                    )

        return {
            "hub_boot_id": hub_boot_id,
            "in_sync": in_sync,
            "drifted": drifted,
            "missing": missing,
        }

    @app.post("/hub/v1/sim/faults")
    async def configure_simulated_faults(payload: FaultInjectionRequest) -> dict[str, Any]:
        fault = actuator.configure_fault(
            mode=payload.mode,
            delay_ms=payload.delay_ms,
            scene_id=payload.scene_id,
            window_id=payload.window_id,
        )
        now_iso = utc_now_iso()
        drifted_windows: list[dict[str, Any]] = []

        if fault.mode == "drift":
            with _open_session() as session:
                stmt = select(HubWindowShadow)
                if fault.scene_id is not None:
                    stmt = stmt.where(HubWindowShadow.scene_id == fault.scene_id)
                if fault.window_id is not None:
                    stmt = stmt.where(HubWindowShadow.window_id == fault.window_id)
                shadows = session.exec(stmt).all()
                for shadow in shadows:
                    rep_base = json.loads(shadow.reported_base_cells_json)
                    rep_marker = normalize_marker_overlay(json.loads(shadow.reported_marker_json))
                    new_rep_base, new_rep_marker = actuator.perturb_for_drift(
                        shadow.hardware_mode,
                        rep_base,
                        rep_marker,
                    )
                    new_effective = compose_effective_cells(new_rep_base, new_rep_marker)
                    new_digest = compute_state_digest(new_effective, new_rep_marker)

                    shadow.reported_base_cells_json = json.dumps(quantize_cells(new_rep_base))
                    shadow.reported_marker_json = json.dumps(new_rep_marker)
                    shadow.effective_cells_json = json.dumps(new_effective)
                    shadow.state_digest = new_digest
                    shadow.sync_status = "drifted"
                    shadow.updated_at = now_iso
                    session.add(shadow)
                    drifted_windows.append(
                        {
                            "scene_id": shadow.scene_id,
                            "window_id": shadow.window_id,
                            "state_digest": new_digest,
                        }
                    )
                session.commit()

        await broadcaster.broadcast(
            {
                "event": "fault_injected",
                "mode": fault.mode,
                "delay_ms": fault.delay_ms,
                "scene_id": fault.scene_id,
                "window_id": fault.window_id,
                "drifted_windows": drifted_windows,
                "timestamp": now_iso,
            }
        )

        return {
            "status": "ok",
            "mode": fault.mode,
            "delay_ms": fault.delay_ms,
            "scene_id": fault.scene_id,
            "window_id": fault.window_id,
            "drifted_windows": drifted_windows,
        }

    @app.websocket("/hub/v1/ws")
    async def hub_websocket(websocket: WebSocket) -> None:
        if verifier.is_enabled:
            query = websocket.url.query
            verification = verifier.verify(
                method="GET",
                path=websocket.url.path,
                query=query,
                headers=websocket.headers,
                raw_body=b"",
            )
            if not verification.ok:
                await websocket.close(code=1008, reason=f"Unauthorized: {verification.reason}")
                return

        await broadcaster.connect(websocket)
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.strip().lower() == "ping":
                    await websocket.send_json(
                        {
                            "event": "heartbeat",
                            "hub_id": resolved_hub_id,
                            "hub_boot_id": hub_boot_id,
                            "timestamp": utc_now_iso(),
                        }
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            broadcaster.disconnect(websocket)

    return app


app = create_app()
