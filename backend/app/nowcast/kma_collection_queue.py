"""File-backed durable queue for resumable KMA GK2A backfills."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.nowcast.kma_adapter import (
    KMA_INTERVAL_MINUTES,
    KMA_MAX_POINTS_PER_REQUEST,
    KmaCollection,
    KmaGk2aAdapter,
    KmaGk2aError,
    KmaSeries,
    build_kma_snapshot,
    parse_kma_wide_payload,
)
from app.nowcast.kma_dataset import KmaArtifactPaths, write_kma_collection
from engine.fr01.models import Site

QUEUE_VERSION = 1
VARIABLES = ("DSR", "CA")


class KmaQueueError(ValueError):
    """A sanitized durable-queue state or lifecycle error."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _utc_z(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise KmaQueueError("kma_queue_invalid_timestamp") from error
    return _utc(parsed, "queue timestamp")


def _stamp(value: datetime) -> str:
    return _utc(value, "timestamp").strftime("%Y%m%dT%H%M%SZ")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    encoded = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_write_bytes(path, encoded.encode("utf-8"))


def _load_queue(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KmaQueueError("kma_queue_unreadable") from error
    if document.get("queue_version") != QUEUE_VERSION or not isinstance(
        document.get("tasks"), list
    ):
        raise KmaQueueError("kma_queue_invalid_schema")
    return document


def _chunk_ranges(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    if start >= end:
        raise ValueError("start_time must be before end_time")
    if any(
        value.second or value.microsecond or value.minute % KMA_INTERVAL_MINUTES
        for value in (start, end)
    ):
        raise ValueError("range must align to 10-minute UTC boundaries")
    ranges: list[tuple[datetime, datetime]] = []
    maximum_span = timedelta(
        minutes=KMA_INTERVAL_MINUTES * (KMA_MAX_POINTS_PER_REQUEST - 1)
    )
    current = start
    while current <= end:
        chunk_end = min(end, current + maximum_span)
        ranges.append((current, chunk_end))
        current = chunk_end + timedelta(minutes=KMA_INTERVAL_MINUTES)
    return ranges


def queue_path_for(output_root: Path, start: datetime, end: datetime) -> Path:
    """Return the deterministic journal path for one requested range."""

    stem = f"{_stamp(start)}_{_stamp(end)}"
    return output_root / "manifests" / f"{stem}_queue.json"


def prepare_queue(
    *,
    site: Site,
    start_time: datetime,
    end_time: datetime,
    output_root: Path,
    resume: bool = False,
    created_at: datetime | None = None,
) -> Path:
    """Create a queue journal, or validate and recover an existing journal for resume."""

    start = _utc(start_time, "start_time")
    end = _utc(end_time, "end_time")
    ranges = _chunk_ranges(start, end)
    path = queue_path_for(output_root, start, end)
    if path.exists():
        if not resume:
            raise KmaQueueError("kma_queue_already_exists_use_resume")
        document = _load_queue(path)
        expected = (
            _utc_z(start),
            _utc_z(end),
            site.latitude_deg,
            site.longitude_deg,
        )
        actual = (
            document.get("requested_start_utc"),
            document.get("requested_end_utc"),
            document.get("request_latitude"),
            document.get("request_longitude"),
        )
        if actual != expected:
            raise KmaQueueError("kma_queue_request_mismatch")
        recovered = False
        for task in document["tasks"]:
            if task.get("status") == "running":
                task["status"] = "pending"
                task["last_error"] = "interrupted_before_completion"
                recovered = True
        if recovered:
            document["updated_at_utc"] = _utc_z(datetime.now(UTC))
            _atomic_write_json(path, document)
        return path

    stem = f"{_stamp(start)}_{_stamp(end)}"
    tasks: list[dict[str, Any]] = []
    for index, (chunk_start, chunk_end) in enumerate(ranges, start=1):
        for variable in VARIABLES:
            relative_raw = (
                Path("raw")
                / "kma_gk2a"
                / f"{stem}_{variable}_part{index:03d}.txt"
            )
            tasks.append(
                {
                    "task_id": f"{variable}_{index:03d}",
                    "variable": variable,
                    "chunk_index": index,
                    "start_utc": _utc_z(chunk_start),
                    "end_utc": _utc_z(chunk_end),
                    "status": "pending",
                    "attempts": 0,
                    "raw_path": relative_raw.as_posix(),
                    "payload_sha256": None,
                    "last_error": None,
                    "completed_at_utc": None,
                }
            )
    now = _utc(created_at or datetime.now(UTC), "created_at")
    document = {
        "queue_version": QUEUE_VERSION,
        "provider": "kma_gk2a",
        "requested_start_utc": _utc_z(start),
        "requested_end_utc": _utc_z(end),
        "request_latitude": site.latitude_deg,
        "request_longitude": site.longitude_deg,
        "interval_minutes": KMA_INTERVAL_MINUTES,
        "max_points_per_request": KMA_MAX_POINTS_PER_REQUEST,
        "created_at_utc": _utc_z(now),
        "updated_at_utc": _utc_z(now),
        "tasks": tasks,
    }
    _atomic_write_json(path, document)
    return path


def queue_summary(queue_path: Path) -> dict[str, int]:
    """Return counts suitable for progress output without exposing credentials."""

    document = _load_queue(queue_path)
    counts = {status: 0 for status in ("pending", "running", "succeeded", "failed")}
    for task in document["tasks"]:
        status = task.get("status")
        if status not in counts:
            raise KmaQueueError("kma_queue_invalid_task_status")
        counts[status] += 1
    counts["total"] = len(document["tasks"])
    return counts


def run_queue(
    *,
    queue_path: Path,
    output_root: Path,
    adapter: KmaGk2aAdapter,
    max_concurrency: int = 3,
    retry_failed: bool = False,
) -> dict[str, int]:
    """Execute unfinished tasks, checkpointing every completion for safe resume."""

    if max_concurrency < 1:
        raise ValueError("max_concurrency must be positive")
    document = _load_queue(queue_path)
    if retry_failed:
        for task in document["tasks"]:
            if task["status"] == "failed":
                task["status"] = "pending"
                task["last_error"] = None

    selected = [task for task in document["tasks"] if task["status"] == "pending"]
    if not selected:
        return queue_summary(queue_path)

    task_by_id = {task["task_id"]: task for task in document["tasks"]}
    for task in selected:
        task["status"] = "running"
        task["attempts"] += 1
    document["updated_at_utc"] = _utc_z(datetime.now(UTC))
    _atomic_write_json(queue_path, document)

    def fetch(task: dict[str, Any]) -> tuple[str, bytes]:
        payload, _series = adapter.fetch_chunk(
            _parse_utc(task["start_utc"]),
            _parse_utc(task["end_utc"]),
            task["variable"],
        )
        return task["task_id"], payload

    workers = min(max_concurrency, len(selected))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch, task): task["task_id"] for task in selected}
        for future in as_completed(futures):
            task = task_by_id[futures[future]]
            try:
                _task_id, payload = future.result()
                raw_path = output_root / task["raw_path"]
                _atomic_write_bytes(raw_path, payload)
                task["payload_sha256"] = hashlib.sha256(payload).hexdigest()
                task["status"] = "succeeded"
                task["last_error"] = None
                task["completed_at_utc"] = _utc_z(datetime.now(UTC))
            except KmaGk2aError as error:
                task["status"] = "failed"
                task["last_error"] = error.reason
            document["updated_at_utc"] = _utc_z(datetime.now(UTC))
            _atomic_write_json(queue_path, document)
    return queue_summary(queue_path)


