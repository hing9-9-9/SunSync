"""Evaluate frozen FR-01 artifacts on validation rows and interpretable segments."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.nowcast.kma_model_artifact import (
    LoadedQuantileArtifact,
    load_model_bundle,
    sha256_file,
)
from app.nowcast.kma_model_dataset import (
    ALLOWED_HORIZON_MINUTES,
    ModelDatasetRow,
    assert_training_data_excludes_final_test,
    build_model_dataset,
    load_kma_points,
    load_open_meteo_points,
    parse_utc,
    split_development_rows,
    validate_no_leakage,
)

from engine.fr01.models import Site

KST = ZoneInfo("Asia/Seoul")
QUANTILES = (("p10", 0.10), ("p50", 0.50), ("p90", 0.90))


def _mean(values: np.ndarray) -> float:
    if len(values) == 0:
        raise ValueError("cannot calculate metrics for an empty segment")
    return float(np.mean(values))


def _metrics(model: LoadedQuantileArtifact, rows: Sequence[ModelDatasetRow]) -> dict[str, Any]:
    features = np.asarray([row.features() for row in rows], dtype=np.float64)
    factors = model.predict_factors(features)
    clear_sky = np.asarray([row.clear_sky_dni_target_W_m2 for row in rows], dtype=np.float64)
    reference = np.asarray([row.reference_dni_W_m2 for row in rows], dtype=np.float64)
    baseline = np.asarray([row.open_meteo_dni_W_m2 for row in rows], dtype=np.float64)
    predictions = {label: factor * clear_sky for label, factor in factors.items()}
    model_error = predictions["p50"] - reference
    baseline_error = baseline - reference
    covered = (reference >= predictions["p10"]) & (reference <= predictions["p90"])
    pinball: dict[str, float] = {}
    cdf: dict[str, float] = {}
    for label, quantile in QUANTILES:
        error = reference - predictions[label]
        pinball[label] = _mean(np.maximum(quantile * error, (quantile - 1.0) * error))
        cdf[label] = _mean(reference <= predictions[label])
    return {
        "row_count": len(rows),
        "p50": {
            "mae_W_m2": _mean(np.abs(model_error)),
            "rmse_W_m2": math.sqrt(_mean(model_error**2)),
            "bias_W_m2": _mean(model_error),
        },
        "open_meteo": {
            "mae_W_m2": _mean(np.abs(baseline_error)),
            "rmse_W_m2": math.sqrt(_mean(baseline_error**2)),
            "bias_W_m2": _mean(baseline_error),
        },
        "pinball_loss_W_m2": pinball,
        "empirical_cdf_at_quantile": cdf,
        "picp_p10_p90": _mean(covered),
        "mean_interval_width_W_m2": _mean(predictions["p90"] - predictions["p10"]),
    }


def _segments(
    model: LoadedQuantileArtifact,
    rows: Sequence[ModelDatasetRow],
    definitions: dict[str, Callable[[ModelDatasetRow], bool]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, predicate in definitions.items():
        selected = [row for row in rows if predicate(row)]
        result[name] = (
            _metrics(model, selected)
            if selected
            else {"row_count": 0, "status": "insufficient_sample"}
        )
    return result


def _local_hour(row: ModelDatasetRow) -> int:
    return parse_utc(row.valid_time_utc, field="valid_time_utc").astimezone(KST).hour


def _evaluate_horizon(
    model: LoadedQuantileArtifact, rows: tuple[ModelDatasetRow, ...]
) -> dict[str, Any]:
    return {
        "overall": _metrics(model, rows),
        "by_local_time": _segments(
            model,
            rows,
            {
                "morning_06_09": lambda row: 6 <= _local_hour(row) < 10,
                "midday_10_13": lambda row: 10 <= _local_hour(row) < 14,
                "afternoon_14_18": lambda row: 14 <= _local_hour(row) < 19,
            },
        ),
        "by_solar_elevation": _segments(
            model,
            rows,
            {
                "low_5_20deg": lambda row: 5 <= row.solar_elevation_target_deg < 20,
                "medium_20_45deg": lambda row: 20 <= row.solar_elevation_target_deg < 45,
                "high_45deg_plus": lambda row: row.solar_elevation_target_deg >= 45,
            },
        ),
        "by_cloud_cover": _segments(
            model,
            rows,
            {
                "clear_0_20pct": lambda row: 0 <= row.cloud_cover_input_pct < 20,
                "partly_cloudy_20_70pct": lambda row: 20 <= row.cloud_cover_input_pct < 70,
                "cloudy_70_100pct": lambda row: 70 <= row.cloud_cover_input_pct <= 100,
            },
        ),
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    models = load_model_bundle(args.artifact_root)
    kma = load_kma_points(args.kma_csv, assume_dsr_w_m2=True)
    assert_training_data_excludes_final_test(kma)
    open_meteo = load_open_meteo_points(args.open_meteo_csv)
    site = Site(args.site_lat, args.site_lon, args.timezone)
    validation_rows: dict[int, tuple[ModelDatasetRow, ...]] = {}
    horizon_results: dict[str, Any] = {}
    for horizon in ALLOWED_HORIZON_MINUTES:
        build = build_model_dataset(
            kma,
            open_meteo,
            site,
            horizon_minutes=horizon,
            dsr_unit_assumed=True,
        )
        validate_no_leakage(build.rows)
        rows = split_development_rows(build.rows).validation
        validation_rows[horizon] = rows
        horizon_results[str(horizon)] = _evaluate_horizon(models[horizon], rows)

    common_origins = set.intersection(
        *(
            {row.prediction_origin_utc for row in validation_rows[horizon]}
            for horizon in ALLOWED_HORIZON_MINUTES
        )
    )
    common: dict[str, Any] = {"origin_count": len(common_origins), "metrics": {}}
    for horizon in ALLOWED_HORIZON_MINUTES:
        selected = tuple(
            row
            for row in validation_rows[horizon]
            if row.prediction_origin_utc in common_origins
        )
        common["metrics"][str(horizon)] = _metrics(models[horizon], selected)

    document = {
        "evaluation_type": "frozen_model_validation_segments",
        "model_version": "fr01_kma_quantile_v1",
        "evaluated_on": "2026-11-08",
        "selection_policy": "validation only; diagnostic not evaluated; final test unused",
        "diagnostic_evaluated": False,
        "final_test_used": False,
        "generation_command": ".\\.venv\\Scripts\\python.exe tools/ai_experiments/evaluate_fr01_kma_models.py",
        "inputs": {
            "manifest": {
                "path": args.artifact_root.joinpath("manifest.json").as_posix(),
                "sha256": sha256_file(args.artifact_root / "manifest.json"),
            },
            "kma": {"path": args.kma_csv.as_posix(), "sha256": sha256_file(args.kma_csv)},
            "open_meteo": {
                "path": args.open_meteo_csv.as_posix(),
                "sha256": sha256_file(args.open_meteo_csv),
            },
        },
        "segment_contract": {
            "time_zone": "Asia/Seoul",
            "local_time": ["06<=hour<10", "10<=hour<14", "14<=hour<19"],
            "solar_elevation_deg": ["5<=x<20", "20<=x<45", "45<=x"],
            "cloud_cover_pct": ["0<=x<20", "20<=x<70", "70<=x<=100"],
        },
        "horizons": horizon_results,
        "common_validation": common,
    }
    _write_json(args.output, document)

    print("=== FROZEN MODEL VALIDATION ===")
    for horizon in ALLOWED_HORIZON_MINUTES:
        metrics = horizon_results[str(horizon)]["overall"]
        print(
            f"horizon={horizon:>2} rows={metrics['row_count']:>3} "
            f"P50_MAE={metrics['p50']['mae_W_m2']:.2f} "
            f"P50_RMSE={metrics['p50']['rmse_W_m2']:.2f} "
            f"OM_MAE={metrics['open_meteo']['mae_W_m2']:.2f} "
            f"PICP={metrics['picp_p10_p90']:.3f}"
        )
    print(f"common_validation_origins={len(common_origins)}")
    print("diagnostic_evaluated=NO")
    print("final_test_used=NO")
    print(f"output={args.output.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    dataset = Path("data/fr01/datasets/development_20261005_20261101")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1"),
    )
    parser.add_argument("--kma-csv", type=Path, default=dataset / "kma_gk2a.csv")
    parser.add_argument(
        "--open-meteo-csv", type=Path, default=dataset / "open_meteo_runs.csv"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/fr01/model/stage6/validation_segment_evaluation.json"),
    )
    parser.add_argument("--site-lat", type=float, default=37.5714)
    parser.add_argument("--site-lon", type=float, default=126.9658)
    parser.add_argument("--timezone", default="Asia/Seoul")
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
