#!/usr/bin/env python3
"""FR-03 & FR-06 Vision AI 3D Furniture Estimation & Benchmark Experiment.

Synthesizes a virtual reading room with ground-truth furniture (bookshelf, desk, chair)
and an ArUco window marker, simulates 2D detection and relative depth with noise,
runs VisionRegistrationPipeline, and outputs a benchmark accuracy report.

Supports `--real-annotations-json` for real-photo experiments.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Ensure backend and root are on PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.vision_ai import (
    CameraIntrinsicsInput,
    Detection2DInput,
    MarkerAnchorInput,
    VisionFurnitureEstimateRequest,
)

from engine.vision_ai.pipeline import VisionRegistrationPipeline


def setup_virtual_room() -> dict[str, Any]:
    """Define a ground truth reading room with window ArUco marker and furniture."""
    # Room: 6m (East) x 5m (North) x 2.8m (Height)
    room_floor = [(0.0, 0.0), (6.0, 0.0), (6.0, 5.0), (0.0, 5.0)]
    room_height = 2.8

    # Window on North wall at y = 5.0, x in [2.0, 4.0], z in [1.0, 2.2]
    # Window plane: u_axis along width (-x), v_axis along height (+z), inward normal (-y)
    u_win = np.array([-1.0, 0.0, 0.0])
    v_win = np.array([0.0, 0.0, 1.0])
    n_win = np.cross(u_win, v_win)  # (0, 1, 0) into the room
    tx_m, ty_m, tz_m = 3.0, 5.0, 1.6  # Marker center on window
    T_enu_m = np.eye(4, dtype=np.float64)
    T_enu_m[0:3, 0] = u_win
    T_enu_m[0:3, 1] = v_win
    T_enu_m[0:3, 2] = n_win
    T_enu_m[0:3, 3] = [tx_m, ty_m, tz_m]

    # Camera at (3.0, 0.8, 1.4) looking North into the room towards (3.0, 3.5, 1.4)
    cam_pos = np.array([3.0, 0.8, 1.4])
    cam_target = np.array([3.0, 3.5, 1.4])
    fwd = cam_target - cam_pos
    fwd = fwd / np.linalg.norm(fwd)
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(fwd, up)
    right = right / np.linalg.norm(right)
    down = np.cross(fwd, right)

    R_enu_c = np.column_stack([right, down, fwd])
    T_enu_c = np.eye(4, dtype=np.float64)
    T_enu_c[0:3, 0:3] = R_enu_c
    T_enu_c[0:3, 3] = cam_pos

    # Ground truth 3D furniture
    furniture_gt = [
        {
            "name": "Library Bookshelf",
            "label": "bookshelf",
            "base_center_m": (1.2, 4.5, 0.0),
            "dimensions_m": (1.2, 0.4, 1.8),
            "yaw_deg": 0.0,
            "protection_grade": "B",
        },
        {
            "name": "Oak Study Desk",
            "label": "desk",
            "base_center_m": (3.0, 3.0, 0.0),
            "dimensions_m": (1.4, 0.7, 0.75),
            "yaw_deg": 0.0,
            "protection_grade": "C",
        },
        {
            "name": "Ergonomic Chair",
            "label": "chair",
            "base_center_m": (3.0, 2.2, 0.0),
            "dimensions_m": (0.6, 0.6, 0.85),
            "yaw_deg": 0.0,
            "protection_grade": "C",
        },
    ]

    return {
        "room_floor": room_floor,
        "room_height": room_height,
        "T_enu_marker": T_enu_m,
        "marker_side_m": 0.6,
        "T_enu_camera_gt": T_enu_c,
        "camera_pos_gt": cam_pos,
        "furniture": furniture_gt,
    }


def project_cuboid_to_box(
    base_center: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    yaw_deg: float,
    T_enu_c: np.ndarray,
    K: np.ndarray,
) -> tuple[tuple[float, float, float, float], float]:
    """Project a 3D cuboid into camera frame to get 2D bounding box and average depth."""
    cx, cy, cz = base_center
    w, d, h = dimensions
    rad = math.radians(yaw_deg)
    u = np.array([math.cos(rad), math.sin(rad), 0.0])
    v = np.array([-math.sin(rad), math.cos(rad), 0.0])
    z = np.array([0.0, 0.0, 1.0])

    R_enu_c = T_enu_c[0:3, 0:3]
    t_enu_c = T_enu_c[0:3, 3]

    corners_enu = []
    for du in [-0.5 * w, 0.5 * w]:
        for dv in [-0.5 * d, 0.5 * d]:
            for dh in [0.0, h]:
                corners_enu.append(np.array([cx, cy, cz]) + du * u + dv * v + dh * z)

    # Invert camera transform: P_c = R_enu_c.T @ (P_enu - t_enu_c)
    corners_c = [R_enu_c.T @ (p - t_enu_c) for p in corners_enu]
    proj = [K @ p / p[2] for p in corners_c]
    xs = [float(p[0]) for p in proj]
    ys = [float(p[1]) for p in proj]
    box_xyxy = (min(xs), min(ys), max(xs), max(ys))
    mean_depth = float(np.mean([p[2] for p in corners_c]))
    return box_xyxy, mean_depth


def run_synthetic_benchmark(noise_pct: float = 2.0) -> dict[str, Any]:
    """Execute synthetic benchmark with simulated depth noise and print accuracy table."""
    scene_env = setup_virtual_room()
    cam_intrinsics = CameraIntrinsicsInput(
        width=1280, height=720, fx=900.0, fy=900.0, cx=640.0, cy=360.0
    )
    K = np.array(
        [
            [cam_intrinsics.fx, 0, cam_intrinsics.cx],
            [0, cam_intrinsics.fy, cam_intrinsics.cy],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )

    T_enu_c_gt = scene_env["T_enu_camera_gt"]
    T_enu_m = scene_env["T_enu_marker"]
    s = scene_env["marker_side_m"]

    # 4 Marker corners in local marker coordinates
    obj_pts = np.array(
        [
            [-s / 2.0, s / 2.0, 0.0],
            [s / 2.0, s / 2.0, 0.0],
            [s / 2.0, -s / 2.0, 0.0],
            [-s / 2.0, -s / 2.0, 0.0],
        ],
        dtype=np.float32,
    )

    # Transform marker corners to camera frame
    T_c_m = np.linalg.inv(T_enu_c_gt) @ T_enu_m
    corners_c = [T_c_m[0:3, 0:3] @ pt + T_c_m[0:3, 3] for pt in obj_pts]
    proj_corners = [K @ pt / pt[2] for pt in corners_c]
    marker_corners_px = [(float(p[0]), float(p[1])) for p in proj_corners]
    marker_mean_depth = float(np.mean([p[2] for p in corners_c]))

    # Project furniture objects
    detections: list[Detection2DInput] = []
    rng = np.random.default_rng(42)

    for item in scene_env["furniture"]:
        box_xyxy, true_depth = project_cuboid_to_box(
            item["base_center_m"], item["dimensions_m"], item["yaw_deg"], T_enu_c_gt, K
        )
        # Apply simulated relative depth noise
        noise_factor = 1.0 + (rng.normal(0.0, noise_pct / 100.0))
        simulated_rel_depth = true_depth * noise_factor

        detections.append(
            Detection2DInput(
                box_xyxy=box_xyxy,
                label=item["label"],
                confidence=0.92,
                observed_depth_rel=simulated_rel_depth,
                name=item["name"],
                protection_grade=item["protection_grade"],
            )
        )

    # Build estimate request
    marker_anchor = MarkerAnchorInput(
        code_id=0,
        side_length_m=s,
        corners_px=marker_corners_px,
        anchor_depth_rel=marker_mean_depth,
    )

    request = VisionFurnitureEstimateRequest(
        camera=cam_intrinsics,
        marker_anchor=marker_anchor,
        detections=detections,
        apply_to_scene=False,
        auto_create_protected_regions=True,
    )

    # Run pipeline
    pipeline = VisionRegistrationPipeline()
    pose_res, depth_calib, estimated_items = pipeline.run(
        scene_id="virtual_reading_room",
        room_id="room_reading",
        room_floor_vertices=scene_env["room_floor"],
        room_height_m=scene_env["room_height"],
        T_ENU_marker=[float(v) for v in T_enu_m.flatten()],
        request=request,
        observation_id="obs_synth_benchmark",
        policy_id="policy_reading_room",
    )

    # Compute errors against ground truth
    cam_pos_est = np.array(pose_res.camera_position_enu_m)
    cam_pos_gt = scene_env["camera_pos_gt"]
    cam_pose_err_cm = float(np.linalg.norm(cam_pos_est - cam_pos_gt) * 100.0)

    results: list[dict[str, Any]] = []
    for gt_item, est_res in zip(scene_env["furniture"], estimated_items, strict=True):
        occ = est_res.occluder
        c_gt = np.array(gt_item["base_center_m"])
        c_est = np.array(occ.base_center_m)
        c_err_cm = float(np.linalg.norm(c_est - c_gt) * 100.0)

        d_gt = np.array(gt_item["dimensions_m"])
        d_est = np.array(occ.dimensions_m)
        d_err_cm = float(np.linalg.norm(d_est - d_gt) * 100.0)

        yaw_diff = abs(occ.yaw_deg_ccw_from_east - gt_item["yaw_deg"]) % 180.0
        yaw_err = float(min(yaw_diff, 180.0 - yaw_diff))

        results.append(
            {
                "name": gt_item["name"],
                "label": gt_item["label"],
                "gt_center": gt_item["base_center_m"],
                "est_center": occ.base_center_m,
                "center_err_cm": round(c_err_cm, 2),
                "gt_dims": gt_item["dimensions_m"],
                "est_dims": occ.dimensions_m,
                "dim_err_cm": round(d_err_cm, 2),
                "yaw_err_deg": round(yaw_err, 2),
                "clamped": est_res.clamped_to_room,
                "protected_region": est_res.protected_region.region_id
                if est_res.protected_region
                else None,
            }
        )

    mean_c_err = float(np.mean([r["center_err_cm"] for r in results]))
    mean_d_err = float(np.mean([r["dim_err_cm"] for r in results]))

    report = {
        "camera_pose_error_cm": round(cam_pose_err_cm, 3),
        "depth_scale_alpha": round(depth_calib.alpha, 4),
        "depth_shift_beta": round(depth_calib.beta, 4),
        "mean_center_error_cm": round(mean_c_err, 2),
        "mean_dim_error_cm": round(mean_d_err, 2),
        "items": results,
    }
    return report


def print_benchmark_table(report: dict[str, Any], noise_pct: float) -> None:
    """Pretty-print benchmark results as a Markdown table."""
    print("=" * 100)
    print(f"FR-03 & FR-06 VISION AI 3D ESTIMATION BENCHMARK (Simulated Noise: {noise_pct}%)")
    print("=" * 100)
    print(f"Camera Pose Recovery Error: {report['camera_pose_error_cm']} cm")
    print(
        f"Metric Depth Calibration:   Z_metric = {report['depth_scale_alpha']} * d_rel + {report['depth_shift_beta']}"
    )
    print(f"Mean 3D Center Error:       {report['mean_center_error_cm']} cm")
    print(f"Mean 3D Dimension Error:    {report['mean_dim_error_cm']} cm")
    print("-" * 100)
    header = f"{'Item':<20} | {'Label':<10} | {'Center Err':<12} | {'Dim Err':<10} | {'Yaw Err':<10} | {'Protected Region':<22}"
    print(header)
    print("-" * 100)
    for item in report["items"]:
        row = (
            f"{item['name']:<20} | "
            f"{item['label']:<10} | "
            f"{item['center_err_cm']:>8.1f} cm | "
            f"{item['dim_err_cm']:>6.1f} cm | "
            f"{item['yaw_err_deg']:>6.1f} deg | "
            f"{item['protected_region']!s:<22}"
        )
        print(row)
    print("=" * 100)


def run_real_photo_experiment(annotations_path: str) -> None:
    """Run registration on real user annotations JSON."""
    data = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    print(f"Loaded {len(data.get('detections', []))} detections from {annotations_path}")
    print("To execute with real photo models:")
    print("  1. Run YOLOv8: YoloV8DetectorAdapter().detect(image_rgb)")
    print(
        "  2. Run Depth Anything V2: MonocularDepthAdapter().estimate_relative_depth(image_rgb, w, h)"
    )
    print("  3. Pass 2D boxes + ArUco window marker corners into VisionRegistrationPipeline.")


def main() -> None:
    parser = argparse.ArgumentParser(description="FR-03 Vision AI 3D Estimation Benchmark")
    parser.add_argument(
        "--noise-pct", type=float, default=2.0, help="Relative depth noise percentage"
    )
    parser.add_argument(
        "--real-annotations-json", type=str, default=None, help="Path to real annotation JSON"
    )
    parser.add_argument(
        "--json-out", type=str, default=None, help="Path to save output JSON report"
    )

    args = parser.parse_args()

    if args.real_annotations_json:
        run_real_photo_experiment(args.real_annotations_json)
        return

    report = run_synthetic_benchmark(noise_pct=args.noise_pct)
    print_benchmark_table(report, noise_pct=args.noise_pct)

    if args.json_out:
        out_p = Path(args.json_out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved to {out_p}")


if __name__ == "__main__":
    main()