def finalize_queue(
    *, queue_path: Path, output_root: Path, site: Site
) -> KmaArtifactPaths:
    """Build normalized artifacts only after every durable task has succeeded."""

    document = _load_queue(queue_path)
    if any(task["status"] != "succeeded" for task in document["tasks"]):
        raise KmaQueueError("kma_queue_incomplete")
    if (
        document.get("request_latitude") != site.latitude_deg
        or document.get("request_longitude") != site.longitude_deg
    ):
        raise KmaQueueError("kma_queue_request_mismatch")

    payloads: dict[str, list[bytes]] = {variable: [] for variable in VARIABLES}
    series_by_variable: dict[str, list[KmaSeries]] = {variable: [] for variable in VARIABLES}
    for variable in VARIABLES:
        tasks = sorted(
            (task for task in document["tasks"] if task["variable"] == variable),
            key=lambda task: task["chunk_index"],
        )
        for task in tasks:
            raw_path = output_root / task["raw_path"]
            try:
                payload = raw_path.read_bytes()
            except OSError as error:
                raise KmaQueueError("kma_queue_raw_missing") from error
            if hashlib.sha256(payload).hexdigest() != task["payload_sha256"]:
                raise KmaQueueError("kma_queue_raw_hash_mismatch")
            parsed = parse_kma_wide_payload(payload, variable)
            if (
                parsed.values[0][0] != _parse_utc(task["start_utc"])
                or parsed.values[-1][0] != _parse_utc(task["end_utc"])
            ):
                raise KmaQueueError("kma_queue_raw_range_mismatch")
            payloads[variable].append(payload)
            series_by_variable[variable].append(parsed)

    def combine(variable: str) -> KmaSeries:
        chunks = series_by_variable[variable]
        first = chunks[0]
        if any(
            (chunk.longitude_deg, chunk.latitude_deg)
            != (first.longitude_deg, first.latitude_deg)
            for chunk in chunks[1:]
        ):
            raise KmaQueueError("kma_queue_grid_mismatch")
        values = tuple(value for chunk in chunks for value in chunk.values)
        expected = timedelta(minutes=KMA_INTERVAL_MINUTES)
        if any(
            current[0] - previous[0] != expected
            for previous, current in pairwise(values)
        ):
            raise KmaQueueError("kma_queue_timestamp_gap")
        return KmaSeries(
            variable=variable,
            base_time=first.base_time,
            longitude_deg=first.longitude_deg,
            latitude_deg=first.latitude_deg,
            values=values,
        )

    start = _parse_utc(document["requested_start_utc"])
    end = _parse_utc(document["requested_end_utc"])
    fetched_at = datetime.now(UTC)
    dsr = combine("DSR")
    cloud = combine("CA")
    dsr_payloads = tuple(payloads["DSR"])
    cloud_payloads = tuple(payloads["CA"])
    snapshot = build_kma_snapshot(
        site,
        start,
        end,
        fetched_at,
        b"\n--KMA-CHUNK--\n".join(dsr_payloads),
        b"\n--KMA-CHUNK--\n".join(cloud_payloads),
        dsr,
        cloud,
    )
    collection = KmaCollection(
        site=site,
        start_time=start,
        end_time=end,
        fetched_at=fetched_at,
        dsr_payloads=dsr_payloads,
        cloud_payloads=cloud_payloads,
        dsr=dsr,
        cloud=cloud,
        snapshot=snapshot,
    )
    return write_kma_collection(collection, output_root)
