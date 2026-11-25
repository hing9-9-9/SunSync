#!/usr/bin/env python3
"""Local CLI visualizer for the FR-03 Vision AI 3D furniture pipeline.

Runs the real SunSync Vision AI stack (ArUco solvePnP + YOLOv8 + Depth Anything V2 +
metric depth calibration + 3D cuboid back-projection) on a single indoor photo and
exports stage-by-stage PNG visualizations plus a structured JSON summary.

Example:
    PYTHONPATH=server/backend:server .venv/bin/python \
      server/tools/ai_experiments/visualize_fr03_pipeline.py \
      --image /path/to/photo.jpg \
      --out-dir /tmp/vision_viz \
      --marker-side-m 0.14
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "server" / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "server" / "backend"))
if str(PROJECT_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "server"))

from app.schemas.vision_ai import (
    CameraIntrinsicsInput,
    MarkerAnchorInput,
    VisionFurnitureEstimateRequest,
)

from engine.vision_ai.pipeline import VisionRegistrationPipeline
from engine.vision_ai.protocols import (
    DEFAULT_METRIC_DEPTH_MODEL,
    CompositeDepthEstimator,
    CompositeFurnitureDetector,
    DetectedObject2D,
    MonocularDepthAdapter,
    YoloV8DetectorAdapter,
    _overlaps_any_box,
)

# BGR colors for OpenCV drawing (high contrast, colorblind-friendly palette)
LABEL_COLORS_BGR: dict[str, tuple[int, int, int]] = {
    "bookshelf": (175, 118, 22),  # Deep blue
    "desk": (36, 138, 214),  # Warm amber/orange
    "chair": (110, 158, 32),  # Teal/green
    "other": (165, 92, 132),  # Muted plum
}
QUALITY_COLORS_BGR: dict[str, tuple[int, int, int]] = {
    "high": (72, 168, 60),
    "medium": (28, 152, 224),
    "low_needs_aruco": (56, 72, 220),
}


def detect_any_aruco_marker(
    image_rgb: np.ndarray, target_code_id: int | None
) -> tuple[int, list[tuple[float, float]]]:
    """Detect DICT_4X4_50 ArUco marker (target ID or first detected marker)."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(image_rgb)
    if ids is None or len(ids) == 0:
        raise ValueError("No DICT_4X4_50 ArUco marker detected in the image.")
    flat_ids = [int(x) for x in ids.flatten()]
    if target_code_id is not None:
        for idx, cid in enumerate(flat_ids):
            if cid == target_code_id:
                return cid, [(float(pt[0]), float(pt[1])) for pt in corners[idx][0]]
        raise ValueError(f"ArUco code_id={target_code_id} not found (detected ids: {flat_ids}).")
    return flat_ids[0], [(float(pt[0]), float(pt[1])) for pt in corners[0][0]]


def build_default_window_marker_transform(
    room_width: float, room_depth: float, marker_height_m: float
) -> list[float]:
    """Place window marker on the North wall (y = room_depth) facing South (-y) into the room."""
    u_win = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    v_win = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    n_win = np.cross(u_win, v_win)  # (0, -1, 0) inward into the room
    t_enu_m = np.eye(4, dtype=np.float64)
    t_enu_m[0:3, 0] = u_win
    t_enu_m[0:3, 1] = v_win
    t_enu_m[0:3, 2] = n_win
    t_enu_m[0:3, 3] = [room_width / 2.0, room_depth, marker_height_m]
    return [float(v) for v in t_enu_m.flatten()]


