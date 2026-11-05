"""Vision AI registration pipeline for furniture classification and 3D bounding box estimation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np
from app.schemas.scene import Occluder, ProtectedRegion, Quality
from app.services.film import clamp_cuboid_to_room
from shapely.geometry import Polygon

from engine.vision_ai.protocols import FurnitureLabel

if TYPE_CHECKING:
    from app.schemas.vision_ai import (
        CameraIntrinsicsInput,
        Detection2DInput,
        EstimatedFurnitureResult,
        MarkerAnchorInput,
        VisionFurnitureEstimateRequest,
    )

# Standard physical bounds for monocular metric depth (metres)
MIN_METRIC_DEPTH_M = 0.3
MAX_METRIC_DEPTH_M = 25.0

# Class-specific geometric priors (width, depth, height) in metres
CLASS_GEOMETRIC_PRIORS: dict[FurnitureLabel, dict[str, float]] = {
    "bookshelf": {"depth": 0.40, "height": 1.80, "min_height": 0.8, "max_height": 2.5},
    "desk": {"depth": 0.70, "height": 0.75, "min_height": 0.5, "max_height": 1.2},
    "chair": {"depth": 0.55, "height": 0.85, "min_height": 0.4, "max_height": 1.3},
    "other": {"depth": 0.60, "height": 1.00, "min_height": 0.3, "max_height": 2.5},
}


@dataclass(frozen=True)
class CameraPoseResult:
    """Resolved camera pose in ENU and metric depths of anchor marker corners."""

    T_ENU_camera: list[float]  # 16-element row-major rigid transform
    camera_position_enu_m: tuple[float, float, float]
    marker_corner_depths_metric: list[float]  # Depth Z_c of 4 corners in camera frame
    T_camera_marker: np.ndarray  # 4x4 matrix


@dataclass(frozen=True)
class DepthCalibrationResult:
    """Linear calibration parameters: Z_metric = alpha * d_rel + beta."""

    alpha: float
    beta: float

    def to_metric_depth(self, d_rel: float | np.ndarray) -> float | np.ndarray:
        raw = self.alpha * d_rel + self.beta
        if isinstance(raw, np.ndarray):
            return np.clip(raw, MIN_METRIC_DEPTH_M, MAX_METRIC_DEPTH_M)
        if not math.isfinite(raw):
            return MIN_METRIC_DEPTH_M
        return float(min(max(raw, MIN_METRIC_DEPTH_M), MAX_METRIC_DEPTH_M))


class VisionRegistrationPipeline:
    """Pipeline for ArUco-anchored metric depth calibration and 3D cuboid unprojection."""

    # -------------------------------------------------------------------------
    # Step 1: ArUco Window Marker Metric Anchor
    # -------------------------------------------------------------------------
    @staticmethod
    def resolve_camera_pose_from_aruco(
        camera: CameraIntrinsicsInput,
        marker_anchor: MarkerAnchorInput,
        T_ENU_marker: Sequence[float],
        T_ENU_camera_presolved: Sequence[float] | None = None,
        image_rgb: np.ndarray | None = None,
    ) -> CameraPoseResult:
        """Resolve T_ENU_camera and exact metric depths of window marker corners using solvePnP."""
        s = marker_anchor.side_length_m

        # Marker object points in marker local coordinate system:
        # Standard ArUco square points order (clockwise starting top-left):
        # 0: [-s/2,  s/2, 0]
        # 1: [ s/2,  s/2, 0]
        # 2: [ s/2, -s/2, 0]
        # 3: [-s/2, -s/2, 0]
        obj_pts = np.array(
            [
                [-s / 2.0, s / 2.0, 0.0],
                [s / 2.0, s / 2.0, 0.0],
                [s / 2.0, -s / 2.0, 0.0],
                [-s / 2.0, -s / 2.0, 0.0],
            ],
            dtype=np.float32,
        )

        K = np.array(
            [
                [camera.fx, 0.0, camera.cx],
                [0.0, camera.fy, camera.cy],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        dist_coeffs = np.zeros(4, dtype=np.float64)

        T_enu_m = np.array(T_ENU_marker, dtype=np.float64).reshape((4, 4))

        # Branch A: Pre-solved T_ENU_camera provided
        if T_ENU_camera_presolved is not None:
            T_enu_c = np.array(T_ENU_camera_presolved, dtype=np.float64).reshape((4, 4))
            T_c_enu = np.linalg.inv(T_enu_c)
            T_c_m = T_c_enu @ T_enu_m

            # Metric depth of marker corners in camera frame
            corner_depths: list[float] = []
            for pt in obj_pts:
                p_c = T_c_m[0:3, 0:3] @ pt + T_c_m[0:3, 3]
                corner_depths.append(float(p_c[2]))

            cam_pos = (float(T_enu_c[0, 3]), float(T_enu_c[1, 3]), float(T_enu_c[2, 3]))
            return CameraPoseResult(
                T_ENU_camera=[round(float(v), 6) for v in T_enu_c.flatten()],
                camera_position_enu_m=cam_pos,
                marker_corner_depths_metric=corner_depths,
                T_camera_marker=T_c_m,
            )

        # Branch B: Solve PnP from corner pixels
        corners_px = marker_anchor.corners_px
        if corners_px is None and image_rgb is not None:
            corners_px = VisionRegistrationPipeline._detect_aruco_corners(
                image_rgb, marker_anchor.code_id
            )

        if corners_px is None:
            raise ValueError(
                "Either marker corners_px or pre-solved T_ENU_camera must be provided."
            )

        img_pts = np.array(corners_px, dtype=np.float32).reshape((4, 2))

        # Solve PnP using IPPE_SQUARE as primary, falling back to ITERATIVE if necessary
        rvec, tvec = VisionRegistrationPipeline._solve_pnp_robust(obj_pts, img_pts, K, dist_coeffs)

        R_c_m, _ = cv2.Rodrigues(rvec)
        t_c_m = tvec.reshape((3, 1))

        # Marker pose in camera frame: T_c_m
        T_c_m = np.eye(4, dtype=np.float64)
        T_c_m[0:3, 0:3] = R_c_m
        T_c_m[0:3, 3] = t_c_m.ravel()

        # Compute T_ENU_camera = T_ENU_marker @ inv(T_c_m)
        R_enu_m = T_enu_m[0:3, 0:3]
        t_enu_m = T_enu_m[0:3, 3]

        R_enu_c = R_enu_m @ R_c_m.T
        t_enu_c = t_enu_m - (R_enu_c @ t_c_m.ravel())

        T_enu_c = np.eye(4, dtype=np.float64)
        T_enu_c[0:3, 0:3] = R_enu_c
        T_enu_c[0:3, 3] = t_enu_c

        corner_depths = []
        for pt in obj_pts:
            p_c = R_c_m @ pt + t_c_m.ravel()
            corner_depths.append(float(p_c[2]))

        cam_pos = (float(t_enu_c[0]), float(t_enu_c[1]), float(t_enu_c[2]))
        return CameraPoseResult(
            T_ENU_camera=[round(float(v), 6) for v in T_enu_c.flatten()],
            camera_position_enu_m=cam_pos,
            marker_corner_depths_metric=corner_depths,
            T_camera_marker=T_c_m,
        )

    @staticmethod
    def _solve_pnp_robust(
        obj_pts: np.ndarray, img_pts: np.ndarray, K: np.ndarray, dist_coeffs: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Solve PnP using SOLVEPNP_IPPE_SQUARE, falling back to SOLVEPNP_ITERATIVE if reprojection error is high."""
        try:
            success, rvec, tvec = cv2.solvePnP(
                obj_pts, img_pts, K, dist_coeffs, flags=cv2.SOLVEPNP_IPPE_SQUARE
            )
            if success:
                proj, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist_coeffs)
                err = float(np.mean(np.linalg.norm(proj.reshape(-1, 2) - img_pts, axis=1)))
                if err <= 1.0:
                    return rvec, tvec
        except cv2.error:
            pass

        # Fallback to iterative optimization
        success, rvec, tvec = cv2.solvePnP(
            obj_pts, img_pts, K, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
        )
        return rvec, tvec

    @staticmethod
    def _detect_aruco_corners(
        image_rgb: np.ndarray, target_code_id: int
    ) -> list[tuple[float, float]]:
        """Detect ArUco DICT_4X4_50 marker corners from image."""
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        corners, ids, _ = detector.detectMarkers(image_rgb)
        if ids is not None and len(ids) > 0:
            for i, cid in enumerate(ids.flatten()):
                if cid == target_code_id:
                    c = corners[i][0]
                    return [(float(pt[0]), float(pt[1])) for pt in c]
        raise ValueError(f"ArUco marker code_id {target_code_id} not detected in image.")

    # -------------------------------------------------------------------------
    # Step 2: Metric Depth Scale & Shift Calibration
    # -------------------------------------------------------------------------
    @staticmethod
    def calibrate_monocular_depth(
        anchor_relative_depths: Sequence[float],
        anchor_metric_depths: Sequence[float],
        manual_alpha: float | None = None,
        manual_beta: float | None = None,
    ) -> DepthCalibrationResult:
        """Calibrate linear relationship: Z_metric = alpha * d_rel + beta."""
        if manual_alpha is not None and manual_beta is not None:
            return DepthCalibrationResult(alpha=float(manual_alpha), beta=float(manual_beta))

        if len(anchor_relative_depths) == 0 or len(anchor_metric_depths) == 0:
            return DepthCalibrationResult(alpha=1.0, beta=0.0)

        rel = np.array(anchor_relative_depths, dtype=np.float64)
        met = np.array(anchor_metric_depths, dtype=np.float64)

        if manual_alpha is not None:
            beta = float(np.mean(met - manual_alpha * rel))
            return DepthCalibrationResult(alpha=float(manual_alpha), beta=beta)

        if len(rel) >= 2 and (float(np.ptp(rel)) > 1e-4):
            try:
                coeffs = np.polyfit(rel, met, deg=1)
                alpha, beta = float(coeffs[0]), float(coeffs[1])
                if not (math.isfinite(alpha) and math.isfinite(beta) and alpha > 0.01):
                    raise ValueError(f"Ill-conditioned linear calibration: alpha={alpha}")
            except (np.linalg.LinAlgError, ValueError):
                mean_rel = max(1e-4, float(np.mean(rel)))
                mean_met = float(np.mean(met))
                alpha = max(0.01, mean_met / mean_rel)
                beta = 0.0
        else:
            mean_rel = max(1e-4, float(np.mean(rel)))
            mean_met = float(np.mean(met))
            alpha = max(0.01, mean_met / mean_rel)
            beta = 0.0

        return DepthCalibrationResult(alpha=alpha, beta=beta)

    # -------------------------------------------------------------------------
    # Step 3: 3D Cuboid Back-Projection & Floor-Standing Refinement
    # -------------------------------------------------------------------------
    @staticmethod
    def backproject_box_to_3d_cuboid(
        detection: Detection2DInput,
        camera: CameraIntrinsicsInput,
        T_ENU_camera: Sequence[float],
        depth_calibration: DepthCalibrationResult,
        room_floor_poly: Polygon,
        room_height_m: float,
        room_id: str,
        occluder_id: str,
        observation_id: str,
        policy_id: str = "policy_default",
        auto_create_protected_regions: bool = True,
        depth_map_metric: np.ndarray | None = None,
    ) -> EstimatedFurnitureResult:
        """Unproject 2D box + calibrated depth into floor-standing 3D cuboid and generate ProtectedRegion."""
        from app.schemas.vision_ai import EstimatedFurnitureResult

        x1, y1, x2, y2 = detection.box_xyxy
        w_px = max(1.0, x2 - x1)
        h_px = max(1.0, y2 - y1)
        u_c = (x1 + x2) / 2.0
        v_c = (y1 + y2) / 2.0

        # Determine metric distance to object
        if depth_map_metric is not None:
            ix1 = int(max(0, min(depth_map_metric.shape[1] - 1, x1 + 0.2 * w_px)))
            ix2 = int(max(0, min(depth_map_metric.shape[1], x2 - 0.2 * w_px)))
            iy1 = int(max(0, min(depth_map_metric.shape[0] - 1, y1 + 0.2 * h_px)))
            iy2 = int(max(0, min(depth_map_metric.shape[0], y2 - 0.2 * h_px)))
            patch = depth_map_metric[iy1:iy2, ix1:ix2]
            if patch.size > 0:
                z_metric = float(np.median(patch))
            else:
                z_metric = 2.5
        elif detection.observed_depth_rel is not None:
            z_metric = float(depth_calibration.to_metric_depth(detection.observed_depth_rel))
        else:
            z_metric = 2.5

        z_metric = float(np.clip(z_metric, MIN_METRIC_DEPTH_M, MAX_METRIC_DEPTH_M))

        fx, fy, cx, cy = camera.fx, camera.fy, camera.cx, camera.cy
        T_enu_c = np.array(T_ENU_camera, dtype=np.float64).reshape((4, 4))
        R_enu_c = T_enu_c[0:3, 0:3]
        t_enu_c = T_enu_c[0:3, 3]

        p_left_c = np.array([(x1 - cx) / fx * z_metric, (v_c - cy) / fy * z_metric, z_metric])
        p_right_c = np.array([(x2 - cx) / fx * z_metric, (v_c - cy) / fy * z_metric, z_metric])
        p_top_c = np.array([(u_c - cx) / fx * z_metric, (y1 - cy) / fy * z_metric, z_metric])
        p_bot_c = np.array([(u_c - cx) / fx * z_metric, (y2 - cy) / fy * z_metric, z_metric])

        p_left_enu = R_enu_c @ p_left_c + t_enu_c
        p_right_enu = R_enu_c @ p_right_c + t_enu_c
        p_top_enu = R_enu_c @ p_top_c + t_enu_c
        p_bot_enu = R_enu_c @ p_bot_c + t_enu_c

        prior = CLASS_GEOMETRIC_PRIORS.get(detection.label, CLASS_GEOMETRIC_PRIORS["other"])

        lateral_vec = np.array([p_right_enu[0] - p_left_enu[0], p_right_enu[1] - p_left_enu[1]])
        measured_width = float(np.linalg.norm(lateral_vec))
        width_m = max(0.2, min(5.0, measured_width)) if measured_width > 0.1 else 0.8

        yaw_rad = math.atan2(float(lateral_vec[1]), float(lateral_vec[0]))
        yaw_deg = math.degrees(yaw_rad) % 360.0

        rad = math.radians(yaw_deg)
        c_yaw, s_yaw = math.cos(rad), math.sin(rad)
        v_vec = np.array([-s_yaw, c_yaw, 0.0])

        if detection.depth_span_rel is not None:
            measured_depth = float(detection.depth_span_rel * depth_calibration.alpha)
            if 0.2 <= measured_depth <= 2.5:
                depth_m = measured_depth
            else:
                depth_m = prior["depth"]
        else:
            depth_m = prior["depth"]

        measured_height = float(p_top_enu[2] - p_bot_enu[2])
        if prior["min_height"] <= measured_height <= prior["max_height"]:
            height_m = measured_height
        elif p_top_enu[2] > 0.3:
            height_m = float(min(p_top_enu[2], room_height_m * 0.95))
        else:
            height_m = prior["height"]
        height_m = max(0.1, min(room_height_m * 0.98, height_m))

        front_center = 0.5 * (p_left_enu + p_right_enu)
        view_dir_xy = np.array([front_center[0] - t_enu_c[0], front_center[1] - t_enu_c[1], 0.0])
        if np.dot(view_dir_xy, v_vec) < 0:
            yaw_deg = (yaw_deg + 180.0) % 360.0
            rad = math.radians(yaw_deg)
            c_yaw, s_yaw = math.cos(rad), math.sin(rad)
            v_vec = np.array([-s_yaw, c_yaw, 0.0])

        center_x = float(front_center[0] + 0.5 * depth_m * v_vec[0])
        center_y = float(front_center[1] + 0.5 * depth_m * v_vec[1])
        base_center = (center_x, center_y, 0.0)
        dimensions = (width_m, depth_m, height_m)

        clamped_base, clamped_dims, was_clamped = clamp_cuboid_to_room(
            base_center=base_center,
            dimensions=dimensions,
            yaw=yaw_deg,
            floor_poly=room_floor_poly,
            room_height=room_height_m,
        )

        display_name = detection.name or f"{detection.label.capitalize()} (Vision AI)"

        occluder = Occluder(
            occluder_id=occluder_id,
            room_id=room_id,
            name=display_name,
            kind="cuboid",
            type=detection.label,
            base_center_m=clamped_base,
            dimensions_m=clamped_dims,
            yaw_deg_ccw_from_east=round(yaw_deg, 4),
            material="opaque",
            quality=Quality(observation_ids=[observation_id], confirmed=False),
        )

        protected_region: ProtectedRegion | None = None
        if auto_create_protected_regions:
            cx_c, cy_c, cz_c = clamped_base
            w_c, d_c, h_c = clamped_dims
            rad_c = math.radians(yaw_deg)
            cu, su = math.cos(rad_c), math.sin(rad_c)
            u_c_vec = (cu, su, 0.0)
            v_c_vec = (-su, cu, 0.0)

            region_id = f"region_{occluder_id}"
            grade = detection.protection_grade

            if detection.label == "bookshelf":
                normal = (su, -cu, 0.0)
                fc_x = cx_c - 0.5 * d_c * v_c_vec[0]
                fc_y = cy_c - 0.5 * d_c * v_c_vec[1]
                p0 = (fc_x - 0.5 * w_c * u_c_vec[0], fc_y - 0.5 * w_c * u_c_vec[1], cz_c)
                p1 = (fc_x + 0.5 * w_c * u_c_vec[0], fc_y + 0.5 * w_c * u_c_vec[1], cz_c)
                p2 = (fc_x + 0.5 * w_c * u_c_vec[0], fc_y + 0.5 * w_c * u_c_vec[1], cz_c + h_c)
                p3 = (fc_x - 0.5 * w_c * u_c_vec[0], fc_y - 0.5 * w_c * u_c_vec[1], cz_c + h_c)
                protected_region = ProtectedRegion(
                    region_id=region_id,
                    room_id=room_id,
                    name=f"{display_name} Bookshelf Front",
                    host_occluder_id=occluder_id,
                    vertices_m=[p0, p1, p2, p3],
                    receiving_normal=normal,
                    grade=grade,
                    policy_id=policy_id,
                    quality=Quality(observation_ids=[observation_id], confirmed=False),
                )
            else:
                top_z = cz_c + h_c
                p0 = (
                    cx_c - 0.5 * w_c * u_c_vec[0] - 0.5 * d_c * v_c_vec[0],
                    cy_c - 0.5 * w_c * u_c_vec[1] - 0.5 * d_c * v_c_vec[1],
                    top_z,
                )
                p1 = (
                    cx_c + 0.5 * w_c * u_c_vec[0] - 0.5 * d_c * v_c_vec[0],
                    cy_c + 0.5 * w_c * u_c_vec[1] - 0.5 * d_c * v_c_vec[1],
                    top_z,
                )
                p2 = (
                    cx_c + 0.5 * w_c * u_c_vec[0] + 0.5 * d_c * v_c_vec[0],
                    cy_c + 0.5 * w_c * u_c_vec[1] + 0.5 * d_c * v_c_vec[1],
                    top_z,
                )
                p3 = (
                    cx_c - 0.5 * w_c * u_c_vec[0] + 0.5 * d_c * v_c_vec[0],
                    cy_c - 0.5 * w_c * u_c_vec[1] + 0.5 * d_c * v_c_vec[1],
                    top_z,
                )
                protected_region = ProtectedRegion(
                    region_id=region_id,
                    room_id=room_id,
                    name=f"{display_name} Surface",
                    host_occluder_id=occluder_id,
                    vertices_m=[p0, p1, p2, p3],
                    receiving_normal=(0.0, 0.0, 1.0),
                    grade=grade,
                    policy_id=policy_id,
                    quality=Quality(observation_ids=[observation_id], confirmed=False),
                )

        return EstimatedFurnitureResult(
            occluder=occluder,
            protected_region=protected_region,
            confidence=detection.confidence,
            calibrated_distance_m=z_metric,
            clamped_to_room=was_clamped,
        )

    # -------------------------------------------------------------------------
    # Full Execution Pipeline
    # -------------------------------------------------------------------------
    def run(
        self,
        scene_id: str,
        room_id: str,
        room_floor_vertices: Sequence[tuple[float, float]],
        room_height_m: float,
        T_ENU_marker: Sequence[float],
        request: VisionFurnitureEstimateRequest,
        observation_id: str,
        policy_id: str = "policy_default",
        image_rgb: np.ndarray | None = None,
        depth_map: np.ndarray | None = None,
        existing_ids: set[str] | None = None,
    ) -> tuple[CameraPoseResult, DepthCalibrationResult, list[EstimatedFurnitureResult]]:
        """Run full 3-step registration pipeline."""
        pose_res = self.resolve_camera_pose_from_aruco(
            camera=request.camera,
            marker_anchor=request.marker_anchor,
            T_ENU_marker=T_ENU_marker,
            T_ENU_camera_presolved=request.T_ENU_camera,
            image_rgb=image_rgb,
        )

        anchor_rel = [request.marker_anchor.anchor_depth_rel] * len(
            pose_res.marker_corner_depths_metric
        )
        depth_calib = self.calibrate_monocular_depth(
            anchor_relative_depths=anchor_rel,
            anchor_metric_depths=pose_res.marker_corner_depths_metric,
            manual_alpha=request.depth_scale_alpha,
            manual_beta=request.depth_shift_beta,
        )

        room_floor_poly = Polygon(room_floor_vertices)
        estimated_items: list[EstimatedFurnitureResult] = []
        used_ids = set(existing_ids or ())

        for idx, det in enumerate(request.detections):
            counter = idx + 1
            occ_id = f"occluder_{det.label}_{counter}"
            while occ_id in used_ids or f"region_{occ_id}" in used_ids:
                counter += 1
                occ_id = f"occluder_{det.label}_{counter}"
            used_ids.add(occ_id)
            used_ids.add(f"region_{occ_id}")

            res = self.backproject_box_to_3d_cuboid(
                detection=det,
                camera=request.camera,
                T_ENU_camera=pose_res.T_ENU_camera,
                depth_calibration=depth_calib,
                room_floor_poly=room_floor_poly,
                room_height_m=room_height_m,
                room_id=room_id,
                occluder_id=occ_id,
                observation_id=observation_id,
                policy_id=policy_id,
                auto_create_protected_regions=request.auto_create_protected_regions,
                depth_map_metric=depth_map,
            )
            estimated_items.append(res)

        return pose_res, depth_calib, estimated_items
