"""Collect archived Open-Meteo model runs for leakage-safe FR-01 backtesting."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs025"


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _runs(start: datetime, end: datetime, availability_delay_hours: int) -> list[datetime]:
    first = start - timedelta(hours=availability_delay_hours)
    first = first.replace(hour=(first.hour // 6) * 6, minute=0, second=0, microsecond=0)
    last = end.replace(hour=(end.hour // 6) * 6, minute=0, second=0, microsecond=0)
    values: list[datetime] = []
    current = first
    while current <= last:
        values.append(current)
        current += timedelta(hours=6)
    return values


def _fetch_run(
    run: datetime,
    *,
    latitude: float,
    longitude: float,
    forecast_hours: int,
    timeout_seconds: float,
    attempts: int = 3,
) -> tuple[bytes, str]:
    query = urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "direct_normal_irradiance",
            "timezone": "UTC",
            "run": run.strftime("%Y-%m-%dT%H:%M"),
            "forecast_hours": forecast_hours,
            "models": MODEL,
        }
    )
    url = f"{API_URL}?{query}"
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=timeout_seconds) as response:  # nosec B310
                if response.status != 200:
                    raise RuntimeError(f"Open-Meteo HTTP {response.status}")
                return response.read(), url
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            if attempt == attempts:
                raise RuntimeError(f"Open-Meteo run fetch failed: {utc_z(run)}") from error
            time.sleep(float(attempt))
    raise AssertionError("unreachable")


def _parse_run(payload: bytes) -> tuple[dict[str, object], list[tuple[datetime, float]]]:
    try:
        document = json.loads(payload.decode("utf-8"))
        hourly = document["hourly"]
        units = document["hourly_units"]
        if units["direct_normal_irradiance"] != "W/m²":
            raise ValueError("unexpected DNI unit")
        times = hourly["time"]
        values = hourly["direct_normal_irradiance"]
        if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
            raise ValueError("invalid hourly arrays")
        points: list[tuple[datetime, float]] = []
        for raw_time, raw_value in zip(times, values, strict=True):
            if raw_value is None:
                continue
            timestamp = datetime.fromisoformat(raw_time).replace(tzinfo=UTC)
            value = float(raw_value)
            if value < 0:
                raise ValueError("negative DNI")
            points.append((timestamp, value))
        metadata = {
            "grid_latitude": document.get("latitude"),
            "grid_longitude": document.get("longitude"),
            "grid_elevation_m": document.get("elevation"),
            "timezone": document.get("timezone"),
        }
        return metadata, points
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid Open-Meteo single-run response") from error


def collect(args: argparse.Namespace) -> int:
    start = parse_utc(args.start)
    end = parse_utc(args.end)
    if end <= start:
        raise ValueError("end must be later than start")
    runs = _runs(start, end, args.availability_delay_hours)
    retrieved_at = datetime.now(UTC)
    raw_dir = args.output_root / "raw" / "open_meteo_single_runs"
    normalized_dir = args.output_root / "normalized" / "open_meteo"
    stem = f"{start:%Y%m%dT%H%M%SZ}_{end:%Y%m%dT%H%M%SZ}_open_meteo_runs"
    csv_path = args.normalized_csv or normalized_dir / f"{stem}.csv"
    manifest_path = args.manifest_path or args.output_root / "manifests" / f"{stem}_collection.json"
    resolved_root = args.output_root.resolve()
    if not csv_path.resolve().is_relative_to(resolved_root):
        raise ValueError("normalized-csv must be inside output-root")
    if not manifest_path.resolve().is_relative_to(resolved_root):
        raise ValueError("manifest-path must be inside output-root")
    if args.fail_if_output_exists and (csv_path.exists() or manifest_path.exists()):
        raise ValueError("refusing to overwrite an existing frozen collection")
    rows: list[dict[str, object]] = []
    raw_entries: list[dict[str, object]] = []
    grid_metadata: dict[str, object] | None = None

    for index, run in enumerate(runs, start=1):
        payload, _url = _fetch_run(
            run,
            latitude=args.latitude,
            longitude=args.longitude,
            forecast_hours=args.forecast_hours,
            timeout_seconds=args.timeout_seconds,
        )
        metadata, points = _parse_run(payload)
        grid_metadata = metadata
        stamp = run.strftime("%Y%m%dT%H%M%SZ")
        raw_path = raw_dir / f"{stamp}_{MODEL}.json"
        if args.fail_if_output_exists and raw_path.exists():
            raise ValueError(f"refusing to overwrite an existing raw payload: {raw_path}")
        _atomic_write(raw_path, payload)
        snapshot_id = f"{MODEL}_{stamp}"
        available_at = run + timedelta(hours=args.availability_delay_hours)
        for valid_time, dni in points:
            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "snapshot_available_at_utc": utc_z(available_at),
                    "issued_at_utc": utc_z(run),
                    "retrieved_at_utc": utc_z(retrieved_at),
                    "valid_time_utc": utc_z(valid_time),
                    "open_meteo_dni_W_m2": dni,
                    "model": MODEL,
                    "temporal_meaning": "preceding_hour_mean",
                    "availability_assumption_hours": args.availability_delay_hours,
                }
            )
        raw_entries.append(
            {
                "run_utc": utc_z(run),
                "path": raw_path.relative_to(args.output_root).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "point_count": len(points),
            }
        )
        print(f"run {index}/{len(runs)} collected: {utc_z(run)} points={len(points)}")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    temporary_csv = csv_path.with_name(f".{csv_path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_csv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_csv, csv_path)
    finally:
        if temporary_csv.exists():
            temporary_csv.unlink()

    normalized_payload = csv_path.read_bytes()
    manifest = {
        "provider": "open_meteo",
        "product": "single_runs_hourly_direct_normal_irradiance",
        "model": MODEL,
        "requested_start_utc": utc_z(start),
        "requested_end_utc": utc_z(end),
        "retrieved_at_utc": utc_z(retrieved_at),
        "request_latitude": args.latitude,
        "request_longitude": args.longitude,
        "grid": grid_metadata,
        "run_interval_hours": 6,
        "availability_assumption_hours": args.availability_delay_hours,
        "availability_note": (
            "Conservative reconstructed availability time; run initialization is not publication time."
        ),
        "dni_unit": "W/m²",
        "temporal_meaning": "preceding_hour_mean",
        "row_count": len(rows),
        "normalized_csv": csv_path.relative_to(args.output_root).as_posix(),
        "normalized_csv_sha256": hashlib.sha256(normalized_payload).hexdigest(),
        "raw_payloads": raw_entries,
    }
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
    )
    print(f"normalized_csv={csv_path.resolve()}")
    print(f"manifest={manifest_path.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="UTC-aware ISO timestamp")
    parser.add_argument("--end", required=True, help="UTC-aware ISO timestamp")
    parser.add_argument("--latitude", type=float, default=37.5714)
    parser.add_argument("--longitude", type=float, default=126.9658)
    parser.add_argument("--forecast-hours", type=int, default=30)
    parser.add_argument("--availability-delay-hours", type=int, default=7)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--output-root", type=Path, default=Path("data/fr01"))
    parser.add_argument(
        "--normalized-csv",
        type=Path,
        help="Optional exact normalized CSV path; it must remain under output-root.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        help="Optional exact collection manifest path.",
    )
    parser.add_argument(
        "--fail-if-output-exists",
        action="store_true",
        help="Refuse to overwrite frozen CSV, manifest, or raw payload files.",
    )
    return parser


def main() -> int:
    try:
        return collect(build_parser().parse_args())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
