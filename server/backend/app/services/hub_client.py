"""Signed HTTP client, Device Shadow digest helpers, and Transactional Outbox for SunSync Hub.

Zero external runtime dependencies: uses Python stdlib (`urllib.request`, `hmac`, `hashlib`,
`secrets`, `json`, `time`, `base64`) so production installs never require extra packages.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.config import hub_base_url, hub_key_id, hub_shared_secret, hub_timeout_seconds
from app.models.film import FilmApplicationLogRecord, FilmStateRecord, HubCommandRecord
from app.models.scene import SceneRecord
from app.schemas.film import (
    DeliveryStatus,
    HubReconcileResponse,
    HubReconcileWindowResult,
    HubStatusResponse,
    HubWindowStatusEntry,
    SyncStatus,
)
from app.services.stored_scene import load_stored_scene

RETRYABLE_OUTBOX_STATUSES = ("pending", "dispatched", "timed_out")
_VALID_SYNC_STATUSES: set[str] = {
    "local_only",
    "synced",
    "pending",
    "drifted",
    "rejected",
    "failed",
}
_VALID_DELIVERY_STATUSES: set[str] = {
    "local_only",
    "pending",
    "dispatched",
    "acked",
    "applied",
    "failed",
    "timed_out",
    "superseded",
    "rejected",
}


def utc_now_iso() -> str:
    dt_utc = datetime.now(UTC)
    if dt_utc.microsecond == 0:
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_hub_enabled() -> bool:
    """Return True when `SUNSYNC_HUB_BASE_URL` is configured."""

    return bool(hub_base_url())


def new_command_id() -> str:
    """Generate a chronologically sortable RFC 9562 UUIDv7 string using stdlib only."""

    ts_ms = time.time_ns() // 1_000_000
    rand = secrets.token_bytes(10)
    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6] = (0x70) | (rand[0] & 0x0F)
    b[7] = rand[1]
    b[8] = (0x80) | (rand[2] & 0x3F)
    b[9:16] = rand[3:10]
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def normalize_sync_status(value: str | None) -> SyncStatus:
    if value in _VALID_SYNC_STATUSES:
        return cast(SyncStatus, value)
    return "local_only"


def normalize_delivery_status(value: str | None) -> DeliveryStatus:
    if value in _VALID_DELIVERY_STATUSES:
        return cast(DeliveryStatus, value)
    return "local_only"


def compute_hub_signature(
    method: str,
    path_with_query: str,
    timestamp: str,
    nonce: str,
    raw_body: bytes,
    secret: str,
) -> str:
    """Compute `v1=<hex>` HMAC-SHA256 signature matching `sunsync-hub` verification."""

    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    canonical = f"{method.upper()}\n{path_with_query}\n{timestamp}\n{nonce}\n{body_sha256}"
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v1={digest}"


def build_signed_headers(
    method: str,
    path_with_query: str,
    raw_body: bytes,
    *,
    shared_secret: str | None = None,
    key_id: str | None = None,
    idempotency_key: str | None = None,
    timestamp: str | None = None,
    nonce: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build outbound HTTP headers with HMAC-SHA256 signature and idempotency key."""

    secret = hub_shared_secret() if shared_secret is None else shared_secret
    resolved_key_id = hub_key_id() if key_id is None else key_id

    headers: dict[str, str] = {"Accept": "application/json"}
    if raw_body or method.upper() in ("POST", "PUT", "PATCH"):
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if extra_headers:
        headers.update(extra_headers)
    if secret:
        ts = timestamp if timestamp is not None else str(int(time.time()))
        n = nonce if nonce is not None else secrets.token_urlsafe(16)
        headers["X-Hub-Key-Id"] = resolved_key_id
        headers["X-Hub-Timestamp"] = ts
        headers["X-Hub-Nonce"] = n
        headers["X-Hub-Signature"] = compute_hub_signature(
            method=method,
            path_with_query=path_with_query,
            timestamp=ts,
            nonce=n,
            raw_body=raw_body,
            secret=secret,
        )
    return headers


