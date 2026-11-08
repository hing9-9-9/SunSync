"""Local raw/normalized artifact writer for KMA GK2A collections."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.nowcast.kma_adapter import KMA_DSR_UNIT_SOURCE_URL, KMA_GK2A_URL, KmaCollection

TRANSFORM_VERSION = "kma_gk2a_wide_to_long_v1"


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _file_stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


@dataclass(frozen=True)
class KmaArtifactPaths:
    dsr_raw: tuple[Path, ...]
    cloud_raw: tuple[Path, ...]
    normalized_csv: Path
    manifest: Path


def write_kma_collection(collection: KmaCollection, output_root: Path) -> KmaArtifactPaths:
    """Persist exact provider payloads and a human-readable normalized CSV atomically."""

    start_stamp = _file_stamp(collection.start_time)
    end_stamp = _file_stamp(collection.end_time)
    stem = f"{start_stamp}_{end_stamp}"
    raw_directory = output_root / "raw" / "kma_gk2a"
    normalized_directory = output_root / "normalized" / "kma_gk2a"
    manifest_directory = output_root / "manifests"
    multiple_dsr = len(collection.dsr_payloads) > 1
    multiple_cloud = len(collection.cloud_payloads) > 1

    def raw_paths(variable: str, count: int, multiple: bool) -> tuple[Path, ...]:
        return tuple(
            raw_directory
            / (f"{stem}_{variable}_part{index:03d}.txt" if multiple else f"{stem}_{variable}.txt")
            for index in range(1, count + 1)
        )

    paths = KmaArtifactPaths(
        dsr_raw=raw_paths("DSR", len(collection.dsr_payloads), multiple_dsr),
        cloud_raw=raw_paths("CA", len(collection.cloud_payloads), multiple_cloud),
        normalized_csv=normalized_directory / f"{stem}_kma_gk2a.csv",
        manifest=manifest_directory / f"{stem}_collection.json",
    )

    for path, payload in zip(paths.dsr_raw, collection.dsr_payloads, strict=True):
        _atomic_write_bytes(path, payload)
    for path, payload in zip(paths.cloud_raw, collection.cloud_payloads, strict=True):
        _atomic_write_bytes(path, payload)

    dsr_values = dict(collection.dsr.values)
    cloud_values = dict(collection.cloud.values)
    timestamps = sorted(set(dsr_values) | set(cloud_values))
    csv_lines: list[str] = []
    fieldnames = [
        "timestamp_utc",
        "requested_latitude",
        "requested_longitude",
        "returned_latitude",
        "returned_longitude",
        "dsr_raw",
        "ca_raw_provider",
        "ca_fraction",
        "cloud_cover_pct",
        "dsr_unit",
        "unit_confirmed",
        "quality_flag",
    ]
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for timestamp in timestamps:
        dsr = dsr_values.get(timestamp)
        cloud_raw = cloud_values.get(timestamp)
        cloud = cloud_raw if cloud_raw is not None and 0.0 <= cloud_raw <= 1.0 else None
        cloud_out_of_range = cloud_raw is not None and cloud is None
        if cloud_out_of_range:
            quality = "ca_out_of_range"
        elif dsr is None and cloud is None:
            quality = "missing"
        elif dsr is None or cloud is None:
            quality = "partial"
        else:
            quality = "ok"
        writer.writerow(
            {
                "timestamp_utc": _utc_z(timestamp),
                "requested_latitude": collection.site.latitude_deg,
                "requested_longitude": collection.site.longitude_deg,
                "returned_latitude": collection.dsr.latitude_deg,
                "returned_longitude": collection.dsr.longitude_deg,
                "dsr_raw": "" if dsr is None else dsr,
                "ca_raw_provider": "" if cloud_raw is None else cloud_raw,
                "ca_fraction": "" if cloud is None else cloud,
                "cloud_cover_pct": "" if cloud is None else cloud * 100.0,
                "dsr_unit": "W/m2",
                "unit_confirmed": "true",
                "quality_flag": quality,
            }
        )
    csv_lines.append(buffer.getvalue())
    _atomic_write_text(paths.normalized_csv, "".join(csv_lines))

    manifest = {
        "provider": "kma_gk2a",
        "product": "10min_dsr_cloud_point",
        "endpoint": KMA_GK2A_URL,
        "requested_start_utc": _utc_z(collection.start_time),
        "requested_end_utc": _utc_z(collection.end_time),
        "fetched_at_utc": _utc_z(collection.fetched_at),
        "request_latitude": collection.site.latitude_deg,
        "request_longitude": collection.site.longitude_deg,
        "returned_latitude": collection.dsr.latitude_deg,
        "returned_longitude": collection.dsr.longitude_deg,
        "interval_minutes": 10,
        "variables": ["DSR", "CA"],
        "row_count": len(timestamps),
        "valid_dsr_count": sum(value is not None for value in dsr_values.values()),
        "valid_cloud_count": sum(
            value is not None and 0.0 <= value <= 1.0 for value in cloud_values.values()
        ),
        "out_of_range_cloud_count": sum(
            value is not None and not 0.0 <= value <= 1.0 for value in cloud_values.values()
        ),
        "dsr_unit": "W/m2",
        "unit_confirmed": True,
        "dsr_unit_source": KMA_DSR_UNIT_SOURCE_URL,
        "transform_version": TRANSFORM_VERSION,
        "raw_payloads": {
            "DSR": [
                {
                    "path": path.relative_to(output_root).as_posix(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for path, payload in zip(paths.dsr_raw, collection.dsr_payloads, strict=True)
            ],
            "CA": [
                {
                    "path": path.relative_to(output_root).as_posix(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for path, payload in zip(paths.cloud_raw, collection.cloud_payloads, strict=True)
            ],
        },
        "normalized_csv": paths.normalized_csv.relative_to(output_root).as_posix(),
    }
    _atomic_write_text(
        paths.manifest,
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return paths
