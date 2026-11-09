"""Build and evaluate the first leakage-safe FR-01 10-minute P50 model."""

from __future__ import annotations

import argparse
import csv
import json
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

from app.nowcast.kma_linear_model import LinearQuantileRegressor
from app.nowcast.kma_model_dataset import (
    FEATURE_NAMES,
    ModelDatasetRow,
    assert_training_data_excludes_final_test,
    build_10min_dataset,
    load_kma_points,
    load_open_meteo_points,
    split_development_rows,
    validate_no_leakage,
    write_dataset_csv,
)

from engine.fr01.models import Site


def _arrays(rows: list[ModelDatasetRow] | tuple[ModelDatasetRow, ...]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([row.features() for row in rows], dtype=np.float64)
    y = np.asarray([row.reference_dni_factor for row in rows], dtype=np.float64)
    return x, y


def _metrics(reference: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    error = prediction - reference
    return {
        "mae_W_m2": float(np.mean(np.abs(error))),
        "rmse_W_m2": float(np.sqrt(np.mean(error**2))),
    }


def _evaluate(
    model: LinearQuantileRegressor, rows: list[ModelDatasetRow]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    x, _ = _arrays(rows)
    factors = model.predict_factors(x)
    references = np.asarray([row.reference_dni_W_m2 for row in rows], dtype=np.float64)
    baselines = np.asarray([row.open_meteo_dni_W_m2 for row in rows], dtype=np.float64)
    clear_sky = np.asarray([row.clear_sky_dni_target_W_m2 for row in rows], dtype=np.float64)
    predictions = factors * clear_sky
    records: list[dict[str, Any]] = []
    for row, factor, prediction in zip(rows, factors, predictions, strict=True):
        records.append(
            {
                "prediction_origin_utc": row.prediction_origin_utc,
                "valid_time_utc": row.valid_time_utc,
                "horizon_minutes": row.horizon_minutes,
                "reference_dni_W_m2": round(row.reference_dni_W_m2, 6),
                "open_meteo_dni_W_m2": round(row.open_meteo_dni_W_m2, 6),
                "p50_factor": round(float(factor), 9),
                "p50_dni_W_m2": round(float(prediction), 6),
            }
        )
    return records, {
        "p50": _metrics(references, predictions),
        "open_meteo": _metrics(references, baselines),
    }


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
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


def _print_samples(rows: tuple[ModelDatasetRow, ...], count: int = 10) -> None:
    if not rows:
        return
    indexes = np.linspace(0, len(rows) - 1, min(count, len(rows)), dtype=int)
    print("\n=== PREPROCESSED SAMPLE ROWS ===")
    print("origin              valid               dsr_in cloud  om_dni  y_dni  om_available")
    for index in indexes:
        row = rows[int(index)]
        print(
            f"{row.prediction_origin_utc:<20} "
            f"{row.valid_time_utc:<20} "
            f"{row.dsr_input_W_m2:>6.1f} "
            f"{row.cloud_cover_input_pct:>5.1f} "
            f"{row.open_meteo_dni_W_m2:>7.1f} "
            f"{row.reference_dni_W_m2:>6.1f} "
            f"{row.open_meteo_snapshot_available_at_utc}"
        )


def run(args: argparse.Namespace) -> int:
    site = Site(args.site_lat, args.site_lon, args.timezone)
    kma_points = load_kma_points(args.kma_csv, assume_dsr_w_m2=args.assume_dsr_w_m2)
    assert_training_data_excludes_final_test(kma_points)
    open_meteo_points = load_open_meteo_points(args.open_meteo_csv)
    result = build_10min_dataset(
        kma_points,
        open_meteo_points,
        site,
        dsr_unit_assumed=args.assume_dsr_w_m2,
        min_solar_elevation_deg=args.min_solar_elevation,
    )
    if not result.rows:
        raise ValueError("preprocessing produced no valid rows")
    validate_no_leakage(result.rows)

    output_dir: Path = args.output_dir
    dataset_path = output_dir / "10min_dataset.csv"
    summary_path = output_dir / "10min_dataset_summary.json"
    write_dataset_csv(result.rows, dataset_path)

    splits = split_development_rows(result.rows)
    train_rows = splits.train
    validation_rows = splits.validation
    diagnostic_rows = splits.diagnostic
    train_x, train_y = _arrays(train_rows)
    model = LinearQuantileRegressor(quantile=0.5)
    fit = model.fit(train_x, train_y)

    validation_predictions, validation_metrics = _evaluate(model, validation_rows)
    diagnostic_predictions, diagnostic_metrics = _evaluate(model, diagnostic_rows)
    _write_csv(output_dir / "validation_predictions.csv", validation_predictions)
    _write_csv(output_dir / "diagnostic_predictions.csv", diagnostic_predictions)

    summary: dict[str, Any] = {
        "model_type": "linear_quantile_regression_p50",
        "reference_type": "dsr_proxy",
        "cloud_patch_used": False,
        "horizon_minutes": 10,
        "feature_names": list(FEATURE_NAMES),
        "weights": [float(value) for value in fit.weights],
        "training_pinball_loss": fit.loss,
        "optimizer_iterations": fit.iterations,
        "source_kma_rows": result.source_kma_rows,
        "valid_model_rows": len(result.rows),
        "excluded_rows": result.excluded,
        "dsr_unit_assumed_W_m2": result.dsr_unit_assumed,
        "split_boundaries_utc": splits.boundaries,
        "split_counts": {
            "train": len(train_rows),
            "validation": len(validation_rows),
            "diagnostic": len(diagnostic_rows),
        },
        "validation_metrics": validation_metrics,
        "diagnostic_metrics": diagnostic_metrics,
        "leakage_checks_passed": True,
    }
    _write_json(summary_path, summary)

    print("=== DATA PREPROCESSING ===")
    print(f"KMA source rows       : {result.source_kma_rows}")
    print(f"Valid 10-minute rows  : {len(result.rows)}")
    print(f"Excluded rows         : {result.excluded}")
    print(f"DSR W/m2 assumed      : {result.dsr_unit_assumed}")
    print("Leakage checks        : PASS")
    print(
        "Split counts          : "
        f"train={len(train_rows)} validation={len(validation_rows)} "
        f"diagnostic={len(diagnostic_rows)}"
    )
    _print_samples(result.rows)

    print("\n=== P50 LINEAR MODEL ===")
    for name, weight in zip(FEATURE_NAMES, fit.weights, strict=True):
        print(f"{name:<25}: {weight: .8f}")
    print(f"training pinball loss : {fit.loss:.6f}")

    print("\n=== SAME-ROW EVALUATION ===")
    for split_name, metrics in (
        ("validation", validation_metrics),
        ("diagnostic", diagnostic_metrics),
    ):
        print(
            f"{split_name:<10} "
            f"rows={len(validation_rows) if split_name == 'validation' else len(diagnostic_rows)} "
            f"P50 MAE={metrics['p50']['mae_W_m2']:.2f} RMSE={metrics['p50']['rmse_W_m2']:.2f} | "
            f"OM MAE={metrics['open_meteo']['mae_W_m2']:.2f} "
            f"RMSE={metrics['open_meteo']['rmse_W_m2']:.2f}"
        )
    print(f"\nArtifacts: {output_dir.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kma-csv", type=Path, required=True)
    parser.add_argument("--open-meteo-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/fr01/p50_first"))
    parser.add_argument("--site-lat", type=float, default=37.5714)
    parser.add_argument("--site-lon", type=float, default=126.9658)
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--min-solar-elevation", type=float, default=5.0)
    parser.add_argument(
        "--assume-dsr-w-m2",
        action="store_true",
        help="Explicitly treat provider DSR values as W/m2 when source metadata is unconfirmed.",
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
