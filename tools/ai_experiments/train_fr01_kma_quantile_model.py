"""Train and evaluate FR-01 P10/P50/P90 models for one allowed horizon."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.nowcast.kma_linear_model import (
    DEFAULT_WEIGHT_BOUNDS,
    LinearQuantileRegressor,
    enforce_monotonic_quantiles,
)
from app.nowcast.kma_model_dataset import (
    ALLOWED_HORIZON_MINUTES,
    FEATURE_NAMES,
    ModelDatasetRow,
    assert_training_data_excludes_final_test,
    build_model_dataset,
    load_kma_points,
    load_open_meteo_points,
    split_development_rows,
    validate_no_leakage,
    write_dataset_csv,
)

from engine.fr01.models import Site

QUANTILES = (0.10, 0.50, 0.90)
QUANTILE_LABELS = {0.10: "p10", 0.50: "p50", 0.90: "p90"}


def _arrays(rows: Sequence[ModelDatasetRow]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([row.features() for row in rows], dtype=np.float64)
    y = np.asarray([row.reference_dni_factor for row in rows], dtype=np.float64)
    return x, y


def _pinball_loss(reference: np.ndarray, prediction: np.ndarray, quantile: float) -> float:
    error = reference - prediction
    return float(np.mean(np.maximum(quantile * error, (quantile - 1.0) * error)))


def _mae_rmse(reference: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    error = prediction - reference
    return {
        "mae_W_m2": float(np.mean(np.abs(error))),
        "rmse_W_m2": float(np.sqrt(np.mean(error**2))),
        "bias_W_m2": float(np.mean(error)),
    }


def _evaluate(
    models: dict[float, LinearQuantileRegressor], rows: Sequence[ModelDatasetRow]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    x, _ = _arrays(rows)
    raw_factors = {quantile: models[quantile].predict_raw_factors(x) for quantile in QUANTILES}
    p10_factor, p50_factor, p90_factor = enforce_monotonic_quantiles(
        raw_factors[0.10], raw_factors[0.50], raw_factors[0.90]
    )
    corrected_factors = {0.10: p10_factor, 0.50: p50_factor, 0.90: p90_factor}

    clear_sky = np.asarray([row.clear_sky_dni_target_W_m2 for row in rows], dtype=np.float64)
    references = np.asarray([row.reference_dni_W_m2 for row in rows], dtype=np.float64)
    baselines = np.asarray([row.open_meteo_dni_W_m2 for row in rows], dtype=np.float64)
    raw_dni = {quantile: raw_factors[quantile] * clear_sky for quantile in QUANTILES}
    corrected_dni = {
        quantile: corrected_factors[quantile] * clear_sky for quantile in QUANTILES
    }

    crossing_10_50 = raw_factors[0.10] > raw_factors[0.50]
    crossing_50_90 = raw_factors[0.50] > raw_factors[0.90]
    any_crossing = crossing_10_50 | crossing_50_90
    covered = (references >= corrected_dni[0.10]) & (references <= corrected_dni[0.90])
    interval_width = corrected_dni[0.90] - corrected_dni[0.10]

    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        record: dict[str, Any] = {
            "prediction_origin_utc": row.prediction_origin_utc,
            "valid_time_utc": row.valid_time_utc,
            "horizon_minutes": row.horizon_minutes,
            "reference_dni_W_m2": round(row.reference_dni_W_m2, 6),
            "open_meteo_dni_W_m2": round(row.open_meteo_dni_W_m2, 6),
        }
        for quantile in QUANTILES:
            label = QUANTILE_LABELS[quantile]
            record[f"raw_{label}_factor"] = round(float(raw_factors[quantile][index]), 9)
            record[f"raw_{label}_dni_W_m2"] = round(float(raw_dni[quantile][index]), 6)
            record[f"{label}_factor"] = round(float(corrected_factors[quantile][index]), 9)
            record[f"{label}_dni_W_m2"] = round(float(corrected_dni[quantile][index]), 6)
        record["raw_quantile_crossing"] = bool(any_crossing[index])
        record["reference_inside_p10_p90"] = bool(covered[index])
        record["interval_width_W_m2"] = round(float(interval_width[index]), 6)
        records.append(record)

    metrics: dict[str, Any] = {
        "row_count": len(rows),
        "p50": _mae_rmse(references, corrected_dni[0.50]),
        "open_meteo": _mae_rmse(references, baselines),
        "pinball_loss_W_m2": {
            QUANTILE_LABELS[quantile]: _pinball_loss(
                references, corrected_dni[quantile], quantile
            )
            for quantile in QUANTILES
        },
        "raw_pinball_loss_W_m2": {
            QUANTILE_LABELS[quantile]: _pinball_loss(references, raw_dni[quantile], quantile)
            for quantile in QUANTILES
        },
        "picp_p10_p90": float(np.mean(covered)),
        "empirical_cdf_at_quantile": {
            QUANTILE_LABELS[quantile]: float(
                np.mean(references <= corrected_dni[quantile])
            )
            for quantile in QUANTILES
        },
        "mean_interval_width_W_m2": float(np.mean(interval_width)),
        "median_interval_width_W_m2": float(np.median(interval_width)),
        "p10_zero_rate": float(np.mean(corrected_factors[0.10] == 0.0)),
        "p90_cap_rate": float(np.mean(corrected_factors[0.90] >= 1.05)),
        "raw_crossing_rate": float(np.mean(any_crossing)),
        "raw_crossing_count": int(np.sum(any_crossing)),
        "raw_p10_gt_p50_count": int(np.sum(crossing_10_50)),
        "raw_p50_gt_p90_count": int(np.sum(crossing_50_90)),
    }
    return records, metrics


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        raise ValueError("cannot write empty prediction records")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _print_metrics(split_name: str, metrics: dict[str, Any]) -> None:
    pinball = metrics["pinball_loss_W_m2"]
    print(
        f"{split_name:<10} rows={metrics['row_count']} "
        f"P50 MAE={metrics['p50']['mae_W_m2']:.2f} "
        f"RMSE={metrics['p50']['rmse_W_m2']:.2f} | "
        f"OM MAE={metrics['open_meteo']['mae_W_m2']:.2f} "
        f"RMSE={metrics['open_meteo']['rmse_W_m2']:.2f}"
    )
    print(
        f"{'':<10} pinball P10/P50/P90="
        f"{pinball['p10']:.2f}/{pinball['p50']:.2f}/{pinball['p90']:.2f} "
        f"PICP={metrics['picp_p10_p90']:.3f} "
        f"width={metrics['mean_interval_width_W_m2']:.2f} W/m2 "
        f"raw_crossing={metrics['raw_crossing_count']}/{metrics['row_count']}"
    )


def run(args: argparse.Namespace) -> int:
    site = Site(args.site_lat, args.site_lon, args.timezone)
    kma_points = load_kma_points(args.kma_csv, assume_dsr_w_m2=args.assume_dsr_w_m2)
    assert_training_data_excludes_final_test(kma_points)
    open_meteo_points = load_open_meteo_points(args.open_meteo_csv)
    result = build_model_dataset(
        kma_points,
        open_meteo_points,
        site,
        horizon_minutes=args.horizon_minutes,
        dsr_unit_assumed=args.assume_dsr_w_m2,
        min_solar_elevation_deg=args.min_solar_elevation,
    )
    if not result.rows:
        raise ValueError("preprocessing produced no valid rows")
    validate_no_leakage(result.rows)
    splits = split_development_rows(result.rows)

    train_x, train_y = _arrays(splits.train)
    models: dict[float, LinearQuantileRegressor] = {}
    fits = {}
    model_bounds: dict[float, tuple[tuple[float, float], ...]] = {
        quantile: DEFAULT_WEIGHT_BOUNDS for quantile in QUANTILES
    }
    if args.p90_intercept_limit is not None:
        model_bounds[0.90] = (
            (-args.p90_intercept_limit, args.p90_intercept_limit),
            *DEFAULT_WEIGHT_BOUNDS[1:],
        )
    for quantile in QUANTILES:
        model = LinearQuantileRegressor(quantile, bounds=model_bounds[quantile])
        fits[quantile] = model.fit(train_x, train_y)
        models[quantile] = model

    validation_predictions, validation_metrics = _evaluate(models, splits.validation)
    diagnostic_predictions: list[dict[str, Any]] = []
    diagnostic_metrics: dict[str, Any] | None = None
    if not args.validation_only:
        diagnostic_predictions, diagnostic_metrics = _evaluate(models, splits.diagnostic)

    output_dir: Path = args.output_dir
    dataset_name = "10min_dataset.csv" if args.horizon_minutes == 10 else "dataset.csv"
    write_dataset_csv(result.rows, output_dir / dataset_name)
    _write_csv(output_dir / "validation_quantile_predictions.csv", validation_predictions)
    if diagnostic_predictions:
        _write_csv(output_dir / "diagnostic_quantile_predictions.csv", diagnostic_predictions)

    summary: dict[str, Any] = {
        "model_type": "linear_quantile_regression_p10_p50_p90",
        "optimizer": "L-BFGS-B",
        "reference_type": "dsr_proxy",
        "cloud_patch_used": False,
        "horizon_minutes": args.horizon_minutes,
        "feature_names": list(FEATURE_NAMES),
        "weights": {
            QUANTILE_LABELS[quantile]: [float(value) for value in fits[quantile].weights]
            for quantile in QUANTILES
        },
        "weight_bounds": {
            QUANTILE_LABELS[quantile]: [list(bound) for bound in model_bounds[quantile]]
            for quantile in QUANTILES
        },
        "training_pinball_loss_factor": {
            QUANTILE_LABELS[quantile]: fits[quantile].loss for quantile in QUANTILES
        },
        "optimizer_iterations": {
            QUANTILE_LABELS[quantile]: fits[quantile].iterations for quantile in QUANTILES
        },
        "source_kma_rows": result.source_kma_rows,
        "valid_model_rows": len(result.rows),
        "excluded_rows": result.excluded,
        "dsr_unit_assumed_W_m2": result.dsr_unit_assumed,
        "split_boundaries_utc": splits.boundaries,
        "split_counts": {
            "train": len(splits.train),
            "validation": len(splits.validation),
            "diagnostic": len(splits.diagnostic),
        },
        "selection_policy": "validation only; diagnostic is report-only",
        "quantile_postprocessing": {
            "p10": "clip(raw_p10, 0, 1.05)",
            "p50": "max(p10, clip(raw_p50, 0, 1.05))",
            "p90": "max(p50, clip(raw_p90, 0, 1.05))",
        },
        "validation_metrics": validation_metrics,
        "diagnostic_metrics_report_only": diagnostic_metrics,
        "validation_only_run": args.validation_only,
        "leakage_checks_passed": True,
        "final_test_used": False,
    }
    _write_json(output_dir / "quantile_model_summary.json", summary)

    print(f"=== {args.horizon_minutes}-MINUTE QUANTILE MODEL DATA ===")
    print(f"KMA source rows       : {result.source_kma_rows}")
    print(f"Valid model rows      : {len(result.rows)}")
    print(f"Excluded rows         : {result.excluded}")
    print(
        "Split counts          : "
        f"train={len(splits.train)} validation={len(splits.validation)} "
        f"diagnostic={len(splits.diagnostic)}"
    )
    print("Leakage checks        : PASS")
    print("Final test used       : NO")

    print("\n=== P10/P50/P90 WEIGHTS ===")
    for quantile in QUANTILES:
        print(f"{QUANTILE_LABELS[quantile].upper()}:")
        for name, weight in zip(FEATURE_NAMES, fits[quantile].weights, strict=True):
            print(f"  {name:<23}: {weight: .8f}")
        print(
            f"  train pinball factor  : {fits[quantile].loss:.6f} "
            f"iterations={fits[quantile].iterations}"
        )

    print("\n=== VALIDATION (MODEL SELECTION) ===")
    _print_metrics("validation", validation_metrics)
    if diagnostic_metrics is not None:
        print("\n=== DIAGNOSTIC (REPORT ONLY; DO NOT TUNE) ===")
        _print_metrics("diagnostic", diagnostic_metrics)
    else:
        print("\n=== DIAGNOSTIC ===")
        print("SKIPPED (validation-only model selection run)")
    print(f"\nArtifacts: {output_dir.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kma-csv", type=Path, required=True)
    parser.add_argument("--open-meteo-csv", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/fr01/model/quantile_stage4")
    )
    parser.add_argument(
        "--horizon-minutes",
        type=int,
        choices=ALLOWED_HORIZON_MINUTES,
        default=10,
    )
    parser.add_argument("--site-lat", type=float, default=37.5714)
    parser.add_argument("--site-lon", type=float, default=126.9658)
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--min-solar-elevation", type=float, default=5.0)
    parser.add_argument(
        "--p90-intercept-limit",
        type=float,
        choices=(0.5, 0.75),
        default=None,
        help="Validation experiment override for the symmetric P90 intercept bound.",
    )
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="Skip diagnostic evaluation so model selection uses validation only.",
    )
    parser.add_argument(
        "--assume-dsr-w-m2",
        action="store_true",
        help="Treat legacy CSV DSR values as confirmed W/m2.",
    )
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
