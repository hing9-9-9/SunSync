"""Validate and freeze FR-01 final-test inputs without calculating performance."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.nowcast.kma_final_test import validate_final_test_inputs


def _write_deterministic(path: Path, document: dict) -> str:
    payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError("existing readiness manifest differs; frozen file will not be overwritten")
        return "verified_existing"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return "created"


def run(args: argparse.Namespace) -> int:
    document = validate_final_test_inputs(
        root=args.final_root,
        kma_csv=args.kma_csv,
        kma_manifest=args.kma_manifest,
        open_meteo_csv=args.open_meteo_csv,
        open_meteo_manifest=args.open_meteo_manifest,
        model_root=args.model_root,
    )
    status = _write_deterministic(args.readiness_output, document)
    print("=== FINAL TEST INPUT READINESS ===")
    print(f"status={document['status']}")
    print(f"manifest_action={status}")
    print(f"kma_rows={document['kma']['row_count']}")
    print(f"open_meteo_runs={document['open_meteo']['run_count']}")
    print(f"open_meteo_rows={document['open_meteo']['row_count']}")
    print(f"temporal_coverage={document['temporal_coverage_origins']}")
    print("performance_evaluated=NO")
    print("final_evaluation_executed=NO")
    print(f"readiness_manifest={args.readiness_output.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    final_root = Path(
        "data/fr01/archive/frozen_final_test/final_test_20261102_20261105"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final-root", type=Path, default=final_root)
    parser.add_argument("--kma-csv", type=Path, default=final_root / "kma_gk2a.csv")
    parser.add_argument(
        "--kma-manifest", type=Path, default=final_root / "dataset_manifest.json"
    )
    parser.add_argument(
        "--open-meteo-csv", type=Path, default=final_root / "open_meteo_runs.csv"
    )
    parser.add_argument(
        "--open-meteo-manifest",
        type=Path,
        default=final_root / "open_meteo_collection.json",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1"),
    )
    parser.add_argument(
        "--readiness-output",
        type=Path,
        default=final_root / "input_readiness_manifest.json",
    )
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
