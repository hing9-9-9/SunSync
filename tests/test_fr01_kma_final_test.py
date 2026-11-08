from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.nowcast.kma_final_test import evaluate_final_rows, validate_final_test_inputs
from app.nowcast.kma_model_artifact import load_model_bundle, sha256_file
from app.nowcast.kma_model_dataset import ModelDatasetRow

from tools.ai_experiments import evaluate_fr01_kma_final_test

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1"


def _json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _z(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _readiness_fixture(tmp_path: Path) -> tuple[dict, Path]:
    start = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    kma_csv = tmp_path / "kma.csv"
    with kma_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["timestamp_utc"])
        writer.writeheader()
        current = start
        while current <= end:
            writer.writerow({"timestamp_utc": _z(current)})
            current += timedelta(minutes=10)
    kma_manifest = tmp_path / "dataset_manifest.json"
    _json(
        kma_manifest,
        {"status": "frozen_final_test", "kma": {"sha256": sha256_file(kma_csv)}},
    )

    runs = (
        datetime(2026, 11, 1, 12, tzinfo=UTC),
        datetime(2026, 11, 1, 18, tzinfo=UTC),
        datetime(2026, 11, 2, 0, tzinfo=UTC),
    )
    open_meteo_csv = tmp_path / "open_meteo.csv"
    fields = [
        "snapshot_id",
        "snapshot_available_at_utc",
        "issued_at_utc",
        "valid_time_utc",
        "model",
        "temporal_meaning",
        "availability_assumption_hours",
    ]
    with open_meteo_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            for offset in range(1, 31):
                writer.writerow(
                    {
                        "snapshot_id": f"ecmwf_ifs025_{run:%Y%m%dT%H%M%SZ}",
                        "snapshot_available_at_utc": _z(run + timedelta(hours=7)),
                        "issued_at_utc": _z(run),
                        "valid_time_utc": _z(run + timedelta(hours=offset)),
                        "model": "ecmwf_ifs025",
                        "temporal_meaning": "preceding_hour_mean",
                        "availability_assumption_hours": 7,
                    }
                )

    raw_entries = []
    for run in runs:
        relative = Path("raw") / f"{run:%Y%m%dT%H%M%SZ}.json"
        raw_path = tmp_path / relative
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"fixture-{_z(run)}".encode()
        raw_path.write_bytes(payload)
        raw_entries.append(
            {
                "run_utc": _z(run),
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "point_count": 30,
            }
        )
    open_meteo_manifest = tmp_path / "open_meteo_collection.json"
    _json(
        open_meteo_manifest,
        {
            "model": "ecmwf_ifs025",
            "availability_assumption_hours": 7,
            "temporal_meaning": "preceding_hour_mean",
            "normalized_csv_sha256": sha256_file(open_meteo_csv),
            "raw_payloads": raw_entries,
        },
    )
    document = validate_final_test_inputs(
        root=tmp_path,
        kma_csv=kma_csv,
        kma_manifest=kma_manifest,
        open_meteo_csv=open_meteo_csv,
        open_meteo_manifest=open_meteo_manifest,
        model_root=MODEL_ROOT,
        start=start,
        end=end,
    )
    return document, open_meteo_csv


def test_readiness_fixture_checks_runs_delay_hashes_and_coverage(tmp_path: Path) -> None:
    document, _ = _readiness_fixture(tmp_path)
    assert document["status"] == "ready_for_one_time_evaluation"
    assert document["performance_evaluated"] is False
    assert document["open_meteo"]["run_count"] == 3
    assert document["temporal_coverage_origins"] == {"10": 6, "30": 4, "60": 1}


def test_readiness_rejects_changed_normalized_input(tmp_path: Path) -> None:
    _, open_meteo_csv = _readiness_fixture(tmp_path)
    with open_meteo_csv.open("a", encoding="utf-8") as stream:
        stream.write("tampered\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_final_test_inputs(
            root=tmp_path,
            kma_csv=tmp_path / "kma.csv",
            kma_manifest=tmp_path / "dataset_manifest.json",
            open_meteo_csv=open_meteo_csv,
            open_meteo_manifest=tmp_path / "open_meteo_collection.json",
            model_root=MODEL_ROOT,
            start=datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
            end=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
        )


def _row(reference: float) -> ModelDatasetRow:
    return ModelDatasetRow(
        prediction_origin_utc="2026-11-02T00:00:00Z",
        input_kma_timestamp_utc="2026-11-02T00:00:00Z",
        open_meteo_snapshot_available_at_utc="2026-11-01T19:00:00Z",
        open_meteo_issued_at_utc="2026-11-01T12:00:00Z",
        open_meteo_valid_time_utc="2026-11-02T00:10:00Z",
        target_dsr_timestamp_utc="2026-11-02T00:10:00Z",
        valid_time_utc="2026-11-02T00:10:00Z",
        horizon_minutes=10,
        dsr_input_W_m2=400.0,
        cloud_cover_input_pct=20.0,
        open_meteo_dni_W_m2=500.0,
        solar_elevation_origin_deg=20.0,
        solar_elevation_target_deg=22.0,
        clear_sky_dni_origin_W_m2=800.0,
        clear_sky_dni_target_W_m2=850.0,
        kma_dni_prior_W_m2=450.0,
        intercept=1.0,
        direct_prior_factor=0.55,
        open_meteo_factor=0.60,
        cloud_transmittance=0.80,
        advection_impact=0.0,
        reference_dni_W_m2=reference,
        reference_dni_factor=reference / 850.0,
        reference_type="dsr_proxy",
        cloud_patch_used=False,
    )


def test_final_evaluator_core_only_applies_loaded_frozen_model() -> None:
    model = load_model_bundle(MODEL_ROOT)[10]
    records, metrics = evaluate_final_rows(model, (_row(450.0), _row(550.0)))
    assert metrics["row_count"] == 2
    assert all(record["reference_type"] == "dsr_proxy" for record in records)
    assert all(record["cloud_patch_used"] is False for record in records)
    assert all(record["p10_dni_W_m2"] <= record["p50_dni_W_m2"] for record in records)
    assert all(record["p50_dni_W_m2"] <= record["p90_dni_W_m2"] for record in records)


def test_final_evaluator_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="refusing to evaluate"):
        evaluate_fr01_kma_final_test.run(
            argparse.Namespace(confirm_one_time_final_evaluation=False)
        )


def test_final_evaluator_source_contains_no_fit_or_optimizer_path() -> None:
    source = inspect.getsource(evaluate_fr01_kma_final_test)
    assert ".fit(" not in source
    assert "scipy.optimize" not in source
    assert "train_fr01" not in source
