"""Package frozen FR-01 training summaries as strict backend model artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.nowcast.kma_linear_model import DEFAULT_WEIGHT_BOUNDS
from app.nowcast.kma_model_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    FACTOR_BOUNDS,
    MODEL_VERSION,
    QUANTILE_LABELS,
    QUANTILES,
    load_model_bundle,
    sha256_file,
)
from app.nowcast.kma_model_dataset import FEATURE_NAMES


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"expected JSON object: {path}")
    return document


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _git_metadata() -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=WORKSPACE_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=WORKSPACE_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    tracked_sources = (
        Path("backend/app/nowcast/kma_linear_model.py"),
        Path("backend/app/nowcast/kma_ml.py"),
        Path("backend/app/nowcast/kma_model_dataset.py"),
        Path("backend/app/nowcast/kma_model_artifact.py"),
        Path("tools/ai_experiments/package_fr01_kma_models.py"),
    )
    return {
        "commit": commit,
        "working_tree_dirty_at_packaging": dirty,
        "source_sha256": {
            path.as_posix(): sha256_file(WORKSPACE_ROOT / path) for path in tracked_sources
        },
    }


def _file_metadata(path: Path) -> dict[str, Any]:
    return {"path": path.as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _bounds(summary: dict[str, Any]) -> dict[str, list[list[float]]]:
    recorded = summary.get("weight_bounds")
    if recorded is not None:
        return {label: recorded[label] for label in QUANTILE_LABELS}
    defaults = [list(pair) for pair in DEFAULT_WEIGHT_BOUNDS]
    return {label: defaults for label in QUANTILE_LABELS}


def _model_document(
    *,
    horizon: int,
    summary_path: Path,
    summary: dict[str, Any],
    kma_csv: Path,
    open_meteo_csv: Path,
    git: dict[str, Any],
) -> dict[str, Any]:
    if summary.get("horizon_minutes") != horizon:
        raise ValueError(f"summary horizon mismatch: {summary_path}")
    if summary.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError(f"summary feature contract mismatch: {summary_path}")
    if summary.get("final_test_used") is not False:
        raise ValueError(f"summary must record final_test_used=false: {summary_path}")
    weights = summary.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(QUANTILE_LABELS):
        raise ValueError(f"summary weights are incomplete: {summary_path}")
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "status": "frozen",
        "model_type": "linear_quantile_regression_p10_p50_p90",
        "horizon_minutes": horizon,
        "quantiles": list(QUANTILES),
        "feature_names": list(FEATURE_NAMES),
        "feature_units": {name: "dimensionless" for name in FEATURE_NAMES},
        "weights": {label: weights[label] for label in QUANTILE_LABELS},
        "weight_bounds": _bounds(summary),
        "factor_bounds": list(FACTOR_BOUNDS),
        "postprocessing": {
            "p10": "clip(raw_p10, 0.0, 1.05)",
            "p50": "max(p10, clip(raw_p50, 0.0, 1.05))",
            "p90": "max(p50, clip(raw_p90, 0.0, 1.05))",
        },
        "preprocessing_contract": {
            "version": "fr01_kma_features_v1",
            "dsr_unit": "W/m2 (project assumption recorded in source summary)",
            "dsr_to_dni": "pvlib.irradiance.erbs",
            "reference": "future KMA DSR converted to DNI with pvlib Erbs",
            "reference_type": "dsr_proxy",
            "cloud_patch_used": False,
            "open_meteo_aggregation": "preceding_hour_mean; no linear interpolation",
            "open_meteo_minimum_availability_delay_hours": 7,
            "minimum_solar_elevation_deg": 5.0,
            "direct_prior_observation_weight": "exp(-(horizon_minutes / 60) / 1.5)",
            "advection_impact": "fixed_zero",
        },
        "training_metadata": {
            "frozen_on": "2026-11-08",
            "development_period_utc": ["2026-10-05T00:00:00Z", "2026-11-01T23:50:00Z"],
            "split_boundaries_utc": summary.get("split_boundaries_utc"),
            "split_counts": summary.get("split_counts"),
            "selection_policy": "train and validation only; diagnostic report-only",
            "diagnostic_used_for_selection": False,
            "final_test_used": False,
            "source_summary": _file_metadata(summary_path),
            "input_files": {
                "kma": _file_metadata(kma_csv),
                "open_meteo": _file_metadata(open_meteo_csv),
            },
            "code": git,
        },
    }


def run(args: argparse.Namespace) -> int:
    summaries = {
        10: args.summary_10,
        30: args.summary_30,
        60: args.summary_60,
    }
    git = _git_metadata()
    entries: list[dict[str, Any]] = []
    for horizon, summary_path in summaries.items():
        summary = _read_json(summary_path)
        document = _model_document(
            horizon=horizon,
            summary_path=summary_path,
            summary=summary,
            kma_csv=args.kma_csv,
            open_meteo_csv=args.open_meteo_csv,
            git=git,
        )
        relative = Path(f"horizon_{horizon}") / "model.json"
        output = args.output_root / relative
        _write_json(output, document)
        entries.append(
            {
                "horizon_minutes": horizon,
                "path": relative.as_posix(),
                "sha256": sha256_file(output),
                "bytes": output.stat().st_size,
            }
        )

    manifest = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "status": "frozen",
        "model_family": "FR-01 KMA/Open-Meteo linear quantile nowcast",
        "packaged_on": "2026-11-08",
        "supported_horizons_minutes": [10, 30, 60],
        "models": entries,
        "selection_policy": {
            "diagnostic_used_for_selection": False,
            "final_test_used": False,
        },
        "code": git,
        "generation_command": ".\\.venv\\Scripts\\python.exe tools/ai_experiments/package_fr01_kma_models.py",
    }
    _write_json(args.output_root / "manifest.json", manifest)
    loaded = load_model_bundle(args.output_root)
    sample_features = np.asarray(
        [
            [1.0, 0.0, 0.0, 0.0, 0.0],
            [1.0, 0.25, 0.50, 0.75, 0.0],
            [1.0, 1.0, 1.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    clear_sky = np.asarray([250.0, 500.0, 1000.0], dtype=np.float64)
    max_factor_error = 0.0
    max_dni_error = 0.0
    for horizon, summary_path in summaries.items():
        source_weights = _read_json(summary_path)["weights"]
        loaded_raw = loaded[horizon].predict_raw_factors(sample_features)
        for label in QUANTILE_LABELS:
            expected = sample_features @ np.asarray(source_weights[label], dtype=np.float64)
            factor_error = float(np.max(np.abs(expected - loaded_raw[label])))
            max_factor_error = max(max_factor_error, factor_error)
            max_dni_error = max(
                max_dni_error,
                float(np.max(np.abs(expected * clear_sky - loaded_raw[label] * clear_sky))),
            )
    round_trip_status = (
        "PASS" if max_factor_error <= 1e-10 and max_dni_error <= 1e-6 else "FAIL"
    )
    manifest["round_trip_validation"] = {
        "status": round_trip_status,
        "sample_count_per_horizon": len(sample_features),
        "maximum_factor_absolute_error": max_factor_error,
        "factor_tolerance": 1e-10,
        "maximum_dni_absolute_error_W_m2": max_dni_error,
        "dni_tolerance_W_m2": 1e-6,
    }
    if round_trip_status != "PASS":
        raise ValueError("saved/loaded model round-trip prediction mismatch")
    _write_json(args.output_root / "manifest.json", manifest)
    loaded = load_model_bundle(args.output_root)
    print(f"model_version={MODEL_VERSION}")
    for horizon, model in loaded.items():
        print(f"horizon={horizon} status=PASS path={model.path.resolve()}")
    print(
        "round_trip=PASS "
        f"factor_max_error={max_factor_error:.3g} dni_max_error={max_dni_error:.3g}"
    )
    print(f"manifest={args.output_root.joinpath('manifest.json').resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    dataset = Path("data/fr01/datasets/development_20261005_20261101")
    parser.add_argument("--kma-csv", type=Path, default=dataset / "kma_gk2a.csv")
    parser.add_argument(
        "--open-meteo-csv", type=Path, default=dataset / "open_meteo_runs.csv"
    )
    parser.add_argument(
        "--summary-10",
        type=Path,
        default=Path("data/fr01/model/quantile_stage4_train_20261005/quantile_model_summary.json"),
    )
    parser.add_argument(
        "--summary-30",
        type=Path,
        default=Path("data/fr01/model/stage5/horizon_30min/quantile_model_summary.json"),
    )
    parser.add_argument(
        "--summary-60",
        type=Path,
        default=Path("data/fr01/model/stage5/horizon_60min/quantile_model_summary.json"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1"),
    )
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