def draw_header_bar(
    img_bgr: np.ndarray, title: str, subtitle: str = "", bar_height: int = 58
) -> np.ndarray:
    """Prepend a crisp dark header banner above an image without occluding pixels."""
    _h, w = img_bgr.shape[:2]
    banner = np.full((bar_height, w, 3), (24, 30, 42), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    title_scale = max(0.42, min(0.72, w / 900.0))
    while title_scale > 0.32 and cv2.getTextSize(title, font, title_scale, 2)[0][0] > w - 24:
        title_scale -= 0.03
    cv2.putText(banner, title, (12, 24), font, title_scale, (245, 248, 252), 2, cv2.LINE_AA)
    if subtitle:
        sub_scale = max(0.34, min(0.50, w / 1200.0))
        while sub_scale > 0.28 and cv2.getTextSize(subtitle, font, sub_scale, 1)[0][0] > w - 24:
            sub_scale -= 0.02
        cv2.putText(banner, subtitle, (12, 46), font, sub_scale, (176, 194, 218), 1, cv2.LINE_AA)
    return np.vstack([banner, img_bgr])


def draw_label_pill(
    img_bgr: np.ndarray,
    text: str,
    x: int,
    y: int,
    bg_bgr: tuple[int, int, int],
    font_scale: float = 0.48,
) -> None:
    """Draw readable white text inside a filled rounded-feel rectangle."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), baseline = cv2.getTextSize(text, font, font_scale, 1)
    h, w = img_bgr.shape[:2]
    pad_x, pad_y = 6, 4
    x1 = max(2, min(w - tw - pad_x * 2 - 2, x))
    y1 = max(2, min(h - th - pad_y * 2 - baseline - 2, y - th - pad_y * 2))
    x2 = x1 + tw + pad_x * 2
    y2 = y1 + th + pad_y * 2 + baseline
    cv2.rectangle(img_bgr, (x1, y1), (x2, y2), bg_bgr, -1)
    cv2.putText(
        img_bgr,
        text,
        (x1 + pad_x, y2 - pad_y - baseline + 1),
        font,
        font_scale,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )


def render_stage1_aruco(
    image_bgr: np.ndarray,
    code_id: int,
    marker_side_m: float,
    corners_px: list[tuple[float, float]],
    camera: CameraIntrinsicsInput,
    pose_res: Any,
) -> np.ndarray:
    """Stage 1: ArUco marker corners, reprojection error, and 3D pose axes."""
    canvas = image_bgr.copy()
    pts = np.array(corners_px, dtype=np.int32).reshape((-1, 1, 2))
    cv2.polylines(canvas, [pts], isClosed=True, color=(50, 225, 110), thickness=3)

    for idx, (px, py) in enumerate(corners_px):
        cv2.circle(canvas, (round(px), round(py)), 5, (0, 215, 255), -1)
        cv2.circle(canvas, (round(px), round(py)), 6, (20, 20, 20), 1)
        z_m = pose_res.marker_corner_depths_metric[idx]
        cv2.putText(
            canvas,
            f"C{idx} ({z_m:.2f}m)",
            (round(px) + 8, round(py) + (16 if idx >= 2 else -8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"C{idx} ({z_m:.2f}m)",
            (round(px) + 8, round(py) + (16 if idx >= 2 else -8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (30, 110, 20),
            1,
            cv2.LINE_AA,
        )

    # Project 3D marker coordinate frame axes
    K = np.array(
        [[camera.fx, 0.0, camera.cx], [0.0, camera.fy, camera.cy], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    rvec, _ = cv2.Rodrigues(pose_res.T_camera_marker[0:3, 0:3])
    tvec = pose_res.T_camera_marker[0:3, 3].reshape((3, 1))
    cv2.drawFrameAxes(canvas, K, np.zeros(4, dtype=np.float64), rvec, tvec, marker_side_m * 0.6, 3)

    reproj = pose_res.reprojection_error_mean_px
    reproj_txt = f"{reproj:.2f}px" if reproj is not None else "n/a"
    cam = pose_res.camera_position_enu_m
    sub = (
        f"ID={code_id} | side={marker_side_m:.2f}m | reproj={reproj_txt} | "
        f"cam ENU=({cam[0]:.2f}, {cam[1]:.2f}, {cam[2]:.2f})m"
    )
    return draw_header_bar(canvas, "1. ArUco Marker & Camera Pose (solvePnP)", sub)


def render_stage2_yolo(
    image_bgr: np.ndarray,
    raw_coco_boxes: list[dict[str, Any]],
    sunsync_detections: list[DetectedObject2D],
    marker_corners: list[tuple[float, float]],
) -> np.ndarray:
    """Stage 2: YOLOv8 raw COCO detections (faint) vs SunSync mapped furniture boxes."""
    canvas = image_bgr.copy()

    # Draw marker exclusion zone lightly
    xs = [pt[0] for pt in marker_corners]
    ys = [pt[1] for pt in marker_corners]
    mx1, my1, mx2, my2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
    cv2.rectangle(canvas, (mx1, my1), (mx2, my2), (90, 210, 120), 1)

    # Draw all raw COCO detections that were NOT mapped to SunSync classes in subtle gray
    for raw in raw_coco_boxes:
        if raw["mapped_label"] is not None:
            continue
        x1, y1, x2, y2 = [round(v) for v in raw["box_xyxy"]]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (170, 175, 185), 1, cv2.LINE_AA)
        cv2.putText(
            canvas,
            f"{raw['raw_class']} {raw['confidence']:.2f}",
            (x1 + 3, max(14, y1 + 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (225, 228, 235),
            1,
            cv2.LINE_AA,
        )

    # Draw SunSync mapped furniture detections (after book cluster merge & marker exclusion)
    placed_anchors: list[tuple[int, int]] = []
    for idx, det in enumerate(sunsync_detections, start=1):
        x1, y1, x2, y2 = [round(v) for v in det.box_xyxy]
        color = LABEL_COLORS_BGR.get(det.label, (140, 140, 140))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)
        tag = f"#{idx} {det.label} ({det.raw_class_name}) {det.confidence:.0%}"
        ly = y1 - 2
        while any(abs(x1 - px) < 90 and abs(ly - py) < 24 for px, py in placed_anchors):
            ly -= 24
        placed_anchors.append((x1, ly))
        draw_label_pill(canvas, tag, x1, ly, color, font_scale=0.45)

    sub = (
        f"Raw COCO={len(raw_coco_boxes)} | SunSync furniture={len(sunsync_detections)} "
        "(solid=kept, thin gray=non-furniture COCO)"
    )
    return draw_header_bar(canvas, "2. YOLOv8m 2D Object Detection", sub)


def render_stage3_depth(
    rel_depth_map: np.ndarray,
    depth_source: str,
    alpha: float,
    beta: float,
    raw_min: float,
    raw_max: float,
    *,
    is_metric: bool = True,
) -> np.ndarray:
    """Stage 3: Depth Anything V2 depth heatmap (near=warm, far=dark) with colorbar."""
    p2 = float(np.nanpercentile(rel_depth_map, 2))
    p98 = float(np.nanpercentile(rel_depth_map, 98))
    # Invert display color so near objects (small Z) appear warm/bright and far objects dark
    norm_near = 1.0 - np.clip((rel_depth_map - p2) / max(1e-6, p98 - p2), 0.0, 1.0)
    u8 = np.clip(norm_near * 255.0, 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(u8, cv2.COLORMAP_INFERNO)

    # Append a slim vertical colorbar on the right edge
    h, w = heat.shape[:2]
    bar_w = 24
    grad = np.linspace(255, 0, h, dtype=np.uint8)[:, None]
    grad_img = cv2.applyColorMap(np.repeat(grad, bar_w, axis=1), cv2.COLORMAP_INFERNO)
    canvas = heat.copy()
    x0 = w - bar_w - 10
    cv2.rectangle(canvas, (x0 - 2, 10), (x0 + bar_w + 2, h - 10), (20, 24, 30), -1)
    canvas[12 : h - 12, x0 : x0 + bar_w] = grad_img[12 : h - 12, :]
    unit = "m" if is_metric else ""
    draw_label_pill(canvas, f"near {p2:.2f}{unit}", x0 - 88, 30, (35, 42, 55), 0.38)
    draw_label_pill(canvas, f"far {p98:.2f}{unit}", x0 - 82, h - 14, (35, 42, 55), 0.38)

    if is_metric:
        title = "3. Depth Anything V2 Metric Indoor Depth"
        sub = (
            f"source={depth_source} | raw=[{raw_min:.2f}, {raw_max:.2f}]m "
            f"| cal: Z={alpha:.2f}*d+{beta:.2f}m"
        )
    else:
        title = "3. Depth Anything V2 Relative Depth (1/Disparity)"
        sub = (
            f"source={depth_source} (1/disp) | rel=[{p2:.2f}, {p98:.2f}] "
            f"| cal: Z={alpha:.2f}*d+{beta:.2f}m"
        )
    return draw_header_bar(canvas, title, sub)


def render_stage4_calibrated_overlay(
    image_bgr: np.ndarray,
    rel_depth_map: np.ndarray,
    marker_corners: list[tuple[float, float]],
    estimated_items: list[Any],
    depth_calib: Any,
) -> np.ndarray:
    """Stage 4: Blended depth map + central 60% sampling patches + metric distance & quality."""
    metric_map = depth_calib.to_metric_depth(rel_depth_map)
    m_min = float(np.nanpercentile(metric_map, 2))
    m_max = float(np.nanpercentile(metric_map, 98))
    norm = (metric_map - m_min) / max(1e-6, m_max - m_min)
    heat = cv2.applyColorMap(np.clip(norm * 255.0, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    canvas = cv2.addWeighted(image_bgr, 0.52, heat, 0.48, 0.0)

    # Anchor marker outline
    mpts = np.array(marker_corners, dtype=np.int32).reshape((-1, 1, 2))
    cv2.polylines(canvas, [mpts], isClosed=True, color=(255, 255, 255), thickness=2)

    placed_top: list[tuple[int, int]] = []
    placed_bot: list[tuple[int, int]] = []
    for idx, item in enumerate(estimated_items, start=1):
        diag = item.diagnostics
        if diag is None:
            continue
        x1, y1, x2, y2 = [round(v) for v in diag.box_xyxy_px]
        q_color = QUALITY_COLORS_BGR.get(item.estimation_quality, (200, 200, 200))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), q_color, 2, cv2.LINE_AA)

        if diag.representative_depth_region_xyxy_px is not None:
            ix1, iy1, ix2, iy2 = diag.representative_depth_region_xyxy_px
            cv2.rectangle(canvas, (ix1, iy1), (ix2, iy2), (0, 235, 255), 2, cv2.LINE_AA)
            cv2.line(canvas, (ix1, iy1), (ix2, iy2), (0, 235, 255), 1, cv2.LINE_AA)
            cv2.line(canvas, (ix1, iy2), (ix2, iy1), (0, 235, 255), 1, cv2.LINE_AA)

        iqr = diag.depth_iqr_m if diag.depth_iqr_m is not None else 0.0
        dims = item.occluder.dimensions_m
        tag = (
            f"#{idx} {item.occluder.type} Z={item.calibrated_distance_m:.2f}m "
            f"(IQR={iqr:.2f}m) [{item.estimation_quality}]"
        )
        ly = y1 - 2
        while any(abs(x1 - px) < 120 and abs(ly - py) < 22 for px, py in placed_top):
            ly -= 22
        placed_top.append((x1, ly))
        draw_label_pill(canvas, tag, x1, ly, q_color, font_scale=0.40)
        dim_tag = f"#{idx} {dims[0]:.2f}x{dims[1]:.2f}x{dims[2]:.2f}m"
        by = y2 + 22
        while any(abs(x1 - px) < 120 and abs(by - py) < 22 for px, py in placed_bot):
            by -= 22
        placed_bot.append((x1, by))
        draw_label_pill(canvas, dim_tag, x1, by, (34, 42, 56), font_scale=0.38)

    sub = (
        f"Inner yellow box = central 60% depth median patch | "
        f"alpha={depth_calib.alpha:.3f}, beta={depth_calib.beta:.2f}m"
    )
    return draw_header_bar(canvas, "4. Metric Calibration & 60% Depth Sampling", sub)


def render_stage5_floorplan(
    target_h: int,
    target_w: int,
    room_width: float,
    room_depth: float,
    T_enu_marker: list[float],
    pose_res: Any,
    estimated_items: list[Any],
) -> np.ndarray:
    """Stage 5: Top-down 2D ENU room floorplan with camera pose and 3D furniture footprints."""
    canvas = np.full((target_h, target_w, 3), (248, 250, 252), dtype=np.uint8)
    margin_x = 56
    margin_top = 64
    margin_bot = 92
    avail_w = max(120, target_w - 2 * margin_x)
    avail_h = max(120, target_h - margin_top - margin_bot)
    scale = min(avail_w / room_width, avail_h / room_depth)
    draw_w = room_width * scale
    draw_h = room_depth * scale
    ox = (target_w - draw_w) / 2.0
    oy = margin_top + (avail_h - draw_h) / 2.0

    def to_px(ex: float, ny: float) -> tuple[int, int]:
        px = round(ox + ex * scale)
        py = round(oy + (room_depth - ny) * scale)
        return px, py

    # Grid lines (every 1m)
    for gx in range(math.ceil(room_width) + 1):
        p1, p2 = to_px(gx, 0.0), to_px(gx, room_depth)
        cv2.line(canvas, p1, p2, (228, 234, 242), 1)
    for gy in range(math.ceil(room_depth) + 1):
        p1, p2 = to_px(0.0, gy), to_px(room_width, gy)
        cv2.line(canvas, p1, p2, (228, 234, 242), 1)

    # Room rectangle
    tl = to_px(0.0, room_depth)
    br = to_px(room_width, 0.0)
    cv2.rectangle(canvas, tl, br, (48, 62, 82), 2)

    # North wall window & marker position
    m_arr = np.array(T_enu_marker, dtype=np.float64).reshape((4, 4))
    mx, my = float(m_arr[0, 3]), float(m_arr[1, 3])
    w1 = to_px(max(0.0, mx - 1.0), my)
    w2 = to_px(min(room_width, mx + 1.0), my)
    cv2.line(canvas, w1, w2, (215, 130, 30), 6)
    mp = to_px(mx, my)
    cv2.circle(canvas, mp, 6, (25, 175, 65), -1)
    cv2.putText(
        canvas,
        "Window ArUco Marker (North)",
        (max(12, mp[0] - 110), max(22, mp[1] - 14)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (35, 65, 95),
        1,
        cv2.LINE_AA,
    )

    # Camera position & forward view direction
    cam_t = np.array(pose_res.T_ENU_camera, dtype=np.float64).reshape((4, 4))
    cx_e, cy_n = float(cam_t[0, 3]), float(cam_t[1, 3])
    fwd = cam_t[0:3, 2]
    cam_pt = to_px(cx_e, cy_n)
    tip_pt = to_px(cx_e + float(fwd[0]) * 0.85, cy_n + float(fwd[1]) * 0.85)
    cv2.arrowedLine(canvas, cam_pt, tip_pt, (45, 70, 210), 2, tipLength=0.25)
    cv2.circle(canvas, cam_pt, 6, (45, 70, 210), -1)
    cv2.putText(
        canvas,
        f"Camera ({cx_e:.1f}, {cy_n:.1f}m)",
        (max(10, min(target_w - 170, cam_pt[0] - 60)), min(target_h - 16, cam_pt[1] + 22)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (45, 70, 210),
        1,
        cv2.LINE_AA,
    )

    # Furniture footprints
    for idx, item in enumerate(estimated_items, start=1):
        occ = item.occluder
        corners = item.estimated_corners_enu_m
        if len(corners) == 4:
            poly = np.array([to_px(x, y) for x, y in corners], dtype=np.int32)
            color = LABEL_COLORS_BGR.get(occ.type, (120, 120, 120))
            overlay = canvas.copy()
            cv2.fillPoly(overlay, [poly], color)
            cv2.addWeighted(overlay, 0.28, canvas, 0.72, 0.0, canvas)
            cv2.polylines(canvas, [poly], isClosed=True, color=color, thickness=2)

            ctr = to_px(occ.base_center_m[0], occ.base_center_m[1])
            label = f"#{idx} {occ.type}"
            cv2.putText(
                canvas,
                label,
                (ctr[0] - 32, ctr[1] - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.44,
                (20, 28, 38),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                label,
                (ctr[0] - 32, ctr[1] - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.44,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    # Bottom summary legend
    y_leg = target_h - 68
    cv2.putText(
        canvas,
        f"Room: {room_width:.1f}m (E) x {room_depth:.1f}m (N) | Items: {len(estimated_items)}",
        (18, y_leg),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.44,
        (40, 52, 68),
        1,
        cv2.LINE_AA,
    )
    for idx, item in enumerate(estimated_items[:4], start=1):
        occ = item.occluder
        c = occ.base_center_m
        d = occ.dimensions_m
        clamp_str = " [clamped]" if item.clamped_to_room else ""
        line = (
            f"#{idx} {occ.type}: ctr=({c[0]:.2f},{c[1]:.2f})m "
            f"dim={d[0]:.2f}x{d[1]:.2f}x{d[2]:.2f}m{clamp_str}"
        )
        cv2.putText(
            canvas,
            line,
            (18, y_leg + 18 * idx),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.37,
            (60, 74, 92),
            1,
            cv2.LINE_AA,
        )

    return draw_header_bar(
        canvas,
        "5. Back-Projected 3D Footprint (ENU Floorplan)",
        "Top-down view after clamp_cuboid_to_room",
    )


def make_overview_grid(panels: list[np.ndarray], panel_height: int = 640) -> np.ndarray:
    """Resize all stage panels to uniform height and arrange into a crisp overview strip."""
    resized: list[np.ndarray] = []
    gap = 12
    for panel in panels:
        h, w = panel.shape[:2]
        scale = panel_height / float(h)
        new_w = max(1, round(w * scale))
        resized.append(cv2.resize(panel, (new_w, panel_height), interpolation=cv2.INTER_AREA))

    total_w = sum(p.shape[1] for p in resized) + gap * (len(resized) + 1)
    canvas = np.full((panel_height + 2 * gap, total_w, 3), (18, 22, 30), dtype=np.uint8)
    x = gap
    for p in resized:
        pw = p.shape[1]
        canvas[gap : gap + panel_height, x : x + pw] = p
        x += pw + gap
    return canvas


def inspect_raw_yolo_coco(
    yolo_adapter: YoloV8DetectorAdapter, image_rgb: np.ndarray
) -> list[dict[str, Any]]:
    """Run YOLOv8 directly to capture all raw COCO classes before SunSync filtering."""
    yolo_adapter._ensure_loaded()
    assert yolo_adapter._model is not None
    results = yolo_adapter._model(image_rgb, conf=yolo_adapter.conf_thresh, verbose=False)
    rows: list[dict[str, Any]] = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            cls_idx = int(box.cls[0].item())
            raw_name = r.names.get(cls_idx, "unknown").lower()
            conf = float(box.conf[0].item())
            xyxy = [round(float(v), 1) for v in box.xyxy[0].tolist()]
            rows.append(
                {
                    "raw_class": raw_name,
                    "mapped_label": yolo_adapter.COCO_TO_FURNITURE.get(raw_name),
                    "confidence": round(conf, 4),
                    "box_xyxy": xyxy,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export stage-by-stage visual outputs of SunSync FR-03 Vision AI pipeline."
    )
    parser.add_argument("--image", required=True, help="Path to input photo (JPG/PNG).")
    parser.add_argument(
        "--out-dir",
        default="vision_viz_out",
        help="Directory to write stage PNGs and summary.json.",
    )
    parser.add_argument(
        "--code-id",
        type=int,
        default=None,
        help="ArUco DICT_4X4_50 code ID (omit to auto-detect first marker in photo).",
    )
    parser.add_argument(
        "--marker-side-m",
        type=float,
        default=0.60,
        help="Physical marker side length in meters (e.g. 0.60 for window poster, 0.14 for laptop).",
    )
    parser.add_argument("--room-width", type=float, default=5.0, help="Room width East (m).")
    parser.add_argument("--room-depth", type=float, default=4.0, help="Room depth North (m).")
    parser.add_argument("--room-height", type=float, default=2.8, help="Room height (m).")
    parser.add_argument(
        "--marker-height-m", type=float, default=1.2, help="Marker center height above floor (m)."
    )
    parser.add_argument(
        "--conf-thresh", type=float, default=0.25, help="YOLO confidence threshold."
    )
    parser.add_argument("--yolo-weights", default="yolov8m.pt", help="YOLOv8 weights path/name.")
    parser.add_argument(
        "--depth-model",
        default=DEFAULT_METRIC_DEPTH_MODEL,
        help="HuggingFace Depth Anything V2 model id.",
    )
    parser.add_argument("--device", default="cpu", help="Torch device for Depth Anything V2.")
    args = parser.parse_args()

    img_path = Path(args.image)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    image_bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if image_bgr is None or image_bgr.size == 0:
        raise SystemExit(f"Could not read image: {img_path}")
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    h_img, w_img = image_rgb.shape[:2]

    focal = float(max(w_img, h_img)) * 0.9
    camera = CameraIntrinsicsInput(
        width=w_img, height=h_img, fx=focal, fy=focal, cx=w_img / 2.0, cy=h_img / 2.0
    )

    timings_ms: dict[str, float] = {}

    # Stage 1: ArUco detection + camera pose
    t0 = time.perf_counter()
    code_id, marker_corners = detect_any_aruco_marker(image_rgb, args.code_id)
    t_enu_marker = build_default_window_marker_transform(
        args.room_width, args.room_depth, args.marker_height_m
    )
    timings_ms["aruco_detect"] = round((time.perf_counter() - t0) * 1000.0, 1)

    # Stage 2: Real YOLOv8m detection
    yolo_adapter = YoloV8DetectorAdapter(model_path=args.yolo_weights, conf_thresh=args.conf_thresh)
    t0 = time.perf_counter()
    raw_coco_boxes = inspect_raw_yolo_coco(yolo_adapter, image_rgb)
    sunsync_detections = yolo_adapter.detect(image_rgb)
    xs = [pt[0] for pt in marker_corners]
    ys = [pt[1] for pt in marker_corners]
    exclude_boxes = [(min(xs), min(ys), max(xs), max(ys))]
    sunsync_detections = [
        d for d in sunsync_detections if not _overlaps_any_box(d.box_xyxy, exclude_boxes)
    ]
    timings_ms["yolov8"] = round((time.perf_counter() - t0) * 1000.0, 1)

    # Stage 3: Real Depth Anything V2 depth map (single forward pass)
    depth_adapter = MonocularDepthAdapter(model_name=args.depth_model, device=args.device)
    t0 = time.perf_counter()
    rel_depth_map = depth_adapter.estimate_relative_depth(image_rgb, w_img, h_img)
    raw_min, raw_max = float(rel_depth_map.min()), float(rel_depth_map.max())
    timings_ms["depth_anything_v2"] = round((time.perf_counter() - t0) * 1000.0, 1)

    # Stage 4 & 5: Full SunSync VisionRegistrationPipeline (using precomputed maps/adapters)
    class _FixedDepthAdapter:
        def estimate_relative_depth(self, _img: np.ndarray | None, _w: int, _h: int) -> np.ndarray:
            return rel_depth_map

    class _FixedYoloAdapter:
        def detect(
            self,
            _img: np.ndarray | None,
            hints: Any = None,
        ) -> list[DetectedObject2D]:
            return list(hints) if hints else list(sunsync_detections)

    detector = CompositeFurnitureDetector(
        prefer_yolo=True, primary_detector=_FixedYoloAdapter(), mode="on"
    )
    depth_est = CompositeDepthEstimator(
        prefer_monocular=True, primary=_FixedDepthAdapter(), mode="on"
    )
    pipeline = VisionRegistrationPipeline(detector=detector, depth_estimator=depth_est)

    t0 = time.perf_counter()
    request = VisionFurnitureEstimateRequest(
        camera=camera,
        marker_anchor=MarkerAnchorInput(
            code_id=code_id,
            side_length_m=args.marker_side_m,
            corners_px=marker_corners,
        ),
        mode="experiment",
        apply_to_scene=False,
        auto_create_protected_regions=True,
        image_base64="placeholder",
    )
    pose_res, depth_calib, estimated_items = pipeline.run(
        scene_id="local_viz",
        room_id="room_viz",
        room_floor_vertices=[
            (0.0, 0.0),
            (args.room_width, 0.0),
            (args.room_width, args.room_depth),
            (0.0, args.room_depth),
        ],
        room_height_m=args.room_height,
        T_ENU_marker=t_enu_marker,
        request=request,
        observation_id="obs_local_viz",
        image_rgb=image_rgb,
    )
    timings_ms["pipeline_3d"] = round((time.perf_counter() - t0) * 1000.0, 1)

    # Render all 5 stages + combined overview
    s1 = render_stage1_aruco(
        image_bgr, code_id, args.marker_side_m, marker_corners, camera, pose_res
    )
    s2 = render_stage2_yolo(image_bgr, raw_coco_boxes, sunsync_detections, marker_corners)
    s3 = render_stage3_depth(
        rel_depth_map,
        depth_est.last_source,
        depth_calib.alpha,
        depth_calib.beta,
        raw_min,
        raw_max,
        is_metric=depth_adapter.is_metric_depth,
    )
    s4 = render_stage4_calibrated_overlay(
        image_bgr, rel_depth_map, marker_corners, estimated_items, depth_calib
    )
    s5 = render_stage5_floorplan(
        h_img, w_img, args.room_width, args.room_depth, t_enu_marker, pose_res, estimated_items
    )
    overview = make_overview_grid([s1, s2, s3, s4, s5], panel_height=680)

    outputs = {
        "01_aruco_pose.png": s1,
        "02_yolo_detections.png": s2,
        "03_depth_anything_v2.png": s3,
        "04_metric_depth_sampling.png": s4,
        "05_room_floorplan_2d.png": s5,
        "00_pipeline_overview.png": overview,
    }
    for fname, img in outputs.items():
        cv2.imwrite(str(out_dir / fname), img)

    summary = {
        "input_image": str(img_path),
        "image_size_wh": [w_img, h_img],
        "marker": {
            "code_id": code_id,
            "side_length_m": args.marker_side_m,
            "corners_px": [[round(x, 1), round(y, 1)] for x, y in marker_corners],
            "corner_metric_depths_m": [round(z, 4) for z in pose_res.marker_corner_depths_metric],
            "reprojection_error_mean_px": pose_res.reprojection_error_mean_px,
            "camera_position_enu_m": [round(v, 4) for v in pose_res.camera_position_enu_m],
        },
        "raw_yolo_coco_detections": raw_coco_boxes,
        "sunsync_detections": [
            {
                "label": d.label,
                "raw_class_name": d.raw_class_name,
                "confidence": round(d.confidence, 4),
                "box_xyxy": [round(v, 1) for v in d.box_xyxy],
            }
            for d in sunsync_detections
        ],
        "depth_calibration": {
            "depth_source": depth_est.last_source,
            "raw_disparity_range": [round(raw_min, 3), round(raw_max, 3)],
            "alpha": round(depth_calib.alpha, 5),
            "beta": round(depth_calib.beta, 5),
            "diagnostics": (
                pipeline.last_diagnostics.depth.model_dump(mode="json")
                if pipeline.last_diagnostics is not None
                else None
            ),
        },
        "estimated_items": [
            {
                "name": item.occluder.name,
                "label": item.occluder.type,
                "confidence": round(item.confidence, 4),
                "calibrated_distance_m": round(item.calibrated_distance_m, 3),
                "base_center_enu_m": [round(v, 3) for v in item.occluder.base_center_m],
                "dimensions_m": [round(v, 3) for v in item.occluder.dimensions_m],
                "yaw_deg": item.occluder.yaw_deg_ccw_from_east,
                "clamped_to_room": item.clamped_to_room,
                "estimation_quality": item.estimation_quality,
                "uncertainty_reason_ko": item.uncertainty_reason_ko,
                "diagnostics": (
                    item.diagnostics.model_dump(mode="json") if item.diagnostics else None
                ),
            }
            for item in estimated_items
        ],
        "timings_ms": timings_ms,
        "output_files": sorted(outputs.keys()),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Saved {len(outputs)} stage images + summary.json to {out_dir}")
    print(f"Timings (ms): {timings_ms}")
    print(
        f"Marker ID={code_id}, reproj={pose_res.reprojection_error_mean_px:.3f}px, "
        f"depth alpha={depth_calib.alpha:.4f}, beta={depth_calib.beta:.4f}"
    )
    for idx, item in enumerate(estimated_items, start=1):
        occ = item.occluder
        print(
            f"  #{idx} {occ.type} (conf={item.confidence:.2f}): "
            f"Z={item.calibrated_distance_m:.2f}m, "
            f"dims={tuple(round(v, 2) for v in occ.dimensions_m)}, "
            f"center={tuple(round(v, 2) for v in occ.base_center_m)}, "
            f"quality={item.estimation_quality}"
        )


if __name__ == "__main__":
    main()
