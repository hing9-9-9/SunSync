"""Field-measurement comparison tool tests using the documented dry-run example."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ai_experiments" / "compare_fr03_field_measurements.py"
spec = importlib.util.spec_from_file_location("fr03_field_measurements", SCRIPT)
assert spec is not None and spec.loader is not None
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def test_documented_template_and_dry_run_json_produce_separate_accuracy_metrics(tmp_path: Path):
    template = ROOT / "docs" / "examples" / "fr03_field_measurements_template.csv"
    example_response = ROOT / "docs" / "examples" / "fr03_dry_run_response_example.json"
    csv_path = tmp_path / "measurements.csv"
    response_path = tmp_path / "fr03_dry_run_response_example.json"
    csv_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    response_path.write_text(example_response.read_text(encoding="utf-8"), encoding="utf-8")

    rows = comparison.compare_measurements(csv_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["three_d_estimation_success"] is True
    assert row["center_error_cm"] == 10.0
    assert row["width_error_cm"] == 10.0
    assert row["yaw_absolute_error_deg"] == 2.0
    assert row["final_distance_error_cm"] == 10.0
    assert row["aruco_reprojection_error_mean_px"] == 0.4
    assert row["depth_calibration_residual_rmse_m"] == 0.08

    output_csv = tmp_path / "comparison.csv"
    comparison.write_results(rows, output_csv)
    summary = comparison.build_summary(rows)
    assert summary["mean_center_error_cm"] == 10.0
    assert summary["mean_yaw_absolute_error_deg"] == 2.0
    assert "aruco_reprojection_error_mean_px" in output_csv.read_text(encoding="utf-8")
    json.dumps(summary)


def test_missing_photo_file_does_not_block_a_json_based_comparison(tmp_path: Path):
    response = {
        "estimated_items": [
            {
                "occluder": {
                    "type": "chair",
                    "base_center_m": [1.0, 1.0, 0.0],
                    "dimensions_m": [0.5, 0.5, 0.8],
                    "yaw_deg_ccw_from_east": 0.0,
                },
                "calibrated_distance_m": 1.0,
                "clamped_to_room": False,
            }
        ]
    }
    (tmp_path / "response.json").write_text(json.dumps(response), encoding="utf-8")
    (tmp_path / "measurements.csv").write_text(
        "experiment_id,photo_id,furniture_id,api_response_json,item_index,ground_truth_label,"
        "ground_truth_center_x_m,ground_truth_center_y_m,ground_truth_center_z_m,"
        "ground_truth_width_m,ground_truth_depth_m,ground_truth_height_m,ground_truth_yaw_deg\n"
        "e-1,photo-deleted-but-hashed,chair-1,response.json,0,chair,1,1,0,0.5,0.5,0.8,0\n",
        encoding="utf-8",
    )
    rows = comparison.compare_measurements(tmp_path / "measurements.csv")
    assert rows[0]["three_d_estimation_success"] is True
    assert rows[0]["photo_id"] == "photo-deleted-but-hashed"
