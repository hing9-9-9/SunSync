"""Run the explicit one-time FR-01 final evaluation with frozen artifacts only."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.nowcast.kma_final_test import evaluate_final_rows
from app.nowcast.kma_model_artifact import load_model_bundle, sha256_file
from app.nowcast.kma_model_dataset import (
    ALLOWED_HORIZON_MINUTES,
    build_model_dataset,
    load_kma_points,
    load_open_meteo_points,
    validate_no_leakage,
)

from engine.fr01.models import Site


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"expected JSON object: {path}")
    return document


def _verify_readiness(args: argparse.Namespace) -> dict[str, Any]:
    readiness = _read_json(args.readiness_manifest)
    if readiness.get("status") != "ready_for_one_time_evaluation":
        raise ValueError("final-test inputs are not marked ready")
    if readiness.get("performance_evaluated") is not False:
        raise ValueError("readiness manifest says performance was already evaluated")
    if readiness.get("final_evaluation_executed") is not False:
        raise ValueError("readiness manifest says final evaluation was already executed")
    if readiness.get("kma", {}).get("sha256") != sha256_file(args.kma_csv):
        raise ValueError("KMA input changed after readiness validation")
    if readiness.get("open_meteo", {}).get("sha256") != sha256_file(args.open_meteo_csv):
        raise ValueError("Open-Meteo input changed after readiness validation")
    expected_model_hash = readiness.get("model_artifacts", {}).get("manifest_sha256")
    if expected_model_hash != sha256_file(args.model_root / "manifest.json"):
        raise ValueError("model artifacts changed after readiness validation")
    return readiness


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def run(args: argparse.Namespace) -> int:
    if not args.confirm_one_time_final_evaluation:
        raise ValueError(
            "refusing to evaluate: pass --confirm-one-time-final-evaluation only after review"
        )
    if args.output_dir.exists():
        raise ValueError("refusing to overwrite an existing final-evaluation directory")
    readiness = _verify_readiness(args)
    models = load_model_bundle(args.model_root)
    kma = load_kma_points(args.kma_csv, assume_dsr_w_m2=True)
    open_meteo = load_open_meteo_points(args.open_meteo_csv)
    site = Site(args.site_lat, args.site_lon, args.timezone)

    results: dict[str, Any] = {}
    predictions: dict[int, list[dict[str, Any]]] = {}
    for horizon in ALLOWED_HORIZON_MINUTES:
        built = build_model_dataset(
            kma,
            open_meteo,
            site,
            horizon_minutes=horizon,
            dsr_unit_assumed=True,
        )
        validate_no_leakage(built.rows)
        records, metrics = evaluate_final_rows(models[horizon], built.rows)
        predictions[horizon] = records
        results[str(horizon)] = {
            "metrics": metrics,
            "source_kma_rows": built.source_kma_rows,
            "valid_model_rows": len(built.rows),
            "excluded_rows": built.excluded,
        }

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".fr01_final_", dir=args.output_dir.parent) as temporary:
        temporary_dir = Path(temporary)
        for horizon, records in predictions.items():
            _write_csv(temporary_dir / f"horizon_{horizon}_predictions.csv", records)
        result_document = {
            "evaluation_type": "one_time_frozen_final_test",
            "status": "completed_no_retuning_allowed",
            "model_version": "fr01_kma_quantile_v1",
            "period": readiness["period"],
            "reference_type": "dsr_proxy",
            "cloud_patch_used": False,
            "model_changed_after_result": False,
            "inputs": {
                "readiness_manifest_sha256": sha256_file(args.readiness_manifest),
                "kma_sha256": sha256_file(args.kma_csv),
                "open_meteo_sha256": sha256_file(args.open_meteo_csv),
                "model_manifest_sha256": sha256_file(args.model_root / "manifest.json"),
            },
            "horizons": results,
            "usage_rule": (
                "Report this result once. Do not tune or modify frozen V1 from final-test metrics."
            ),
        }
        _write_json(temporary_dir / "final_test_metrics.json", result_document)
        os.replace(temporary_dir, args.output_dir)

    print("=== ONE-TIME FINAL TEST COMPLETE ===")
    for horizon in ALLOWED_HORIZON_MINUTES:
        metrics = results[str(horizon)]["metrics"]
        print(
            f"horizon={horizon} rows={metrics['row_count']} "
            f"P50_MAE={metrics['p50']['mae_W_m2']:.2f} "
            f"OM_MAE={metrics['open_meteo']['mae_W_m2']:.2f}"
        )
    print("MODEL RETUNING FROM THESE RESULTS IS PROHIBITED")
    print(f"output={args.output_dir.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    final_root = Path(
        "data/fr01/archive/frozen_final_test/final_test_20261102_20261105"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kma-csv", type=Path, default=final_root / "kma_gk2a.csv")
    parser.add_argument(
        "--open-meteo-csv", type=Path, default=final_root / "open_meteo_runs.csv"
    )
    parser.add_argument(
        "--readiness-manifest",
        type=Path,
        default=final_root / "input_readiness_manifest.json",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/fr01/model/final_test_20261102_20261105"),
    )
    parser.add_argument("--site-lat", type=float, default=37.5714)
    parser.add_argument("--site-lon", type=float, default=126.9658)
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--confirm-one-time-final-evaluation", action="store_true")
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
