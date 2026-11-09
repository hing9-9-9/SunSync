"""Input-readiness contract for the one-time FR-01 frozen final evaluation."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from app.nowcast.kma_model_artifact import (
    LoadedQuantileArtifact,
    load_model_bundle,
    sha256_file,
)
from app.nowcast.kma_model_dataset import (
    ALLOWED_HORIZON_MINUTES,
    ModelDatasetRow,
    parse_utc,
    utc_z,
)

FINAL_START = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
FINAL_END = datetime(2026, 11, 5, 23, 50, tzinfo=UTC)
INTERVAL = timedelta(minutes=10)
RUN_INTERVAL = timedelta(hours=6)
AVAILABILITY_DELAY = timedelta(hours=7)
MODEL = "ecmwf_ifs025"
QUANTILES = (("p10", 0.10), ("p50", 0.50), ("p90", 0.90))


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"expected JSON object: {path}")
    return document


def _timeline(start: datetime, end: datetime, step: timedelta) -> tuple[datetime, ...]:
    values: list[datetime] = []
    current = start
    while current <= end:
        values.append(current)
        current += step
    return tuple(values)


def _expected_runs(start: datetime, end: datetime) -> tuple[datetime, ...]:
    first = (start - AVAILABILITY_DELAY).replace(
        hour=((start - AVAILABILITY_DELAY).hour // 6) * 6,
        minute=0,
        second=0,
        microsecond=0,
    )
    last = end.replace(hour=(end.hour // 6) * 6, minute=0, second=0, microsecond=0)
    return _timeline(first, last, RUN_INTERVAL)


def _validate_kma_timestamps(path: Path, start: datetime, end: datetime) -> int:
    timestamps: list[datetime] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if "timestamp_utc" not in (reader.fieldnames or ()):
            raise ValueError("KMA CSV missing timestamp_utc")
        timestamps = [parse_utc(row["timestamp_utc"], field="timestamp_utc") for row in reader]
    expected = list(_timeline(start, end, INTERVAL))
    if timestamps != expected:
        raise ValueError("KMA final-test timestamps are incomplete, duplicated, or unordered")
    return len(timestamps)


def _load_open_meteo_metadata(
    path: Path,
) -> tuple[dict[datetime, set[datetime]], dict[datetime, datetime], int]:
    required = {
        "snapshot_id",
        "snapshot_available_at_utc",
        "issued_at_utc",
        "valid_time_utc",
        "model",
        "temporal_meaning",
        "availability_assumption_hours",
    }
    valid_by_run: dict[datetime, set[datetime]] = defaultdict(set)
    available_by_run: dict[datetime, datetime] = {}
    seen: set[tuple[datetime, datetime]] = set()
    row_count = 0
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Open-Meteo CSV missing columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            issued = parse_utc(row["issued_at_utc"], field="issued_at_utc")
            available = parse_utc(
                row["snapshot_available_at_utc"], field="snapshot_available_at_utc"
            )
            valid = parse_utc(row["valid_time_utc"], field="valid_time_utc")
            expected_id = f"{MODEL}_{issued:%Y%m%dT%H%M%SZ}"
            if row["snapshot_id"] != expected_id:
                raise ValueError(f"unexpected snapshot_id at line {line_number}")
            if row["model"] != MODEL:
                raise ValueError(f"unexpected model at line {line_number}")
            if row["temporal_meaning"] != "preceding_hour_mean":
                raise ValueError(f"unexpected temporal meaning at line {line_number}")
            if int(row["availability_assumption_hours"]) != 7:
                raise ValueError(f"unexpected availability delay at line {line_number}")
            if available != issued + AVAILABILITY_DELAY:
                raise ValueError(f"available_at is not issued_at + 7h at line {line_number}")
            key = (issued, valid)
            if key in seen:
                raise ValueError(f"duplicate run/valid_time at line {line_number}")
            seen.add(key)
            if issued in available_by_run and available_by_run[issued] != available:
                raise ValueError(f"inconsistent available_at at line {line_number}")
            available_by_run[issued] = available
            valid_by_run[issued].add(valid)
            row_count += 1
    if not row_count:
        raise ValueError("Open-Meteo CSV is empty")
    for run, values in valid_by_run.items():
        ordered = sorted(values)
        if any(right - left != timedelta(hours=1) for left, right in pairwise(ordered)):
            raise ValueError(f"non-hourly valid-time sequence for run {utc_z(run)}")
    return dict(valid_by_run), available_by_run, row_count


def _validate_temporal_coverage(
    valid_by_run: dict[datetime, set[datetime]],
    available_by_run: dict[datetime, datetime],
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    coverage: dict[str, int] = {}
    for horizon_minutes in ALLOWED_HORIZON_MINUTES:
        checked = 0
        for origin in _timeline(start, end, INTERVAL):
            target = origin + timedelta(minutes=horizon_minutes)
            if target > end:
                continue
            eligible = [run for run, available in available_by_run.items() if available <= origin]
            if not eligible:
                raise ValueError(f"no available Open-Meteo run at {utc_z(origin)}")
            latest_available = max(available_by_run[run] for run in eligible)
            selected = max(run for run in eligible if available_by_run[run] == latest_available)
            valid_times = valid_by_run[selected]
            exact_or_covering = target in valid_times or any(
                timedelta(0) < valid_time - target <= timedelta(hours=1)
                for valid_time in valid_times
            )
            if not exact_or_covering:
                raise ValueError(
                    f"Open-Meteo coverage gap: horizon={horizon_minutes} origin={utc_z(origin)}"
                )
            checked += 1
        coverage[str(horizon_minutes)] = checked
    return coverage


def validate_final_test_inputs(
    *,
    root: Path,
    kma_csv: Path,
    kma_manifest: Path,
    open_meteo_csv: Path,
    open_meteo_manifest: Path,
    model_root: Path,
    start: datetime = FINAL_START,
    end: datetime = FINAL_END,
) -> dict[str, Any]:
    """Validate only provenance, time coverage, and hashes; never calculate model metrics."""

    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("invalid final-test period")
    resolved_root = root.resolve()
    for path in (kma_csv, kma_manifest, open_meteo_csv, open_meteo_manifest):
        if not path.resolve().is_relative_to(resolved_root):
            raise ValueError(f"final-test input escapes frozen root: {path}")

    kma_document = _read_json(kma_manifest)
    if kma_document.get("status") != "frozen_final_test":
        raise ValueError("KMA dataset manifest is not frozen_final_test")
    kma_rows = _validate_kma_timestamps(kma_csv, start, end)
    recorded_kma_hash = kma_document.get("kma", {}).get("sha256")
    if recorded_kma_hash != sha256_file(kma_csv):
        raise ValueError("KMA CSV SHA-256 mismatch")

    collection = _read_json(open_meteo_manifest)
    if collection.get("model") != MODEL:
        raise ValueError("unexpected Open-Meteo model")
    if collection.get("availability_assumption_hours") != 7:
        raise ValueError("Open-Meteo manifest availability delay must be 7h")
    if collection.get("temporal_meaning") != "preceding_hour_mean":
        raise ValueError("Open-Meteo manifest temporal meaning mismatch")
    if collection.get("normalized_csv_sha256") != sha256_file(open_meteo_csv):
        raise ValueError("Open-Meteo CSV SHA-256 mismatch")

    expected_runs = _expected_runs(start, end)
    raw_entries = collection.get("raw_payloads")
    if not isinstance(raw_entries, list) or len(raw_entries) != len(expected_runs):
        raise ValueError("Open-Meteo raw payload count does not match expected runs")
    raw_runs: list[datetime] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise TypeError("invalid raw payload manifest entry")
        run = parse_utc(str(entry.get("run_utc")), field="run_utc")
        relative = entry.get("path")
        if not isinstance(relative, str):
            raise TypeError("raw payload path must be a string")
        raw_path = (root / relative).resolve()
        if not raw_path.is_relative_to(resolved_root):
            raise ValueError("raw payload path escapes frozen root")
        if sha256_file(raw_path) != entry.get("sha256"):
            raise ValueError(f"raw payload SHA-256 mismatch: {relative}")
        raw_runs.append(run)
    if tuple(raw_runs) != expected_runs:
        raise ValueError("Open-Meteo run sequence is incomplete or unordered")

    valid_by_run, available_by_run, open_meteo_rows = _load_open_meteo_metadata(open_meteo_csv)
    if tuple(sorted(valid_by_run)) != expected_runs:
        raise ValueError("normalized Open-Meteo run sequence does not match raw manifest")
    coverage = _validate_temporal_coverage(valid_by_run, available_by_run, start, end)
    models = load_model_bundle(model_root)

    return {
        "readiness_schema_version": "1.0",
        "status": "ready_for_one_time_evaluation",
        "performance_evaluated": False,
        "final_evaluation_executed": False,
        "period": {"start_utc": utc_z(start), "end_utc": utc_z(end)},
        "site": {"latitude": 37.5714, "longitude": 126.9658},
        "kma": {"path": kma_csv.name, "row_count": kma_rows, "sha256": sha256_file(kma_csv)},
        "open_meteo": {
            "path": open_meteo_csv.name,
            "row_count": open_meteo_rows,
            "run_count": len(valid_by_run),
            "first_run_utc": utc_z(min(valid_by_run)),
            "last_run_utc": utc_z(max(valid_by_run)),
            "availability_delay_hours": 7,
            "temporal_meaning": "preceding_hour_mean",
            "sha256": sha256_file(open_meteo_csv),
            "collection_manifest": open_meteo_manifest.name,
            "collection_manifest_sha256": sha256_file(open_meteo_manifest),
        },
        "model_artifacts": {
            "version": "fr01_kma_quantile_v1",
            "horizons_minutes": sorted(models),
            "manifest_path": model_root.joinpath("manifest.json").as_posix(),
            "manifest_sha256": sha256_file(model_root / "manifest.json"),
        },
        "temporal_coverage_origins": coverage,
        "checks": [
            "KMA exact 10-minute UTC timeline",
            "Open-Meteo exact 6-hour run sequence",
            "Open-Meteo available_at equals issued_at plus 7 hours",
            "Open-Meteo hourly valid-time continuity",
            "raw, normalized, and model SHA-256 integrity",
            "10, 30, and 60-minute target-time forecast coverage",
        ],
        "usage_rule": (
            "Do not fit, tune, select, or inspect performance before the explicit one-time "
            "final evaluation. Final results must not change the frozen V1 model."
        ),
    }


def evaluate_final_rows(
    model: LoadedQuantileArtifact, rows: tuple[ModelDatasetRow, ...]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply one frozen model to fixed rows; this function has no fitting path."""

    if not rows:
        raise ValueError("cannot evaluate an empty final-test row set")
    if any(row.horizon_minutes != model.horizon_minutes for row in rows):
        raise ValueError("row/model horizon mismatch")
    features = np.asarray([row.features() for row in rows], dtype=np.float64)
    factors = model.predict_factors(features)
    clear_sky = np.asarray([row.clear_sky_dni_target_W_m2 for row in rows], dtype=np.float64)
    reference = np.asarray([row.reference_dni_W_m2 for row in rows], dtype=np.float64)
    baseline = np.asarray([row.open_meteo_dni_W_m2 for row in rows], dtype=np.float64)
    predictions = {label: factors[label] * clear_sky for label, _ in QUANTILES}

    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        records.append(
            {
                "prediction_origin_utc": row.prediction_origin_utc,
                "valid_time_utc": row.valid_time_utc,
                "horizon_minutes": row.horizon_minutes,
                "reference_dni_W_m2": float(reference[index]),
                "reference_type": "dsr_proxy",
                "open_meteo_dni_W_m2": float(baseline[index]),
                "p10_dni_W_m2": float(predictions["p10"][index]),
                "p50_dni_W_m2": float(predictions["p50"][index]),
                "p90_dni_W_m2": float(predictions["p90"][index]),
                "cloud_patch_used": False,
            }
        )

    p50_error = predictions["p50"] - reference
    baseline_error = baseline - reference
    pinball: dict[str, float] = {}
    cdf: dict[str, float] = {}
    for label, quantile in QUANTILES:
        error = reference - predictions[label]
        pinball[label] = float(np.mean(np.maximum(quantile * error, (quantile - 1.0) * error)))
        cdf[label] = float(np.mean(reference <= predictions[label]))
    covered = (reference >= predictions["p10"]) & (reference <= predictions["p90"])
    metrics = {
        "row_count": len(rows),
        "p50": {
            "mae_W_m2": float(np.mean(np.abs(p50_error))),
            "rmse_W_m2": math.sqrt(float(np.mean(p50_error**2))),
            "bias_W_m2": float(np.mean(p50_error)),
        },
        "open_meteo": {
            "mae_W_m2": float(np.mean(np.abs(baseline_error))),
            "rmse_W_m2": math.sqrt(float(np.mean(baseline_error**2))),
            "bias_W_m2": float(np.mean(baseline_error)),
        },
        "pinball_loss_W_m2": pinball,
        "empirical_cdf_at_quantile": cdf,
        "picp_p10_p90": float(np.mean(covered)),
        "mean_interval_width_W_m2": float(np.mean(predictions["p90"] - predictions["p10"])),
    }
    return records, metrics
