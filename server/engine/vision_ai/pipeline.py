"""Vision AI registration pipeline for furniture classification and 3D bounding box estimation."""

from __future__ import annotations

import base64
import io
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import cv2
import numpy as np
from app.schemas.scene import Occluder, ProtectedRegion, Quality
from app.services.film import clamp_cuboid_to_room
from shapely.geometry import Point, Polygon

from engine.vision_ai.protocols import (
    CompositeDepthEstimator,
    CompositeFurnitureDetector,
    DepthEstimatorProtocol,
    DetectionSource,
    FurnitureLabel,
    GeometricSyntheticDetector,
    ObjectDetectorProtocol,
    create_default_depth_estimator,
    create_default_detector,
)

if TYPE_CHECKING:
    from app.schemas.vision_ai import (
        CameraIntrinsicsInput,
        Detection2DInput,
        EstimatedFurnitureResult,
        EstimationQuality,
        MarkerAnchorInput,
        VisionDiagnostics,
        VisionFurnitureEstimateRequest,
    )

# Standard physical bounds for monocular metric depth (metres)
MIN_METRIC_DEPTH_M = 0.3
MAX_METRIC_DEPTH_M = 25.0

KOREAN_FURNITURE_LABELS: dict[FurnitureLabel, str] = {
    "bookshelf": "책장",
    "desk": "책상",
    "chair": "의자",
    "other": "가구",
}

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
    pose_method: str
    reprojection_error_mean_px: float | None
    reprojection_error_max_px: float | None


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

    def __init__(
        self,
        detector: ObjectDetectorProtocol | None = None,
        depth_estimator: DepthEstimatorProtocol | None = None,
    ) -> None:
        self.detector = detector or create_default_detector(prefer_yolo=True)
        self.depth_estimator = depth_estimator or create_default_depth_estimator(
            prefer_monocular=True
        )
        self.last_diagnostics: VisionDiagnostics | None = None

    @staticmethod
    def extract_exif_fx_from_bytes(raw: bytes, upright_w: int, upright_h: int) -> float | None:
        """Extract focal length in pixels from EXIF ``FocalLengthIn35mmFilm`` when available."""
        try:
            from PIL import ExifTags, Image

            with Image.open(io.BytesIO(raw)) as im:
                exif = im.getexif()
                ifd = exif.get_ifd(ExifTags.IFD.Exif)
                exif_tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in ifd.items()}
                f35_raw = exif_tags.get("FocalLengthIn35mmFilm")
                if f35_raw is None:
                    return None
                f35_mm = float(f35_raw)
                if not (5.0 < f35_mm < 300.0):
                    return None
                return float(max(upright_w, upright_h)) * (f35_mm / 36.0)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
            return None

    @staticmethod
    def decode_image_base64_with_exif(image_base64: str) -> tuple[np.ndarray, float | None]:
        """Decode base64 JPEG/PNG bytes with EXIF orientation transpose and optional EXIF fx."""
        payload = image_base64.split(",", 1)[1] if "," in image_base64 else image_base64
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception as exc:
            raise ValueError("Invalid base64 image payload.") from exc

        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(raw)) as im:
                im_upright = ImageOps.exif_transpose(im)
                rgb = np.asarray(im_upright.convert("RGB"), dtype=np.uint8)
                h_img, w_img = rgb.shape[:2]
                fx_exif = VisionRegistrationPipeline.extract_exif_fx_from_bytes(raw, w_img, h_img)
                return rgb, fx_exif
        except (OSError, ValueError, TypeError, AttributeError, ImportError):
            arr = np.frombuffer(raw, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None or bgr.size == 0:
                raise ValueError("Could not decode image bytes from base64 payload.") from None
            rgb_cv = np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)
            h_img, w_img = rgb_cv.shape[:2]
            fx_exif = VisionRegistrationPipeline.extract_exif_fx_from_bytes(raw, w_img, h_img)
            return rgb_cv, fx_exif

    @staticmethod
    def decode_image_base64(image_base64: str) -> np.ndarray:
        """Decode base64-encoded JPEG/PNG image bytes into an upright RGB uint8 array."""
        rgb, _ = VisionRegistrationPipeline.decode_image_base64_with_exif(image_base64)
        return rgb

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
                pose_method="pre_solved_transform",
                reprojection_error_mean_px=None,
                reprojection_error_max_px=None,
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
        projected, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist_coeffs)
        reprojection_errors = np.linalg.norm(projected.reshape(-1, 2) - img_pts, axis=1)
        return CameraPoseResult(
            T_ENU_camera=[round(float(v), 6) for v in T_enu_c.flatten()],
            camera_position_enu_m=cam_pos,
            marker_corner_depths_metric=corner_depths,
            T_camera_marker=T_c_m,
            pose_method="solvepnp_ippe_or_iterative",
            reprojection_error_mean_px=float(np.mean(reprojection_errors)),
            reprojection_error_max_px=float(np.max(reprojection_errors)),
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

    @staticmethod
    def _sample_marker_interior_depth(
        rel_depth_map: np.ndarray,
        marker_corners: Sequence[tuple[float, float]],
    ) -> float | None:
        """Return median finite depth inside the inner 60% of the detected marker quad."""
        if len(marker_corners) != 4:
            return None
        h, w = rel_depth_map.shape[:2]
        pts = np.array(marker_corners, dtype=np.float32)
        center = pts.mean(axis=0)
        inner_pts = np.round(center + 0.6 * (pts - center)).astype(np.int32)
        inner_pts[:, 0] = np.clip(inner_pts[:, 0], 0, w - 1)
        inner_pts[:, 1] = np.clip(inner_pts[:, 1], 0, h - 1)
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, [inner_pts], 1)
        vals = rel_depth_map[mask > 0]
        finite = vals[np.isfinite(vals)]
        if finite.size == 0:
            return None
        return float(np.median(finite))

    @staticmethod
    def _search_optimal_focal_length(
        *,
        marker_corners: Sequence[tuple[float, float]],
        side_length_m: float,
        T_ENU_marker: Sequence[float],
        width: int,
        height: int,
        room_floor_poly: Polygon,
        room_height_m: float,
    ) -> float:
        """1D focal-length search over fx/max(w,h) in [0.35, 0.90] minimizing IPPE error inside room."""
        s = side_length_m
        obj_pts = np.array(
            [
                [-s / 2.0, s / 2.0, 0.0],
                [s / 2.0, s / 2.0, 0.0],
                [s / 2.0, -s / 2.0, 0.0],
                [-s / 2.0, -s / 2.0, 0.0],
            ],
            dtype=np.float32,
        )
        img_pts = np.array(marker_corners, dtype=np.float32).reshape((4, 2))
        dist_coeffs = np.zeros(4, dtype=np.float64)
        T_enu_m = np.array(T_ENU_marker, dtype=np.float64).reshape((4, 4))
        R_enu_m = T_enu_m[0:3, 0:3]
        t_enu_m = T_enu_m[0:3, 3]
        max_side = float(max(width, height))
        cx, cy = width / 2.0, height / 2.0
        room_buf = room_floor_poly.buffer(0.25)

        def _eval_ratio(ratio: float) -> tuple[bool, float]:
            fx = max_side * ratio
            K = np.array([[fx, 0.0, cx], [0.0, fx, cy], [0.0, 0.0, 1.0]], dtype=np.float64)
            try:
                ok, rvec, tvec = cv2.solvePnP(
                    obj_pts, img_pts, K, dist_coeffs, flags=cv2.SOLVEPNP_IPPE_SQUARE
                )
            except cv2.error:
                return False, float("inf")
            if not ok:
                return False, float("inf")
            proj, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist_coeffs)
            err = float(np.mean(np.linalg.norm(proj.reshape(-1, 2) - img_pts, axis=1)))
            R_c_m, _ = cv2.Rodrigues(rvec)
            t_c_m = tvec.ravel()
            R_enu_c = R_enu_m @ R_c_m.T
            t_enu_c = t_enu_m - (R_enu_c @ t_c_m)
            inside = bool(
                room_buf.covers(Point(float(t_enu_c[0]), float(t_enu_c[1])))
                and (-0.25 <= float(t_enu_c[2]) <= room_height_m + 0.25)
            )
            return inside, err

        best_inside: tuple[float, float] | None = None
        best_any: tuple[float, float] = (float("inf"), 0.68)
        for ratio in np.linspace(0.35, 0.90, 56):
            r = float(ratio)
            inside, err = _eval_ratio(r)
            if err < best_any[0]:
                best_any = (err, r)
            if inside and (best_inside is None or err < best_inside[0]):
                best_inside = (err, r)

        seed_ratio = best_inside[1] if best_inside is not None else best_any[1]
        best_refined_inside: tuple[float, float] | None = best_inside
        best_refined_any: tuple[float, float] = best_any
        lo = max(0.34, seed_ratio - 0.015)
        hi = min(0.92, seed_ratio + 0.015)
        for ratio in np.linspace(lo, hi, 16):
            r = float(ratio)
            inside, err = _eval_ratio(r)
            if err < best_refined_any[0]:
                best_refined_any = (err, r)
            if inside and (best_refined_inside is None or err < best_refined_inside[0]):
                best_refined_inside = (err, r)

        final_ratio = (
            best_refined_inside[1] if best_refined_inside is not None else best_refined_any[1]
        )
        return max_side * final_ratio

    @staticmethod
    def _dominant_room_yaw_deg(room_floor_poly: Polygon) -> float:
        """Return principal wall orientation modulo 90 degrees (0.0 for axis-aligned rooms)."""
        coords = list(room_floor_poly.exterior.coords)
        if len(coords) < 2:
            return 0.0
        best_len = -1.0
        best_angle = 0.0
        for idx in range(len(coords) - 1):
            dx = float(coords[idx + 1][0] - coords[idx][0])
            dy = float(coords[idx + 1][1] - coords[idx][1])
            seg_len = math.hypot(dx, dy)
            if seg_len > best_len:
                best_len = seg_len
                best_angle = math.degrees(math.atan2(dy, dx)) % 90.0
        if abs(best_angle) < 1e-3 or abs(best_angle - 90.0) < 1e-3:
            return 0.0
        return round(best_angle, 4)

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
        """Calibrate linear relationship: Z_metric = alpha * d_rel + beta.

        Single planar ArUco markers span < 1.5 m in depth and suffer severe affine
        extrapolation noise when fitting 2 parameters to 4 coplanar corners, so they
        default to proportional scaling (``beta = 0.0``). Full 2-parameter affine
        fitting is enabled only when multi-range anchors span >= 1.5 m.
        """
        if manual_alpha is not None and manual_beta is not None:
            return DepthCalibrationResult(alpha=float(manual_alpha), beta=float(manual_beta))

        if len(anchor_relative_depths) == 0 or len(anchor_metric_depths) == 0:
            return DepthCalibrationResult(alpha=1.0, beta=0.0)

        rel = np.array(anchor_relative_depths, dtype=np.float64)
        met = np.array(anchor_metric_depths, dtype=np.float64)

        if manual_alpha is not None:
            beta = float(np.mean(met - manual_alpha * rel))
            return DepthCalibrationResult(alpha=float(manual_alpha), beta=beta)

        if len(rel) >= 2 and float(np.ptp(rel)) > 0.15 and float(np.ptp(met)) >= 1.5:
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

    @staticmethod
    def _assess_estimation_quality(
        *,
        label: FurnitureLabel,
        display_name: str,
        confidence: float,
        z_metric: float,
        was_clamped: bool,
        detection_source: DetectionSource,
        clamped_base: tuple[float, float, float],
        clamped_dims: tuple[float, float, float],
        window_distance_m: float | None = None,
    ) -> tuple[EstimationQuality, bool, str, str]:
        """Score single-photo 3D accuracy and build actionable Korean ArUco corner guidance."""
        reasons: list[str] = []
        if detection_source == "synthetic_contour":
            reasons.append("윤곽선 보조 검출로 추정되어 실제 가구 형태·치수 오차가 클 수 있습니다")
        if confidence < 0.65:
            reasons.append(f"객체 검출 신뢰도({confidence:.0%})가 낮습니다")
        if z_metric > 4.0:
            reasons.append(f"카메라로부터 거리({z_metric:.1f}m)가 멀어 단안 깊이 오차가 커집니다")
        if was_clamped:
            reasons.append("방 경계에 걸쳐 위치·크기가 자동 보정되었습니다")

        if reasons:
            quality: EstimationQuality = (
                "low_needs_aruco"
                if (confidence < 0.65 or z_metric > 4.0 or detection_source == "synthetic_contour")
                else "medium"
            )
            needs_refinement = True
        elif confidence >= 0.80 and z_metric <= 3.0 and not was_clamped:
            quality = "high"
            needs_refinement = False
            reasons.append(
                "단일 사진 기준 추정 신뢰도가 양호하지만 정밀 배치가 필요하면 ArUco 촬영을 권장합니다"
            )
        else:
            quality = "medium"
            needs_refinement = True
            reasons.append("단일 사진 단안 깊이 추정으로 실제 두께·깊이 오차가 있을 수 있습니다")

        label_ko = (
            display_name
            if re.search(r"[가-힣]", display_name)
            else KOREAN_FURNITURE_LABELS.get(label, "가구")
        )
        guidance_dist_m = window_distance_m if window_distance_m is not None else z_metric
        guidance = (
            f"창문에서 약 {guidance_dist_m:.1f}m 거리(동쪽 {clamped_base[0]:.1f}m, 북쪽 {clamped_base[1]:.1f}m)로 "
            f"추정된 '{label_ko}'({clamped_dims[0]:.2f}×{clamped_dims[1]:.2f}×{clamped_dims[2]:.2f}m) 위치로 이동해 주세요. "
            "해당 가구 네 모서리(P1~P4)에서 창문 ArUco 마커를 촬영하면 정확한 위치와 치수로 등록됩니다."
        )
        return quality, needs_refinement, "; ".join(reasons) + ".", guidance

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
        detection_source: DetectionSource = "client_hint",
        window_plane_origin_enu: tuple[float, float, float] | None = None,
        window_inward_normal_enu: tuple[float, float, float] | None = None,
        camera_pose_reliable: bool = False,
    ) -> EstimatedFurnitureResult:
        """Unproject 2D box + calibrated depth into floor-standing 3D cuboid and generate ProtectedRegion."""
        from app.schemas.vision_ai import EstimatedFurnitureResult, FurnitureDiagnostics

        x1, y1, x2, y2 = detection.box_xyxy
        w_px = max(1.0, x2 - x1)
        h_px = max(1.0, y2 - y1)
        u_c = (x1 + x2) / 2.0
        v_c = (y1 + y2) / 2.0

        # Determine metric distance to object and preserve central-60% patch diagnostics.
        depth_region: tuple[int, int, int, int] | None = None
        depth_median: float | None = None
        valid_depth_pixels = 0
        depth_source: Literal["depth_map", "detection_hint", "default"] = "default"
        depth_fallback_reason: str | None = None
        depth_iqr_m: float | None = None
        background_pixel_mixing_risk: Literal["low", "high", "not_evaluated"] = "not_evaluated"
        representative_depth_rule = "central_60_percent_median"
        if depth_map_metric is not None:
            ix1 = int(max(0, min(depth_map_metric.shape[1] - 1, x1 + 0.2 * w_px)))
            ix2 = int(max(0, min(depth_map_metric.shape[1], x2 - 0.2 * w_px)))
            iy1 = int(max(0, min(depth_map_metric.shape[0] - 1, y1 + 0.2 * h_px)))
            iy2 = int(max(0, min(depth_map_metric.shape[0], y2 - 0.2 * h_px)))
            patch = depth_map_metric[iy1:iy2, ix1:ix2]
            depth_region = (ix1, iy1, ix2, iy2)
            if patch.size > 0:
                finite_patch = patch[np.isfinite(patch)]
                valid_depth_pixels = int(finite_patch.size)
                if finite_patch.size > 0:
                    z_metric = float(np.median(finite_patch))
                    depth_median = z_metric
                    depth_iqr_m = float(
                        np.percentile(finite_patch, 75) - np.percentile(finite_patch, 25)
                    )
                    background_pixel_mixing_risk = (
                        "high" if depth_iqr_m > max(0.25, abs(z_metric) * 0.20) else "low"
                    )
                else:
                    z_metric = 2.5
                    depth_fallback_reason = "non_finite_depth_patch"
                depth_source = "depth_map"
            else:
                z_metric = 2.5
                depth_fallback_reason = "empty_depth_patch"
        elif detection.observed_depth_rel is not None:
            z_metric = float(depth_calibration.to_metric_depth(detection.observed_depth_rel))
            depth_source = "detection_hint"
        else:
            z_metric = 2.5
            depth_fallback_reason = "no_depth_map_or_detection_hint"

        z_metric = float(np.clip(z_metric, MIN_METRIC_DEPTH_M, MAX_METRIC_DEPTH_M))

        fx, fy, cx, cy = camera.fx, camera.fy, camera.cx, camera.cy
        T_enu_c = np.array(T_ENU_camera, dtype=np.float64).reshape((4, 4))
        R_enu_c = T_enu_c[0:3, 0:3]
        t_enu_c = T_enu_c[0:3, 3]
        prior = CLASS_GEOMETRIC_PRIORS.get(detection.label, CLASS_GEOMETRIC_PRIORS["other"])

        width_source: Literal["measured", "prior", "clamped"] = "measured"
        depth_dimension_source: Literal["measured", "prior", "clamped"] = "prior"
        height_source: Literal["measured", "prior", "clamped"] = "measured"
        used_pointcloud = False

        # P1-1: 3D ENU point-cloud wall-aligned cuboid fitting when a calibrated depth map exists
        if depth_map_metric is not None and valid_depth_pixels > 0:
            H_d, W_d = depth_map_metric.shape[:2]
            pc_x1 = int(max(0, min(W_d - 1, x1 + 0.10 * w_px)))
            pc_x2 = int(max(pc_x1 + 1, min(W_d, x2 - 0.10 * w_px)))
            pc_y1 = int(max(0, min(H_d - 1, y1 + 0.10 * h_px)))
            pc_y2 = int(max(pc_y1 + 1, min(H_d, y2 - 0.10 * h_px)))
            stride = max(1, min(4, int(min(pc_x2 - pc_x1, pc_y2 - pc_y1) // 16)))

            us = np.arange(pc_x1, pc_x2, stride, dtype=np.float64)
            vs = np.arange(pc_y1, pc_y2, stride, dtype=np.float64)
            uu, vv = np.meshgrid(us, vs)
            zz = depth_map_metric[pc_y1:pc_y2:stride, pc_x1:pc_x2:stride].astype(np.float64)
            valid_mask = np.isfinite(zz) & (zz >= MIN_METRIC_DEPTH_M) & (zz <= MAX_METRIC_DEPTH_M)
            if int(np.sum(valid_mask)) >= 20:
                u_v = uu[valid_mask]
                v_v = vv[valid_mask]
                z_v = zz[valid_mask]
                pts_c = np.stack([(u_v - cx) / fx * z_v, (v_v - cy) / fy * z_v, z_v], axis=1)
                pts_enu = pts_c @ R_enu_c.T + t_enu_c[None, :]

                if detection.label == "chair":
                    z_cut = float(np.percentile(z_v, 65))
                    fg_mask = (
                        (pts_enu[:, 2] >= 0.10)
                        & (pts_enu[:, 2] <= room_height_m - 0.10)
                        & (z_v <= z_cut)
                    )
                elif detection.label == "desk":
                    fg_mask = (pts_enu[:, 2] >= 0.20) & (pts_enu[:, 2] <= 1.30)
                else:
                    fg_mask = (pts_enu[:, 2] >= 0.10) & (pts_enu[:, 2] <= room_height_m - 0.05)

                if int(np.sum(fg_mask)) >= 20:
                    pts_fg = pts_enu[fg_mask]
                    z_fg = z_v[fg_mask]
                    room_yaw_deg = VisionRegistrationPipeline._dominant_room_yaw_deg(
                        room_floor_poly
                    )
                    rad_r = math.radians(room_yaw_deg)
                    cr, sr = math.cos(rad_r), math.sin(rad_r)
                    u_r = pts_fg[:, 0] * cr + pts_fg[:, 1] * sr
                    v_r = -pts_fg[:, 0] * sr + pts_fg[:, 1] * cr

                    u_min = float(np.percentile(u_r, 5))
                    u_max = float(np.percentile(u_r, 95))
                    v_min = float(np.percentile(v_r, 5))
                    v_max = float(np.percentile(v_r, 95))
                    raw_w = u_max - u_min
                    raw_d = v_max - v_min

                    if max(raw_w, raw_d) >= 0.15:
                        width_m = max(0.2, min(5.0, raw_w))
                        depth_m = max(0.2, min(3.5, raw_d))
                        width_source = "clamped" if not math.isclose(width_m, raw_w) else "measured"
                        depth_dimension_source = (
                            "clamped" if not math.isclose(depth_m, raw_d) else "measured"
                        )
                        z_top = float(np.percentile(pts_fg[:, 2], 90))
                        unconstrained_h = z_top
                        if detection.label in ("desk", "chair", "bookshelf"):
                            min_h = max(0.25, prior["min_height"] * 0.75)
                            max_h = min(room_height_m * 0.95, prior["max_height"])
                        else:
                            min_h = 0.25
                            max_h = room_height_m * 0.95
                        height_m = max(min_h, min(max_h, z_top))
                        height_source = (
                            "clamped" if not math.isclose(height_m, unconstrained_h) else "measured"
                        )
                        u_mid = 0.5 * (u_min + u_max)
                        v_mid = 0.5 * (v_min + v_max)
                        center_x = float(u_mid * cr - v_mid * sr)
                        center_y = float(u_mid * sr + v_mid * cr)
                        yaw_deg = room_yaw_deg
                        z_metric = float(
                            np.clip(float(np.median(z_fg)), MIN_METRIC_DEPTH_M, MAX_METRIC_DEPTH_M)
                        )
                        representative_depth_rule = "enu_pointcloud_bbox"
                        used_pointcloud = True

        if not used_pointcloud:
            # Scalar-Z back-projection fallback (when depth_map is absent or degenerate)
            if depth_map_metric is not None and valid_depth_pixels > 0:
                H_d, W_d = depth_map_metric.shape[:2]
                fx1 = int(max(0, min(W_d - 1, x1)))
                fx2 = int(max(fx1 + 1, min(W_d, x2)))
                fy1 = int(max(0, min(H_d - 1, y1)))
                fy2 = int(max(fy1 + 1, min(H_d, y2)))
                full_patch = depth_map_metric[fy1:fy2, fx1:fx2]
                full_finite = full_patch[np.isfinite(full_patch)]
                if full_finite.size > 0 and depth_fallback_reason is None:
                    z_p25 = float(np.percentile(full_finite, 25))
                    if math.isfinite(z_p25):
                        z_metric = float(np.clip(z_p25, MIN_METRIC_DEPTH_M, MAX_METRIC_DEPTH_M))

            p_left_c = np.array([(x1 - cx) / fx * z_metric, (v_c - cy) / fy * z_metric, z_metric])
            p_right_c = np.array([(x2 - cx) / fx * z_metric, (v_c - cy) / fy * z_metric, z_metric])
            p_top_c = np.array([(u_c - cx) / fx * z_metric, (y1 - cy) / fy * z_metric, z_metric])
            p_bot_c = np.array([(u_c - cx) / fx * z_metric, (y2 - cy) / fy * z_metric, z_metric])

            p_left_enu = R_enu_c @ p_left_c + t_enu_c
            p_right_enu = R_enu_c @ p_right_c + t_enu_c
            p_top_enu = R_enu_c @ p_top_c + t_enu_c
            p_bot_enu = R_enu_c @ p_bot_c + t_enu_c

            lateral_vec = np.array([p_right_enu[0] - p_left_enu[0], p_right_enu[1] - p_left_enu[1]])
            measured_width = float(np.linalg.norm(lateral_vec))
            width_m = max(0.2, min(5.0, measured_width)) if measured_width > 0.1 else 0.8
            width_source = "measured"
            if measured_width <= 0.1:
                width_source = "prior"
            elif not math.isclose(width_m, measured_width):
                width_source = "clamped"

            yaw_rad = math.atan2(float(lateral_vec[1]), float(lateral_vec[0]))
            yaw_deg = math.degrees(yaw_rad) % 360.0

            rad = math.radians(yaw_deg)
            c_yaw, s_yaw = math.cos(rad), math.sin(rad)
            v_vec = np.array([-s_yaw, c_yaw, 0.0])

            if detection.depth_span_rel is not None:
                measured_depth = float(detection.depth_span_rel * depth_calibration.alpha)
                if 0.2 <= measured_depth <= 2.5:
                    depth_m = measured_depth
                    depth_dimension_source = "measured"
                else:
                    depth_m = prior["depth"]
                    depth_dimension_source = "prior"
            else:
                depth_m = prior["depth"]
                depth_dimension_source = "prior"

            measured_height = float(p_top_enu[2] - p_bot_enu[2])
            floor_top_height = float(p_top_enu[2])
            # P1-2: Prefer floor-referenced top height for floor-standing furniture when pose is reliable
            if (
                camera_pose_reliable
                and detection.label in ("desk", "chair", "bookshelf")
                and prior["min_height"] <= floor_top_height <= prior["max_height"]
            ):
                height_m = floor_top_height
                height_source = "measured"
            elif prior["min_height"] <= measured_height <= prior["max_height"]:
                height_m = measured_height
                height_source = "measured"
            elif floor_top_height > 0.3:
                height_m = float(min(floor_top_height, room_height_m * 0.95))
                height_source = "measured"
            else:
                height_m = prior["height"]
                height_source = "prior"
            unconstrained_height_m = height_m
            height_m = max(0.1, min(room_height_m * 0.98, height_m))
            if not math.isclose(height_m, unconstrained_height_m):
                height_source = "clamped"

            front_center = 0.5 * (p_left_enu + p_right_enu)
            view_dir_xy = np.array(
                [front_center[0] - t_enu_c[0], front_center[1] - t_enu_c[1], 0.0]
            )
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
        dimension_sources: dict[str, Literal["measured", "prior", "clamped"]] = {
            "width": "clamped" if was_clamped and clamped_dims[0] != width_m else width_source,
            "depth": "clamped"
            if was_clamped and clamped_dims[1] != depth_m
            else depth_dimension_source,
            "height": "clamped" if was_clamped and clamped_dims[2] != height_m else height_source,
            "position": "clamped" if was_clamped and clamped_base != base_center else "measured",
        }
        clamp_changes: list[str] = []
        if clamped_base != base_center:
            clamp_changes.append("position_outside_room")
        if clamped_dims != dimensions:
            clamp_changes.append("dimensions_exceed_room")
        room_clamp_reason = ",".join(clamp_changes) if clamp_changes else None

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

        cx_c, cy_c, cz_c = clamped_base
        w_c, d_c, h_c = clamped_dims
        rad_c = math.radians(yaw_deg)
        cu, su = math.cos(rad_c), math.sin(rad_c)
        estimated_corners = [
            (
                round(cx_c + sx * 0.5 * w_c * cu - sy * 0.5 * d_c * su, 3),
                round(cy_c + sx * 0.5 * w_c * su + sy * 0.5 * d_c * cu, 3),
            )
            for sx, sy in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
        ]

        window_distance_m: float | None = None
        if window_plane_origin_enu is not None and window_inward_normal_enu is not None:
            orig = np.array(window_plane_origin_enu, dtype=np.float64)
            norm = np.array(window_inward_normal_enu, dtype=np.float64)
            corner_dists = [
                max(0.0, float(np.dot(np.array([ex, ey, 0.0]) - orig, norm)))
                for ex, ey in estimated_corners
            ]
            window_distance_m = min(corner_dists) if corner_dists else None

        protected_region: ProtectedRegion | None = None
        if auto_create_protected_regions:
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

        quality, needs_refinement, reason_ko, guidance_ko = (
            VisionRegistrationPipeline._assess_estimation_quality(
                label=detection.label,
                display_name=display_name,
                confidence=detection.confidence,
                z_metric=z_metric,
                was_clamped=was_clamped,
                detection_source=detection_source,
                clamped_base=clamped_base,
                clamped_dims=clamped_dims,
                window_distance_m=window_distance_m,
            )
        )

        return EstimatedFurnitureResult(
            occluder=occluder,
            protected_region=protected_region,
            confidence=detection.confidence,
            calibrated_distance_m=z_metric,
            clamped_to_room=was_clamped,
            detection_source=detection_source,
            estimation_quality=quality,
            needs_aruco_refinement=needs_refinement,
            uncertainty_reason_ko=reason_ko,
            aruco_capture_guidance_ko=guidance_ko,
            recommended_capture_shape="rectangle",
            estimated_corners_enu_m=estimated_corners,
            diagnostics=FurnitureDiagnostics(
                box_xyxy_px=detection.box_xyxy,
                representative_depth_rule=representative_depth_rule,
                representative_depth_region_xyxy_px=depth_region,
                representative_depth_median_m=depth_median,
                valid_depth_pixel_count=valid_depth_pixels,
                depth_source=depth_source,
                depth_fallback_reason=depth_fallback_reason,
                depth_iqr_m=depth_iqr_m,
                background_pixel_mixing_risk=background_pixel_mixing_risk,
                dimension_sources=dimension_sources,
                prior_dimensions_m=(0.8, prior["depth"], prior["height"]),
                unclamped_base_center_enu_m=base_center,
                unclamped_dimensions_m=dimensions,
                room_clamp_reason=room_clamp_reason,
            ),
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
        from app.schemas.vision_ai import (
            ArucoDiagnostics,
            CameraIntrinsicsInput,
            DepthCalibrationDiagnostics,
            Detection2DInput,
            VisionDiagnostics,
            VisionModelDiagnostics,
        )

        exif_fx: float | None = None
        if image_rgb is None and request.image_base64 is not None:
            image_rgb, exif_fx = self.decode_image_base64_with_exif(request.image_base64)

        room_floor_poly = Polygon(room_floor_vertices)
        camera = request.camera
        is_default_camera = (
            camera.width == 1280
            and camera.height == 720
            and math.isclose(camera.fx, 900.0)
            and math.isclose(camera.fy, 900.0)
            and math.isclose(camera.cx, 640.0)
            and math.isclose(camera.cy, 360.0)
        )
        intrinsics_source = "request_camera"
        can_search_focal = False
        if image_rgb is not None:
            h_img, w_img = image_rgb.shape[:2]
            if request.focal_length_35mm is not None:
                focal = float(max(w_img, h_img)) * (request.focal_length_35mm / 36.0)
                camera = CameraIntrinsicsInput(
                    width=w_img,
                    height=h_img,
                    fx=focal,
                    fy=focal,
                    cx=w_img / 2.0,
                    cy=h_img / 2.0,
                )
                intrinsics_source = "request_focal_35mm"
            elif request.fov_deg is not None:
                focal = (float(max(w_img, h_img)) / 2.0) / math.tan(
                    math.radians(request.fov_deg / 2.0)
                )
                camera = CameraIntrinsicsInput(
                    width=w_img,
                    height=h_img,
                    fx=focal,
                    fy=focal,
                    cx=w_img / 2.0,
                    cy=h_img / 2.0,
                )
                intrinsics_source = "request_fov_deg"
            elif is_default_camera and exif_fx is not None:
                camera = CameraIntrinsicsInput(
                    width=w_img,
                    height=h_img,
                    fx=exif_fx,
                    fy=exif_fx,
                    cx=w_img / 2.0,
                    cy=h_img / 2.0,
                )
                intrinsics_source = "exif_35mm"
            elif is_default_camera and (w_img, h_img) != (1280, 720):
                focal = float(max(w_img, h_img)) * 0.68
                camera = CameraIntrinsicsInput(
                    width=w_img,
                    height=h_img,
                    fx=focal,
                    fy=focal,
                    cx=w_img / 2.0,
                    cy=h_img / 2.0,
                )
                intrinsics_source = "default_1x_prior"
                can_search_focal = True

        marker_corners = request.marker_anchor.corners_px
        if marker_corners is None and request.T_ENU_camera is None and image_rgb is not None:
            marker_corners = self._detect_aruco_corners(image_rgb, request.marker_anchor.code_id)

        marker_anchor = (
            request.marker_anchor.model_copy(update={"corners_px": marker_corners})
            if marker_corners is not None and request.marker_anchor.corners_px is None
            else request.marker_anchor
        )

        pose_res = self.resolve_camera_pose_from_aruco(
            camera=camera,
            marker_anchor=marker_anchor,
            T_ENU_marker=T_ENU_marker,
            T_ENU_camera_presolved=request.T_ENU_camera,
            image_rgb=image_rgb,
        )

        # P0-2 fallback: if EXIF focal length was stripped, refine fx via 1D IPPE search
        if (
            can_search_focal
            and request.T_ENU_camera is None
            and marker_corners is not None
            and image_rgb is not None
        ):
            cam_x, cam_y, cam_z = pose_res.camera_position_enu_m
            cam_in_room = room_floor_poly.buffer(0.25).covers(Point(cam_x, cam_y)) and (
                -0.25 <= cam_z <= room_height_m + 0.25
            )
            reproj_high = (
                pose_res.reprojection_error_mean_px is not None
                and pose_res.reprojection_error_mean_px > 2.5
            )
            if not cam_in_room or reproj_high:
                h_img, w_img = image_rgb.shape[:2]
                fx_opt = self._search_optimal_focal_length(
                    marker_corners=marker_corners,
                    side_length_m=marker_anchor.side_length_m,
                    T_ENU_marker=T_ENU_marker,
                    width=w_img,
                    height=h_img,
                    room_floor_poly=room_floor_poly,
                    room_height_m=room_height_m,
                )
                camera = CameraIntrinsicsInput(
                    width=w_img,
                    height=h_img,
                    fx=fx_opt,
                    fy=fx_opt,
                    cx=w_img / 2.0,
                    cy=h_img / 2.0,
                )
                pose_res = self.resolve_camera_pose_from_aruco(
                    camera=camera,
                    marker_anchor=marker_anchor,
                    T_ENU_marker=T_ENU_marker,
                    T_ENU_camera_presolved=None,
                    image_rgb=image_rgb,
                )
                intrinsics_source = "pnp_1d_focal_search"

        active_detections = list(request.detections)
        rel_depth_map: np.ndarray | None = None
        detection_source: DetectionSource = "client_hint"
        if len(active_detections) == 0 and image_rgb is not None:
            exclude_boxes: list[tuple[float, float, float, float]] = []
            if marker_corners is not None:
                xs = [pt[0] for pt in marker_corners]
                ys = [pt[1] for pt in marker_corners]
                exclude_boxes.append((min(xs), min(ys), max(xs), max(ys)))

            if isinstance(self.detector, CompositeFurnitureDetector):
                raw_detected = self.detector.detect(
                    image_rgb,
                    exclude_boxes=exclude_boxes or None,
                )
                detection_source = self.detector.last_source
            elif isinstance(self.detector, GeometricSyntheticDetector):
                raw_detected = self.detector.detect(
                    image_rgb,
                    exclude_boxes=exclude_boxes or None,
                )
                detection_source = "synthetic_contour"
            else:
                raw_detected = self.detector.detect(image_rgb)
                if exclude_boxes:
                    from engine.vision_ai.protocols import _overlaps_any_box

                    raw_detected = [
                        d
                        for d in raw_detected
                        if not _overlaps_any_box(
                            d.box_xyxy,
                            exclude_boxes,
                            exclude_container=(d.label == "other" or d.raw_class_name == "tv"),
                        )
                    ]
                detection_source = "yolov8"

            if len(raw_detected) == 0:
                raise ValueError("No furniture objects detected in the provided image.")

            for det_obj in raw_detected:
                grade: Literal["B", "C", "D"] = (
                    "B"
                    if det_obj.label == "bookshelf"
                    else ("C" if det_obj.label == "desk" else "D")
                )
                active_detections.append(
                    Detection2DInput(
                        box_xyxy=det_obj.box_xyxy,
                        label=det_obj.label,
                        confidence=float(min(max(det_obj.confidence, 0.0), 1.0)),
                        name=None,
                        protection_grade=grade,
                    )
                )

        # P0-4: Estimate depth whenever image_rgb is available and any detection needs depth
        needs_depth_map = (
            depth_map is None
            and image_rgb is not None
            and (
                len(request.detections) == 0
                or any(det.observed_depth_rel is None for det in active_detections)
            )
        )
        if needs_depth_map and image_rgb is not None:
            rel_depth_map = self.depth_estimator.estimate_relative_depth(
                image_rgb, camera.width, camera.height
            )

        if rel_depth_map is not None and marker_corners is not None:
            anchor_rel = [
                float(
                    rel_depth_map[
                        int(min(max(0, round(py)), rel_depth_map.shape[0] - 1)),
                        int(min(max(0, round(px)), rel_depth_map.shape[1] - 1)),
                    ]
                )
                for px, py in marker_corners
            ]
            # P0-1: Use robust inner-60% marker median + proportional scaling for single planar marker
            marker_met = pose_res.marker_corner_depths_metric
            interior_rel = self._sample_marker_interior_depth(rel_depth_map, marker_corners)
            if (
                interior_rel is not None
                and request.depth_scale_alpha is None
                and request.depth_shift_beta is None
                and len(marker_met) > 0
                and float(np.ptp(np.asarray(marker_met, dtype=np.float64))) < 1.5
            ):
                calib_rel: Sequence[float] = [interior_rel]
                calib_met: Sequence[float] = [float(np.mean(marker_met))]
            else:
                calib_rel = anchor_rel
                calib_met = marker_met
        else:
            anchor_rel = [request.marker_anchor.anchor_depth_rel] * len(
                pose_res.marker_corner_depths_metric
            )
            calib_rel = anchor_rel
            calib_met = pose_res.marker_corner_depths_metric

        depth_calib = self.calibrate_monocular_depth(
            anchor_relative_depths=calib_rel,
            anchor_metric_depths=calib_met,
            manual_alpha=request.depth_scale_alpha,
            manual_beta=request.depth_shift_beta,
        )

        if depth_map is None and rel_depth_map is not None:
            depth_map = np.clip(
                depth_calib.alpha * rel_depth_map + depth_calib.beta,
                MIN_METRIC_DEPTH_M,
                MAX_METRIC_DEPTH_M,
            )

        depth_runtime_source: Literal[
            "depth_anything_v2", "synthetic_geometric", "provided_metric_map"
        ]
        if depth_map is not None and rel_depth_map is None:
            depth_runtime_source = "provided_metric_map"
        elif isinstance(self.depth_estimator, CompositeDepthEstimator):
            depth_runtime_source = self.depth_estimator.last_source
        else:
            depth_runtime_source = "depth_anything_v2"

        if anchor_rel:
            residuals = np.asarray(pose_res.marker_corner_depths_metric, dtype=np.float64) - (
                depth_calib.alpha * np.asarray(anchor_rel, dtype=np.float64) + depth_calib.beta
            )
            residual_rmse = float(np.sqrt(np.mean(np.square(residuals))))
        else:
            residual_rmse = None

        T_enu_m_arr = np.array(T_ENU_marker, dtype=np.float64).reshape((4, 4))
        window_origin_enu = (
            float(T_enu_m_arr[0, 3]),
            float(T_enu_m_arr[1, 3]),
            float(T_enu_m_arr[2, 3]),
        )
        window_normal_enu = (
            float(T_enu_m_arr[0, 2]),
            float(T_enu_m_arr[1, 2]),
            float(T_enu_m_arr[2, 2]),
        )
        cam_px, cam_py, cam_pz = pose_res.camera_position_enu_m
        camera_pose_reliable = bool(
            room_floor_poly.buffer(0.25).covers(Point(cam_px, cam_py))
            and (-0.25 <= cam_pz <= room_height_m + 0.25)
            and (
                pose_res.reprojection_error_mean_px is None
                or pose_res.reprojection_error_mean_px <= 2.5
            )
        )
        room_gate_poly = room_floor_poly.buffer(0.25)

        estimated_items: list[EstimatedFurnitureResult] = []
        used_ids = set(existing_ids or ())

        for idx, det in enumerate(active_detections):
            counter = idx + 1
            occ_id = f"occluder_{det.label}_{counter}"
            while occ_id in used_ids or f"region_{occ_id}" in used_ids:
                counter += 1
                occ_id = f"occluder_{det.label}_{counter}"

            res = self.backproject_box_to_3d_cuboid(
                detection=det,
                camera=camera,
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
                detection_source=detection_source,
                window_plane_origin_enu=window_origin_enu,
                window_inward_normal_enu=window_normal_enu,
                camera_pose_reliable=camera_pose_reliable,
            )

            # P0-3: Reject automatic real-photo detections whose unclamped 3D center lies outside the room
            if (
                detection_source == "yolov8"
                and depth_runtime_source != "synthetic_geometric"
                and res.diagnostics is not None
            ):
                ux, uy, _ = res.diagnostics.unclamped_base_center_enu_m
                if not room_gate_poly.covers(Point(ux, uy)):
                    continue

            used_ids.add(occ_id)
            used_ids.add(f"region_{occ_id}")
            estimated_items.append(res)

        self.last_diagnostics = VisionDiagnostics(
            model=VisionModelDiagnostics(
                request_mode=request.mode,
                detector_source=detection_source,
                depth_source=depth_runtime_source,
                yolo_adapter_selected=isinstance(self.detector, CompositeFurnitureDetector)
                and self.detector._primary is not None,
                depth_anything_adapter_selected=isinstance(
                    self.depth_estimator, CompositeDepthEstimator
                )
                and self.depth_estimator._primary is not None,
            ),
            aruco=ArucoDiagnostics(
                pose_method=pose_res.pose_method,
                marker_corners_px=marker_corners,
                reprojection_error_mean_px=pose_res.reprojection_error_mean_px,
                reprojection_error_max_px=pose_res.reprojection_error_max_px,
                resolved_fx_px=round(float(camera.fx), 2),
                intrinsics_source=intrinsics_source,
            ),
            depth=DepthCalibrationDiagnostics(
                anchor_relative_depths=[float(value) for value in anchor_rel],
                anchor_metric_depths_m=[
                    float(value) for value in pose_res.marker_corner_depths_metric
                ],
                alpha=depth_calib.alpha,
                beta=depth_calib.beta,
                residual_rmse_m=residual_rmse,
            ),
        )

        return pose_res, depth_calib, estimated_items
