#!/usr/bin/env python3
"""Compare FR-03 field measurements with Vision AI dry-run API responses.

Example:
    python server/tools/ai_experiments/compare_fr03_field_measurements.py \
      --measurements-csv docs/examples/fr03_field_measurements_template.csv \
      --output-csv docs/reports/fr03_field_comparison.csv \
      --summary-json docs/reports/fr03_field_comparison_summary.json

One CSV row represents one measured furniture item.  ``api_response_json``
points to the matching API dry-run response and ``item_index`` selects its
``estimated_items`` entry.  Image pixels are never read, so a durable
``photo_id`` is sufficient when the photograph cannot be retained.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

OUTPUT_FIELDS = [
    "experiment_id",
    "photo_id",
    "furniture_id",
    "ground_truth_label",
    "estimated_label",
    "api_response_json",
    "item_index",
    "detection_success",
    "three_d_estimation_success",
    "clamped_to_room",
    "center_error_cm",
    "width_error_cm",
    "width_error_pct",
    "depth_error_cm",
    "depth_error_pct",
    "height_error_cm",
    "height_error_pct",
    "yaw_absolute_error_deg",
    "final_distance_error_cm",
    "aruco_reprojection_error_mean_px",
    "aruco_reprojection_error_max_px",
    "depth_calibration_residual_rmse_m",
    "detection_source",
    "depth_source",
    "estimation_quality",
    "capture_distance_m",
    "camera_angle_deg",
    "lighting_lux",
    "occlusion_notes",
    "yolo_weights_id",
    "depth_model_revision",
    "error",
]


def _float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def _metric_error_cm(estimated: float, actual: float) -> tuple[float, float]:
    error_cm = abs(estimated - actual) * 100.0
    error_pct = error_cm / (abs(actual) * 100.0) * 100.0 if actual else math.nan
    return round(error_cm, 3), round(error_pct, 3)


def _yaw_error_deg(estimated: float, actual: float) -> float:
    """Cuboids are equivalent after a 180-degree turn."""
    delta = abs((estimated - actual) % 180.0)
    return round(min(delta, 180.0 - delta), 3)


def _resolve_response_path(csv_path: Path, response_path: str) -> Path:
    candidate = Path(response_path)
    return candidate if candidate.is_absolute() else csv_path.parent / candidate


def _base_output(row: dict[str, str]) -> dict[str, Any]:
    return {
        "experiment_id": row.get("experiment_id", ""),
        "photo_id": row.get("photo_id", ""),
        "furniture_id": row.get("furniture_id", ""),
        "ground_truth_label": row.get("ground_truth_label", ""),
        "api_response_json": row.get("api_response_json", ""),
        "item_index": row.get("item_index", "0"),
        "capture_distance_m": row.get("capture_distance_m", ""),
        "camera_angle_deg": row.get("camera_angle_deg", ""),
        "lighting_lux": row.get("lighting_lux", ""),
        "occlusion_notes": row.get("occlusion_notes", ""),
        "yolo_weights_id": row.get("yolo_weights_id", ""),
        "depth_model_revision": row.get("depth_model_revision", ""),
    }


def compare_measurements(csv_path: Path) -> list[dict[str, Any]]:
    """Return one normalized comparison result for every measurement CSV row."""
    results: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            output = _base_output(row)
            try:
                response_path = _resolve_response_path(csv_path, row["api_response_json"])
                response = json.loads(response_path.read_text(encoding="utf-8"))
                item_index = int(row.get("item_index") or 0)
                items = response.get("estimated_items", [])
                item = items[item_index]
                occluder = item["occluder"]
                dimensions = occluder["dimensions_m"]
                center = occluder["base_center_m"]
                diagnostics = response.get("diagnostics") or {}
                aruco = diagnostics.get("aruco") or {}
                depth = diagnostics.get("depth") or {}

                gt_center = [_float(row.get(f"ground_truth_center_{axis}_m")) for axis in "xyz"]
                gt_dimensions = [
                    _float(row.get("ground_truth_width_m")),
                    _float(row.get("ground_truth_depth_m")),
                    _float(row.get("ground_truth_height_m")),
                ]
                if any(value is None for value in [*gt_center, *gt_dimensions]):
                    raise ValueError("Ground-truth center and dimensions are required.")

                center_error_cm = math.dist(center, gt_center) * 100.0
                width_cm, width_pct = _metric_error_cm(dimensions[0], gt_dimensions[0])
                depth_cm, depth_pct = _metric_error_cm(dimensions[1], gt_dimensions[1])
                height_cm, height_pct = _metric_error_cm(dimensions[2], gt_dimensions[2])
                truth_distance = _float(row.get("ground_truth_camera_distance_m"))

                output.update(
                    {
                        "estimated_label": occluder.get("type", ""),
                        "detection_success": True,
                        "three_d_estimation_success": True,
                        "clamped_to_room": item.get("clamped_to_room", False),
                        "center_error_cm": round(center_error_cm, 3),
                        "width_error_cm": width_cm,
                        "width_error_pct": width_pct,
                        "depth_error_cm": depth_cm,
                        "depth_error_pct": depth_pct,
                        "height_error_cm": height_cm,
                        "height_error_pct": height_pct,
                        "yaw_absolute_error_deg": _yaw_error_deg(
                            float(occluder["yaw_deg_ccw_from_east"]),
                            float(row["ground_truth_yaw_deg"]),
                        ),
                        "final_distance_error_cm": (
                            round(
                                abs(float(item["calibrated_distance_m"]) - truth_distance) * 100.0,
                                3,
                            )
                            if truth_distance is not None
                            else ""
                        ),
                        "aruco_reprojection_error_mean_px": aruco.get(
                            "reprojection_error_mean_px", ""
                        ),
                        "aruco_reprojection_error_max_px": aruco.get(
                            "reprojection_error_max_px", ""
                        ),
                        "depth_calibration_residual_rmse_m": depth.get("residual_rmse_m", ""),
                        "detection_source": item.get("detection_source", ""),
                        "depth_source": (item.get("diagnostics") or {}).get("depth_source", ""),
                        "estimation_quality": item.get("estimation_quality", ""),
                        "error": "",
                    }
                )
            except (KeyError, IndexError, OSError, ValueError, json.JSONDecodeError) as error:
                output.update(
                    {
                        "detection_success": False,
                        "three_d_estimation_success": False,
                        "error": str(error),
                    }
                )
            results.append(output)
    return results


def write_results(rows: list[dict[str, Any]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row.get("three_d_estimation_success") is True]

    def mean(field: str) -> float | None:
        values = [float(row[field]) for row in successful if row.get(field) not in ("", None)]
        return round(sum(values) / len(values), 3) if values else None

    return {
        "records": len(rows),
        "three_d_successes": len(successful),
        "three_d_success_rate_pct": round(100.0 * len(successful) / len(rows), 3) if rows else 0.0,
        "mean_center_error_cm": mean("center_error_cm"),
        "mean_width_error_cm": mean("width_error_cm"),
        "mean_depth_error_cm": mean("depth_error_cm"),
        "mean_height_error_cm": mean("height_error_cm"),
        "mean_yaw_absolute_error_deg": mean("yaw_absolute_error_deg"),
        "mean_final_distance_error_cm": mean("final_distance_error_cm"),
        "mean_aruco_reprojection_error_mean_px": mean("aruco_reprojection_error_mean_px"),
        "mean_depth_calibration_residual_rmse_m": mean("depth_calibration_residual_rmse_m"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements-csv", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    args = parser.parse_args()

    rows = compare_measurements(args.measurements_csv)
    write_results(rows, args.output_csv)
    summary = build_summary(rows)
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