def compute_state_digest(
    effective_cells: Sequence[Sequence[float]],
    marker_overlay: dict[str, Any] | None = None,
) -> str:
    """Compute canonical SHA-256 digest over quantized effective cells and marker overlay."""

    q_cells = [[round(float(v), 3) for v in row] for row in effective_cells]
    enabled = bool(marker_overlay.get("enabled", False)) if marker_overlay else False
    marker = {
        "enabled": enabled,
        "code_id": (
            int(marker_overlay["code_id"])
            if enabled and marker_overlay and marker_overlay.get("code_id") is not None
            else None
        ),
        "start_row": (
            int(marker_overlay["start_row"])
            if enabled and marker_overlay and marker_overlay.get("start_row") is not None
            else None
        ),
        "start_col": (
            int(marker_overlay["start_col"])
            if enabled and marker_overlay and marker_overlay.get("start_col") is not None
            else None
        ),
    }
    canonical = json.dumps(
        {"cells": q_cells, "marker": marker},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_base_cells_and_overlay(
    record: FilmStateRecord,
) -> tuple[list[list[float]], list[list[float]], dict[str, Any]]:
    """Separate layered `base_cells` and `marker_overlay` from a persisted `FilmStateRecord`."""

    effective_cells: list[list[float]] = json.loads(record.cells_json)
    if record.marker_mode_active:
        base_cells: list[list[float]] = (
            json.loads(record.marker_backup_json) if record.marker_backup_json else effective_cells
        )
        overlay: dict[str, Any] = {
            "enabled": True,
            "code_id": record.marker_code_id,
            "start_row": record.marker_start_row,
            "start_col": record.marker_start_col,
        }
    else:
        base_cells = effective_cells
        overlay = {
            "enabled": False,
            "code_id": None,
            "start_row": None,
            "start_col": None,
        }
    return base_cells, effective_cells, overlay


class HubTransportError(Exception):
    """Error communicating with the standalone Film & ThinQ Hub server."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retryable: bool = True,
        is_timeout: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.retryable = retryable
        self.is_timeout = is_timeout
        self.payload = payload


@dataclass(slots=True)
class DispatchResult:
    command_id: str
    command_status: DeliveryStatus
    sync_status: SyncStatus
    reported_version: int
    hub_ack_at: str | None
    error: str | None
    response: dict[str, Any] | None = None


class HubClient:
    """Synchronous stdlib HTTP client for communicating with `sunsync-hub` (`:8002`)."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        shared_secret: str | None = None,
        key_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (hub_base_url() if base_url is None else base_url).strip().rstrip("/")
        self.shared_secret = hub_shared_secret() if shared_secret is None else shared_secret
        self.key_id = hub_key_id() if key_id is None else key_id
        self.timeout_seconds = (
            hub_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise HubTransportError("SUNSYNC_HUB_BASE_URL is not configured", retryable=False)
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{normalized_path}"
        parsed = urllib.parse.urlsplit(url)
        path_with_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        raw_body = (
            json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
            if body is not None
            else b""
        )
        headers = build_signed_headers(
            method=method,
            path_with_query=path_with_query,
            raw_body=raw_body,
            shared_secret=self.shared_secret,
            key_id=self.key_id,
            idempotency_key=idempotency_key,
            extra_headers=extra_headers,
        )
        req = urllib.request.Request(
            url,
            data=(raw_body if body is not None else None),
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                parsed_json = json.loads(raw.decode("utf-8"))
                return parsed_json if isinstance(parsed_json, dict) else {"data": parsed_json}
        except urllib.error.HTTPError as exc:
            err_body = exc.read()
            err_payload: dict[str, Any] | None = None
            detail = f"Hub HTTP {exc.code}"
            if err_body:
                try:
                    decoded = json.loads(err_body.decode("utf-8"))
                    if isinstance(decoded, dict):
                        err_payload = decoded
                        detail = str(
                            decoded.get("rejection_reason")
                            or decoded.get("detail")
                            or decoded.get("message")
                            or detail
                        )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    detail = (
                        f"Hub HTTP {exc.code}: {err_body.decode('utf-8', errors='replace')[:200]}"
                    )
            retryable = exc.code >= 500 or exc.code == 429
            raise HubTransportError(
                detail,
                status_code=exc.code,
                retryable=retryable,
                is_timeout=(exc.code == 504),
                payload=err_payload,
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            is_timeout = isinstance(reason, TimeoutError)
            raise HubTransportError(
                f"Hub connection failed: {reason}",
                status_code=None,
                retryable=True,
                is_timeout=is_timeout,
            ) from exc
        except TimeoutError as exc:
            raise HubTransportError(
                f"Hub request timed out after {self.timeout_seconds}s",
                status_code=None,
                retryable=True,
                is_timeout=True,
            ) from exc
        except OSError as exc:
            raise HubTransportError(
                f"Hub transport error: {exc}",
                status_code=None,
                retryable=True,
                is_timeout=False,
            ) from exc

    def check_health(self) -> dict[str, Any]:
        return self._request("GET", "/hub/v1/health")

    def put_shadow(
        self,
        scene_id: str,
        window_id: str,
        *,
        desired_version: int,
        hardware_mode: str,
        rows: int,
        cols: int,
        base_cells: list[list[float]],
        marker_overlay: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        command_id: str | None = None,
        if_match: str | None = None,
    ) -> dict[str, Any]:
        path = (
            f"/hub/v1/windows/{urllib.parse.quote(scene_id, safe='')}/"
            f"{urllib.parse.quote(window_id, safe='')}/shadow"
        )
        body: dict[str, Any] = {
            "desired_version": desired_version,
            "hardware_mode": hardware_mode,
            "rows": rows,
            "cols": cols,
            "base_cells": base_cells,
            "marker_overlay": marker_overlay,
            "metadata": metadata or {},
        }
        if command_id is not None:
            body["command_id"] = command_id
        extra = {"If-Match": if_match} if if_match else None
        return self._request(
            "PUT",
            path,
            body,
            idempotency_key=command_id,
            extra_headers=extra,
        )

    def post_commands(
        self,
        scene_id: str,
        window_id: str,
        *,
        desired_version: int,
        commands: list[dict[str, Any]],
        timestamp: str | None = None,
        note: str = "",
        hardware_mode: str | None = None,
        rows: int | None = None,
        cols: int | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        path = (
            f"/hub/v1/windows/{urllib.parse.quote(scene_id, safe='')}/"
            f"{urllib.parse.quote(window_id, safe='')}/commands"
        )
        body: dict[str, Any] = {
            "desired_version": desired_version,
            "commands": commands,
            "timestamp": timestamp or utc_now_iso(),
            "note": note,
        }
        if hardware_mode is not None:
            body["hardware_mode"] = hardware_mode
        if rows is not None:
            body["rows"] = rows
        if cols is not None:
            body["cols"] = cols
        if command_id is not None:
            body["command_id"] = command_id
        return self._request("POST", path, body, idempotency_key=command_id)

    def post_marker_overlay(
        self,
        scene_id: str,
        window_id: str,
        *,
        desired_version: int,
        enabled: bool,
        code_id: int | None = None,
        start_row: int | None = None,
        start_col: int | None = None,
        rows: int | None = None,
        cols: int | None = None,
        hardware_mode: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        path = (
            f"/hub/v1/windows/{urllib.parse.quote(scene_id, safe='')}/"
            f"{urllib.parse.quote(window_id, safe='')}/marker-overlay"
        )
        body: dict[str, Any] = {
            "desired_version": desired_version,
            "enabled": enabled,
            "code_id": code_id,
            "start_row": start_row,
            "start_col": start_col,
        }
        if rows is not None:
            body["rows"] = rows
        if cols is not None:
            body["cols"] = cols
        if hardware_mode is not None:
            body["hardware_mode"] = hardware_mode
        if command_id is not None:
            body["command_id"] = command_id
        return self._request("POST", path, body, idempotency_key=command_id)

    def post_thinq_sync(
        self,
        *,
        hub_device_id: str,
        scene_id: str,
        window_id: str,
        timestamp: str,
        hardware_mode: str,
        cell_opacity_matrix: list[list[float]],
        cell_transmittance_matrix: list[list[float]],
        desired_version: int,
        command_id: str,
        active_shaded_cells: int = 0,
        dimmed_cells: int = 0,
        energy_daylight_score: float = 0.0,
        decision_summary_ko: str = "",
        protocol_version: str = "LG_THINQ_ON_SGF_V1",
    ) -> dict[str, Any]:
        message_id = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
        extra_headers = {
            "X-Client-Id": f"sunsync-backend-{scene_id}",
            "X-Message-Id": message_id,
            "X-Protocol-Version": protocol_version,
        }
        body: dict[str, Any] = {
            "hub_device_id": hub_device_id,
            "protocol_version": protocol_version,
            "scene_id": scene_id,
            "window_id": window_id,
            "timestamp": timestamp,
            "hardware_mode": hardware_mode,
            "cell_opacity_matrix": cell_opacity_matrix,
            "cell_transmittance_matrix": cell_transmittance_matrix,
            "active_shaded_cells": active_shaded_cells,
            "dimmed_cells": dimmed_cells,
            "energy_daylight_score": energy_daylight_score,
            "decision_summary_ko": decision_summary_ko,
            "desired_version": desired_version,
            "command_id": command_id,
        }
        return self._request(
            "POST",
            "/hub/v1/thinq/sync",
            body,
            idempotency_key=command_id,
            extra_headers=extra_headers,
        )

    def get_shadow(self, scene_id: str, window_id: str) -> dict[str, Any]:
        path = (
            f"/hub/v1/windows/{urllib.parse.quote(scene_id, safe='')}/"
            f"{urllib.parse.quote(window_id, safe='')}/shadow"
        )
        return self._request("GET", path)

    def reconcile_digests(self, windows: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/hub/v1/reconcile/digests", {"windows": windows})


def _update_log_delivery(
    session: Session,
    command_id: str,
    delivery_status: DeliveryStatus,
    hub_ack_at: str | None = None,
    log_records: Sequence[FilmApplicationLogRecord] | None = None,
) -> None:
    targets: Sequence[FilmApplicationLogRecord]
    if log_records is not None:
        targets = log_records
    else:
        targets = session.exec(
            select(FilmApplicationLogRecord).where(
                FilmApplicationLogRecord.command_id == command_id
            )
        ).all()
    for log_entry in targets:
        log_entry.delivery_status = delivery_status
        if hub_ack_at is not None:
            log_entry.hub_ack_at = hub_ack_at
        session.add(log_entry)


def enqueue_hub_command(
    session: Session,
    *,
    scene_id: str,
    window_id: str,
    operation: str,
    desired_version: int,
    payload: dict[str, Any],
    command_id: str | None = None,
    now_iso: str | None = None,
) -> HubCommandRecord:
    """Persist an outbox command and supersede older unfinished commands for the same window."""

    ts = now_iso or utc_now_iso()
    resolved_id = command_id or new_command_id()

    older_pending = session.exec(
        select(HubCommandRecord)
        .where(HubCommandRecord.scene_id == scene_id)
        .where(HubCommandRecord.window_id == window_id)
        .where(col(HubCommandRecord.status).in_(RETRYABLE_OUTBOX_STATUSES))
        .where(HubCommandRecord.desired_version < desired_version)
    ).all()
    for old_cmd in older_pending:
        old_cmd.status = "superseded"
        old_cmd.last_error = f"Superseded by desired_version={desired_version}"
        old_cmd.next_retry_at = None
        old_cmd.updated_at = ts
        session.add(old_cmd)
        _update_log_delivery(session, old_cmd.command_id, "superseded")

    cmd_record = HubCommandRecord(
        command_id=resolved_id,
        scene_id=scene_id,
        window_id=window_id,
        operation=operation,
        desired_version=desired_version,
        payload_json=json.dumps(payload, separators=(",", ":"), sort_keys=True),
        status="pending",
        attempts=0,
        next_retry_at=None,
        last_error=None,
        created_at=ts,
        updated_at=ts,
    )
    session.add(cmd_record)
    return cmd_record


def dispatch_command(
    session: Session,
    command_record: HubCommandRecord,
    *,
    film_record: FilmStateRecord | None = None,
    log_records: Sequence[FilmApplicationLogRecord] | None = None,
    client: HubClient | None = None,
) -> DispatchResult:
    """Dispatch a single outbox command to the remote hub and reconcile local shadow metadata."""

    hub = client or HubClient()
    now_iso = utc_now_iso()
    record = film_record or session.get(
        FilmStateRecord, f"{command_record.scene_id}:{command_record.window_id}"
    )

    command_record.attempts += 1
    command_record.status = "dispatched"
    command_record.updated_at = now_iso
    session.add(command_record)

    payload: dict[str, Any] = json.loads(command_record.payload_json)
    try:
        op = command_record.operation
        if op == "shadow_put":
            response = hub.put_shadow(
                command_record.scene_id,
                command_record.window_id,
                desired_version=int(payload["desired_version"]),
                hardware_mode=str(payload["hardware_mode"]),
                rows=int(payload["rows"]),
                cols=int(payload["cols"]),
                base_cells=payload["base_cells"],
                marker_overlay=payload["marker_overlay"],
                metadata=payload.get("metadata"),
                command_id=command_record.command_id,
                if_match=payload.get("if_match"),
            )
        elif op == "cell_commands":
            response = hub.post_commands(
                command_record.scene_id,
                command_record.window_id,
                desired_version=int(payload["desired_version"]),
                commands=payload["commands"],
                timestamp=payload.get("timestamp"),
                note=str(payload.get("note", "")),
                hardware_mode=payload.get("hardware_mode"),
                rows=payload.get("rows"),
                cols=payload.get("cols"),
                command_id=command_record.command_id,
            )
        elif op == "marker_overlay":
            response = hub.post_marker_overlay(
                command_record.scene_id,
                command_record.window_id,
                desired_version=int(payload["desired_version"]),
                enabled=bool(payload["enabled"]),
                code_id=payload.get("code_id"),
                start_row=payload.get("start_row"),
                start_col=payload.get("start_col"),
                rows=payload.get("rows"),
                cols=payload.get("cols"),
                hardware_mode=payload.get("hardware_mode"),
                command_id=command_record.command_id,
            )
        elif op == "thinq_sync":
            response = hub.post_thinq_sync(
                hub_device_id=str(payload["hub_device_id"]),
                scene_id=command_record.scene_id,
                window_id=command_record.window_id,
                timestamp=str(payload["timestamp"]),
                hardware_mode=str(payload["hardware_mode"]),
                cell_opacity_matrix=payload["cell_opacity_matrix"],
                cell_transmittance_matrix=payload["cell_transmittance_matrix"],
                desired_version=int(payload["desired_version"]),
                command_id=command_record.command_id,
                active_shaded_cells=int(payload.get("active_shaded_cells", 0)),
                dimmed_cells=int(payload.get("dimmed_cells", 0)),
                energy_daylight_score=float(payload.get("energy_daylight_score", 0.0)),
                decision_summary_ko=str(payload.get("decision_summary_ko", "")),
                protocol_version=str(payload.get("protocol_version", "LG_THINQ_ON_SGF_V1")),
            )
        else:
            raise HubTransportError(f"Unsupported hub operation: {op}", retryable=False)
    except HubTransportError as exc:
        err_now = utc_now_iso()
        if exc.retryable:
            backoff_s = min(2**command_record.attempts, 30)
            retry_at = (datetime.now(UTC) + timedelta(seconds=backoff_s)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            cmd_status: DeliveryStatus = "timed_out" if exc.is_timeout else "pending"
            sync_status: SyncStatus = "pending"
            command_record.next_retry_at = retry_at
        elif exc.status_code in (400, 409, 412, 422):
            cmd_status = "rejected"
            sync_status = "rejected"
            command_record.next_retry_at = None
        else:
            cmd_status = "failed"
            sync_status = "failed"
            command_record.next_retry_at = None

        command_record.status = cmd_status
        command_record.last_error = exc.detail
        command_record.updated_at = err_now
        session.add(command_record)

        reported_ver = record.reported_version if record else 0
        if record is not None:
            record.sync_status = sync_status
            record.sync_error = exc.detail
            record.last_command_id = command_record.command_id
            session.add(record)

        _update_log_delivery(
            session,
            command_record.command_id,
            cmd_status,
            hub_ack_at=None,
            log_records=log_records,
        )
        return DispatchResult(
            command_id=command_record.command_id,
            command_status=cmd_status,
            sync_status=sync_status,
            reported_version=reported_ver,
            hub_ack_at=None,
            error=exc.detail,
            response=exc.payload,
        )

    ack_now = utc_now_iso()
    raw_status = str(response.get("status") or response.get("sync_status") or "applied").lower()
    ack_at = str(response.get("applied_at") or response.get("hub_ack_at") or ack_now)
    raw_reported_ver = response.get("reported_version")
    effective_cells = response.get("effective_cells")
    rejection_reason = response.get("rejection_reason") or response.get("detail")

    if raw_status in ("applied", "acked", "synced", "in_sync"):
        final_cmd_status: DeliveryStatus = "acked" if raw_status == "acked" else "applied"
        reported_ver = (
            int(raw_reported_ver)
            if raw_reported_ver is not None
            else command_record.desired_version
        )
        if record is not None:
            record.reported_version = max(record.reported_version, reported_ver)
            if effective_cells is not None:
                record.reported_cells_json = json.dumps(effective_cells)
            else:
                record.reported_cells_json = record.cells_json
            record.sync_status = (
                "synced" if record.reported_version >= record.desired_version else "pending"
            )
            record.last_command_id = command_record.command_id
            record.last_synced_at = ack_at
            record.sync_error = None
            if response.get("hub_device_id"):
                record.hub_device_id = str(response["hub_device_id"])
            session.add(record)
            final_sync_status = normalize_sync_status(record.sync_status)
            reported_ver = record.reported_version
        else:
            final_sync_status = "synced"

        command_record.status = final_cmd_status
        command_record.next_retry_at = None
        command_record.last_error = None
        command_record.updated_at = ack_now
        session.add(command_record)
        _update_log_delivery(
            session,
            command_record.command_id,
            final_cmd_status,
            hub_ack_at=ack_at,
            log_records=log_records,
        )
        return DispatchResult(
            command_id=command_record.command_id,
            command_status=final_cmd_status,
            sync_status=final_sync_status,
            reported_version=reported_ver,
            hub_ack_at=ack_at,
            error=None,
            response=response,
        )

    if raw_status == "superseded":
        reason_str = str(rejection_reason or "Command superseded by newer hub state")
        command_record.status = "superseded"
        command_record.next_retry_at = None
        command_record.last_error = reason_str
        command_record.updated_at = ack_now
        session.add(command_record)
        reported_ver = record.reported_version if record else 0
        if raw_reported_ver is not None:
            reported_ver = max(reported_ver, int(raw_reported_ver))
        if record is not None:
            record.reported_version = reported_ver
            if effective_cells is not None:
                record.reported_cells_json = json.dumps(effective_cells)
            record.sync_status = (
                "synced" if record.reported_version >= record.desired_version else "drifted"
            )
            record.sync_error = reason_str if record.sync_status != "synced" else None
            session.add(record)
            final_sync_status = normalize_sync_status(record.sync_status)
        else:
            final_sync_status = "drifted"
        _update_log_delivery(
            session,
            command_record.command_id,
            "superseded",
            hub_ack_at=None,
            log_records=log_records,
        )
        return DispatchResult(
            command_id=command_record.command_id,
            command_status="superseded",
            sync_status=final_sync_status,
            reported_version=reported_ver,
            hub_ack_at=None,
            error=reason_str,
            response=response,
        )

    # Rejected or failed response payload
    is_rejected = raw_status == "rejected"
    final_cmd_status = "rejected" if is_rejected else "failed"
    final_sync_status = "rejected" if is_rejected else "failed"
    reason_str = str(rejection_reason or f"Hub returned status '{raw_status}'")
    command_record.status = final_cmd_status
    command_record.next_retry_at = None
    command_record.last_error = reason_str
    command_record.updated_at = ack_now
    session.add(command_record)
    reported_ver = record.reported_version if record else 0
    if record is not None:
        record.sync_status = final_sync_status
        record.sync_error = reason_str
        record.last_command_id = command_record.command_id
        session.add(record)
    _update_log_delivery(
        session,
        command_record.command_id,
        final_cmd_status,
        hub_ack_at=None,
        log_records=log_records,
    )
    return DispatchResult(
        command_id=command_record.command_id,
        command_status=final_cmd_status,
        sync_status=final_sync_status,
        reported_version=reported_ver,
        hub_ack_at=None,
        error=reason_str,
        response=response,
    )


def _extract_window_ids(items: Any, scene_id: str) -> set[str]:
    result: set[str] = set()
    if not isinstance(items, list):
        return result
    for entry in items:
        if isinstance(entry, dict):
            w_id = entry.get("window_id")
            s_id = entry.get("scene_id")
            if w_id is not None and (s_id is None or s_id == scene_id):
                result.add(str(w_id))
        elif isinstance(entry, str):
            if ":" in entry:
                prefix, w_id = entry.split(":", 1)
                if prefix == scene_id:
                    result.add(w_id)
            else:
                result.add(entry)
    return result


def get_scene_hub_status(
    session: Session,
    scene_id: str,
    *,
    client: HubClient | None = None,
) -> HubStatusResponse:
    """Return live hub reachability, pending outbox depth, and per-window shadow sync status."""

    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    scene = load_stored_scene(scene_record.scene_json)
    records = session.exec(
        select(FilmStateRecord).where(FilmStateRecord.scene_id == scene_id)
    ).all()
    records_by_win = {rec.window_id: rec for rec in records}

    window_ids = [w.window_id for w in scene.windows]
    for rec in records:
        if rec.window_id not in window_ids:
            window_ids.append(rec.window_id)

    pending_rows = session.exec(
        select(HubCommandRecord)
        .where(HubCommandRecord.scene_id == scene_id)
        .where(col(HubCommandRecord.status).in_(RETRYABLE_OUTBOX_STATUSES))
    ).all()
    pending_by_win: dict[str, int] = {}
    for cmd in pending_rows:
        pending_by_win[cmd.window_id] = pending_by_win.get(cmd.window_id, 0) + 1

    window_entries: list[HubWindowStatusEntry] = []
    for w_id in window_ids:
        film_rec = records_by_win.get(w_id)
        if film_rec is not None:
            _, effective_cells, overlay = extract_base_cells_and_overlay(film_rec)
            digest = compute_state_digest(effective_cells, overlay)
            window_entries.append(
                HubWindowStatusEntry(
                    window_id=w_id,
                    desired_version=film_rec.desired_version,
                    reported_version=film_rec.reported_version,
                    sync_status=normalize_sync_status(film_rec.sync_status),
                    last_command_id=film_rec.last_command_id,
                    last_synced_at=film_rec.last_synced_at,
                    hub_device_id=film_rec.hub_device_id,
                    sync_error=film_rec.sync_error,
                    state_digest=digest,
                    pending_commands=pending_by_win.get(w_id, 0),
                )
            )
        else:
            win = next((w for w in scene.windows if w.window_id == w_id), None)
            rows = win.film_rows if win else 10
            cols = win.film_cols if win else 10
            empty_cells = [[0.0 for _ in range(cols)] for _ in range(rows)]
            window_entries.append(
                HubWindowStatusEntry(
                    window_id=w_id,
                    desired_version=0,
                    reported_version=0,
                    sync_status="local_only",
                    state_digest=compute_state_digest(empty_cells, None),
                    pending_commands=pending_by_win.get(w_id, 0),
                )
            )

    checked_at = utc_now_iso()
    url = hub_base_url()
    if not url:
        return HubStatusResponse(
            scene_id=scene_id,
            sync_mode="local",
            hub_url=None,
            reachable=False,
            hub_status="local_only",
            pending_outbox_count=len(pending_rows),
            checked_at=checked_at,
            windows=window_entries,
        )

    hub = client or HubClient(base_url=url)
    try:
        health = hub.check_health()
    except HubTransportError as exc:
        return HubStatusResponse(
            scene_id=scene_id,
            sync_mode="remote_hub",
            hub_url=url,
            reachable=False,
            hub_status="offline",
            pending_outbox_count=len(pending_rows),
            error=exc.detail,
            checked_at=checked_at,
            windows=window_entries,
        )

    fault_mode = health.get("simulated_fault_mode")
    has_degraded_window = any(
        w.sync_status in ("pending", "drifted", "failed", "rejected") for w in window_entries
    )
    hub_status_val: Any = (
        "degraded"
        if (
            len(pending_rows) > 0
            or has_degraded_window
            or (fault_mode is not None and str(fault_mode) not in ("", "none"))
        )
        else "online"
    )
    return HubStatusResponse(
        scene_id=scene_id,
        sync_mode="remote_hub",
        hub_url=url,
        reachable=True,
        hub_status=hub_status_val,
        hub_id=str(health["hub_id"]) if health.get("hub_id") is not None else None,
        hub_boot_id=(str(health["hub_boot_id"]) if health.get("hub_boot_id") is not None else None),
        simulated_fault_mode=str(fault_mode) if fault_mode is not None else None,
        pending_outbox_count=len(pending_rows),
        checked_at=checked_at,
        windows=window_entries,
    )


def reconcile_scene_with_hub(
    session: Session,
    scene_id: str,
    *,
    client: HubClient | None = None,
) -> HubReconcileResponse:
    """Coalesce superseded outbox entries, flush pending commands, and heal shadow drift."""

    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    reconciled_at = utc_now_iso()
    records = session.exec(
        select(FilmStateRecord).where(FilmStateRecord.scene_id == scene_id)
    ).all()
    records_by_win = {rec.window_id: rec for rec in records}

    url = hub_base_url()
    if not url:
        local_results = [
            HubReconcileWindowResult(
                window_id=rec.window_id,
                action="local_only",
                desired_version=rec.desired_version,
                reported_version=rec.reported_version,
                sync_status=normalize_sync_status(rec.sync_status),
                command_id=rec.last_command_id,
            )
            for rec in records
        ]
        return HubReconcileResponse(
            scene_id=scene_id,
            sync_mode="local",
            reconciled_at=reconciled_at,
            windows=local_results,
        )

    hub = client or HubClient(base_url=url)
    superseded_count = 0
    dispatched_count = 0
    flushed_windows: set[str] = set()

    # 1. Coalesce superseded unfinished outbox commands per (scene_id, window_id)
    pending_rows = session.exec(
        select(HubCommandRecord)
        .where(HubCommandRecord.scene_id == scene_id)
        .where(col(HubCommandRecord.status).in_(RETRYABLE_OUTBOX_STATUSES))
        .order_by(
            col(HubCommandRecord.window_id).asc(),
            col(HubCommandRecord.desired_version).asc(),
            col(HubCommandRecord.created_at).asc(),
        )
    ).all()

    by_window: dict[str, list[HubCommandRecord]] = {}
    for row in pending_rows:
        by_window.setdefault(row.window_id, []).append(row)

    latest_to_dispatch: list[HubCommandRecord] = []
    for w_id, cmds in by_window.items():
        if len(cmds) > 1:
            for stale in cmds[:-1]:
                stale.status = "superseded"
                stale.last_error = f"Superseded by command {cmds[-1].command_id}"
                stale.next_retry_at = None
                stale.updated_at = reconciled_at
                session.add(stale)
                _update_log_delivery(session, stale.command_id, "superseded")
                superseded_count += 1
        latest_to_dispatch.append(cmds[-1])

    # 2. Retry the latest pending command per window
    for cmd in latest_to_dispatch:
        dispatched_count += 1
        res = dispatch_command(
            session,
            cmd,
            film_record=records_by_win.get(cmd.window_id),
            client=hub,
        )
        if res.sync_status == "synced":
            flushed_windows.add(cmd.window_id)

    # 3. Bulk digest verification against the hub
    digest_request_items: list[dict[str, Any]] = []
    for rec in records:
        _, effective_cells, overlay = extract_base_cells_and_overlay(rec)
        digest_request_items.append(
            {
                "scene_id": scene_id,
                "window_id": rec.window_id,
                "desired_version": rec.desired_version,
                "state_digest": compute_state_digest(effective_cells, overlay),
            }
        )

    hub_boot_id: str | None = None
    healed_count = 0
    failed_count = 0
    in_sync_count = 0
    window_results: list[HubReconcileWindowResult] = []

    if digest_request_items:
        try:
            digest_resp = hub.reconcile_digests(digest_request_items)
        except HubTransportError as exc:
            for rec in records:
                rec.sync_status = "pending" if exc.retryable else "failed"
                rec.sync_error = exc.detail
                session.add(rec)
                failed_count += 1
                window_results.append(
                    HubReconcileWindowResult(
                        window_id=rec.window_id,
                        action="failed",
                        desired_version=rec.desired_version,
                        reported_version=rec.reported_version,
                        sync_status=normalize_sync_status(rec.sync_status),
                        command_id=rec.last_command_id,
                        error=exc.detail,
                    )
                )
            session.commit()
            return HubReconcileResponse(
                scene_id=scene_id,
                sync_mode="remote_hub",
                hub_boot_id=None,
                superseded_commands=superseded_count,
                dispatched_commands=dispatched_count,
                healed_windows=0,
                failed_windows=failed_count,
                in_sync_windows=0,
                reconciled_at=reconciled_at,
                windows=window_results,
            )

        if digest_resp.get("hub_boot_id") is not None:
            hub_boot_id = str(digest_resp["hub_boot_id"])
        in_sync_ids = _extract_window_ids(digest_resp.get("in_sync"), scene_id)
        drifted_ids = _extract_window_ids(digest_resp.get("drifted"), scene_id)
        missing_ids = _extract_window_ids(digest_resp.get("missing"), scene_id)

        # 4. Push full shadow for drifted or missing windows
        for rec in records:
            w_id = rec.window_id
            if w_id in drifted_ids or w_id in missing_ids:
                base_cells, _, overlay = extract_base_cells_and_overlay(rec)
                if rec.desired_version <= 0:
                    rec.desired_version = 1
                hw_mode = (
                    "LG_SGF" if rec.film_type == "SPD" and rec.hub_device_id else rec.film_type
                )
                shadow_payload: dict[str, Any] = {
                    "desired_version": rec.desired_version,
                    "hardware_mode": hw_mode,
                    "rows": rec.film_rows,
                    "cols": rec.film_cols,
                    "base_cells": base_cells,
                    "marker_overlay": overlay,
                    "metadata": {
                        "source": "reconcile",
                        "plan_id": rec.active_plan_id,
                        "reason": "drifted" if w_id in drifted_ids else "missing",
                    },
                }
                heal_cmd = enqueue_hub_command(
                    session,
                    scene_id=scene_id,
                    window_id=w_id,
                    operation="shadow_put",
                    desired_version=rec.desired_version,
                    payload=shadow_payload,
                    now_iso=reconciled_at,
                )
                dispatched_count += 1
                heal_res = dispatch_command(
                    session,
                    heal_cmd,
                    film_record=rec,
                    client=hub,
                )
                if heal_res.sync_status == "synced":
                    healed_count += 1
                    window_results.append(
                        HubReconcileWindowResult(
                            window_id=w_id,
                            action="healed",
                            desired_version=rec.desired_version,
                            reported_version=rec.reported_version,
                            sync_status="synced",
                            command_id=heal_cmd.command_id,
                        )
                    )
                else:
                    failed_count += 1
                    window_results.append(
                        HubReconcileWindowResult(
                            window_id=w_id,
                            action="failed",
                            desired_version=rec.desired_version,
                            reported_version=rec.reported_version,
                            sync_status=heal_res.sync_status,
                            command_id=heal_cmd.command_id,
                            error=heal_res.error,
                        )
                    )
            elif w_id in in_sync_ids:
                rec.sync_status = "synced"
                rec.reported_version = max(rec.reported_version, rec.desired_version)
                rec.reported_cells_json = rec.cells_json
                rec.last_synced_at = reconciled_at
                rec.sync_error = None
                session.add(rec)
                in_sync_count += 1
                window_results.append(
                    HubReconcileWindowResult(
                        window_id=w_id,
                        action="flushed" if w_id in flushed_windows else "in_sync",
                        desired_version=rec.desired_version,
                        reported_version=rec.reported_version,
                        sync_status="synced",
                        command_id=rec.last_command_id,
                    )
                )
            else:
                # Window not categorized by hub response: treat as failed/drifted
                failed_count += 1
                rec.sync_status = "drifted"
                rec.sync_error = "Window omitted from hub reconcile digest response"
                session.add(rec)
                window_results.append(
                    HubReconcileWindowResult(
                        window_id=w_id,
                        action="failed",
                        desired_version=rec.desired_version,
                        reported_version=rec.reported_version,
                        sync_status="drifted",
                        command_id=rec.last_command_id,
                        error=rec.sync_error,
                    )
                )

    session.commit()
    return HubReconcileResponse(
        scene_id=scene_id,
        sync_mode="remote_hub",
        hub_boot_id=hub_boot_id,
        superseded_commands=superseded_count,
        dispatched_commands=dispatched_count,
        healed_windows=healed_count,
        failed_windows=failed_count,
        in_sync_windows=in_sync_count,
        reconciled_at=reconciled_at,
        windows=window_results,
    )
