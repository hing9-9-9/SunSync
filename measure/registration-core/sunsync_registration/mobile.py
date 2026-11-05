"""Native capture ingestion. Missing device calibration is never treated as zero distortion."""

import base64
from io import BytesIO
from typing import Annotated, Literal

import cv2
import numpy as np
from PIL import Image
from pydantic import AwareDatetime, Field, model_validator

from .models import Board, Camera, Detection, Strict
from .pose import estimate


class AndroidCalibration(Strict):
    kind: Literal["android_camera2"]
    # Reference pixels: preCorrectionActiveArraySize, with its top-left at (0,0).
    intrinsic: tuple[float, float, float, float, float]
    distortion: tuple[float, float, float, float, float]
    reference_width: int = Field(gt=0, le=30000)
    reference_height: int = Field(gt=0, le=30000)
    crop: tuple[float, float, float, float]  # x, y, width, height in reference pixels
    distortion_correction: Literal["off"]
    stabilization: Literal["off"]


class AppleCalibration(Strict):
    kind: Literal["ios_avfoundation"]
    intrinsic: list[float] = Field(min_length=9, max_length=9)  # row-major
    reference_width: int = Field(gt=0, le=30000)
    reference_height: int = Field(gt=0, le=30000)
    distortion_center: tuple[float, float]
    # AVCameraCalibrationData.lensDistortionLookupTable (NOT the inverse table).
    distortion_lookup: list[float] = Field(min_length=2, max_length=4096)
    full_reference_frame: Literal[True]


Calibration = Annotated[
    AndroidCalibration | AppleCalibration, Field(discriminator="kind")
]


class NativeFrame(Strict):
    capture_id: str = Field(min_length=1, max_length=100)
    point_id: str = Field(min_length=1, max_length=30)
    captured_at: AwareDatetime
    # A new stream_id is mandatory on lens, zoom, focus, crop or output-size changes.
    stream_id: str = Field(min_length=1, max_length=150)
    lens_id: str = Field(min_length=1, max_length=100)
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    pixel_rotation_degrees: Literal[0]
    mirrored: Literal[False]
    image_base64: str = Field(min_length=1, max_length=12_000_000)
    board: Board
    calibration: Calibration | None = None

    @model_validator(mode="after")
    def size_limit(self):
        if self.width * self.height > 8_000_000:
            raise ValueError("Native frames must be at most 8 megapixels")
        return self


def decode_frame(frame: NativeFrame):
    raw = base64.b64decode(frame.image_base64, validate=True)
    try:
        with Image.open(BytesIO(raw)) as header:
            if header.format not in ("JPEG", "PNG") or header.size != (
                frame.width,
                frame.height,
            ):
                raise ValueError(
                    "Native encoded pixels disagree with capture dimensions"
                )
            header.verify()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Invalid native image") from error
    pixels = cv2.imdecode(
        np.frombuffer(raw, np.uint8),
        cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION,
    )
    if pixels is None or pixels.shape != (frame.height, frame.width):
        raise ValueError("Invalid native capture")
    return pixels


def detect_corners(board: Board, pixels):
    params = cv2.aruco.DetectorParameters()
    params.markerBorderBits = board.border_bits
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params
    )
    corners, ids, _ = detector.detectMarkers(pixels)
    wanted = board.objects()
    result = [
        Detection(marker_id=int(code), corners_px=c.reshape(4, 2).tolist())
        for c, code in zip(corners, ids.ravel() if ids is not None else [])
        if int(code) in wanted
    ]
    if len({item.marker_id for item in result}) != len(result):
        raise ValueError("Duplicate board marker IDs")
    return result


def normalize_camera(frame: NativeFrame, pixels):
    metadata = frame.calibration
    if metadata is None:
        return None, pixels
    if isinstance(metadata, AndroidCalibration):
        fx, fy, cx, cy, skew = metadata.intrinsic
        if abs(skew) > 1e-6:
            raise ValueError("Nonzero sensor skew requires guided automatic setup")
        x, y, width, height = metadata.crop
        if (
            min(x, y) < 0
            or min(width, height) <= 0
            or x + width > metadata.reference_width
            or y + height > metadata.reference_height
        ):
            raise ValueError("Invalid sensor crop")
        # Camera2 scales a centered, aspect-ratio-preserving crop for each output stream.
        aspect = frame.width / frame.height
        if width / height > aspect:
            cropped = height * aspect
            x += (width - cropped) / 2
            width = cropped
        else:
            cropped = width / aspect
            y += (height - cropped) / 2
            height = cropped
        sx, sy = frame.width / width, frame.height / height
        fx, fy, cx, cy = fx * sx, fy * sy, (cx - x) * sx, (cy - y) * sy
        k1, k2, k3, p1, p2 = metadata.distortion
        distortion = [k1, k2, p1, p2, k3]
        pixel_space = "raw_opencv"
    else:
        k = np.array(metadata.intrinsic).reshape(3, 3)
        if (
            not np.allclose(k[2], [0, 0, 1])
            or abs(k[0, 1]) > 1e-6
            or abs(k[1, 0]) > 1e-6
        ):
            raise ValueError("Invalid native intrinsic matrix")
        sx, sy = (
            frame.width / metadata.reference_width,
            frame.height / metadata.reference_height,
        )
        if not np.isclose(sx, sy, rtol=0.002):
            raise ValueError(
                "Apple image must preserve the full calibration reference frame"
            )
        fx, fy, cx, cy = k[0, 0] * sx, k[1, 1] * sy, k[0, 2] * sx, k[1, 2] * sy
        ox, oy = metadata.distortion_center
        if not (
            0 <= ox < metadata.reference_width and 0 <= oy < metadata.reference_height
        ):
            raise ValueError("Distortion centre outside reference image")
        # Destination (rectilinear) pixel -> distorted source sample, per Apple's LUT.
        radius = np.hypot(
            max(ox, metadata.reference_width - ox),
            max(oy, metadata.reference_height - oy),
        )
        yy, xx = np.indices((frame.height, frame.width), dtype=np.float32)
        dx, dy = xx / sx - ox, yy / sy - oy
        position = np.hypot(dx, dy) / radius * (len(metadata.distortion_lookup) - 1)
        magnification = np.interp(
            position,
            np.arange(len(metadata.distortion_lookup)),
            metadata.distortion_lookup,
        )
        if np.any(magnification <= -1) or np.any(abs(magnification) > 2):
            raise ValueError("Invalid lens distortion lookup")
        map_x = ((ox + dx * (1 + magnification)) * sx).astype(np.float32)
        map_y = ((oy + dy * (1 + magnification)) * sy).astype(np.float32)
        pixels = cv2.remap(
            pixels,
            map_x,
            map_y,
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        distortion, pixel_space = [], "rectified"
    camera = Camera(
        width=frame.width,
        height=frame.height,
        fx=fx,
        fy=fy,
        cx=cx,
        cy=cy,
        distortion=distortion,
        pixel_space=pixel_space,
        source=metadata.kind,
        lens_id=frame.lens_id,
    )
    return camera, pixels


def ingest_frame(frame: NativeFrame):
    camera, pixels = normalize_camera(frame, decode_frame(frame))
    found = detect_corners(frame.board, pixels)
    common = {
        "capture_id": frame.capture_id,
        "point_id": frame.point_id,
        "captured_at": frame.captured_at.isoformat(),
        "stream_id": frame.stream_id,
        "lens_id": frame.lens_id,
        "width": frame.width,
        "height": frame.height,
        "detections": [entry.model_dump(mode="json") for entry in found],
    }
    if camera is None:
        return {
            **common,
            "status": "automatic_setup_required",
            "camera": None,
            "message": "마커 전체가 보이도록 휴대폰을 여러 각도로 천천히 움직여 주세요.",
        }
    return {
        **common,
        "status": "camera_ready",
        "camera": camera.model_dump(mode="json"),
        "pose": estimate(frame.board, camera, found),
    }


class SetupView(Strict):
    capture_id: str
    stream_id: str
    lens_id: str
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    detections: list[Detection] = Field(min_length=4, max_length=4)


class AutomaticSetup(Strict):
    board: Board
    views: list[SetupView] = Field(min_length=12, max_length=30)


def calibrate_automatically(request: AutomaticSetup):
    views = request.views
    first = views[0]
    identity = lambda v: (v.stream_id, v.lens_id, v.width, v.height)
    if any(identity(v) != identity(first) for v in views) or len(
        {v.capture_id for v in views}
    ) != len(views):
        raise ValueError(
            "Automatic setup requires unique captures from one unchanged camera stream"
        )
    objects, images, signatures = [], [], []
    board = request.board.objects()
    for view in views:
        found = {
            entry.marker_id: np.array(entry.corners_px, np.float32)
            for entry in view.detections
        }
        if len(found) != 4 or set(found) != set(board):
            raise ValueError("All four board markers are required for automatic setup")
        points = np.concatenate([found[key] for key in sorted(board)])
        if (
            np.any(points < 0)
            or np.any(points[:, 0] >= first.width)
            or np.any(points[:, 1] >= first.height)
        ):
            raise ValueError("Setup corners outside image")
        if any(np.sqrt(np.mean((points - prior) ** 2)) < 5 for prior in signatures):
            raise ValueError(
                "Move the phone: repeated viewpoints do not calibrate the camera"
            )
        signatures.append(points)
        objects.append(
            np.concatenate([board[key] for key in sorted(board)]).astype(np.float32)
        )
        images.append(points)
    coverage = np.concatenate(images)
    if (
        np.ptp(coverage[:, 0]) < first.width * 0.5
        or np.ptp(coverage[:, 1]) < first.height * 0.5
    ):
        raise ValueError("Move the marker view across more of the screen")
    # Hold out every fourth view to avoid accepting a model solely on training residuals.
    train = [i for i in range(len(views)) if i % 4]
    rms, k, d, rs, ts, std, _, errors = cv2.calibrateCameraExtended(
        [objects[i] for i in train],
        [images[i] for i in train],
        (first.width, first.height),
        None,
        None,
        flags=cv2.CALIB_FIX_K3,
    )
    if (
        not np.isfinite(k).all()
        or not np.isfinite(d).all()
        or rms > 1.5
        or max(errors.ravel()) > 2.0
    ):
        raise ValueError("Camera setup is unstable; capture clearer, more varied views")
    normals = np.array([cv2.Rodrigues(r)[0][:, 2] for r in rs])
    if min(np.ptp(normals[:, :2], axis=0)) < 0.15 or np.any(
        std.ravel()[:2] / [k[0, 0], k[1, 1]] > 0.03
    ):
        raise ValueError("Tilt the phone in both directions for automatic setup")
    for i in range(len(views)):
        if i % 4:
            continue
        ok, r, t = cv2.solvePnP(objects[i], images[i], k, d)
        projected = cv2.projectPoints(objects[i], r, t, k, d)[0].reshape(-1, 2)
        if not ok or np.sqrt(np.mean(np.sum((projected - images[i]) ** 2, axis=1))) > 2:
            raise ValueError("Camera setup failed independent-view validation")
    camera = Camera(
        width=first.width,
        height=first.height,
        fx=k[0, 0],
        fy=k[1, 1],
        cx=k[0, 2],
        cy=k[1, 2],
        distortion=d.ravel().tolist(),
        pixel_space="raw_opencv",
        source="guided_board_setup",
        lens_id=first.lens_id,
    )
    if (
        not 0.2 * first.width < camera.fx < 5 * first.width
        or not 0.2 * first.height < camera.fy < 5 * first.height
    ):
        raise ValueError("Camera setup outside supported optical range")
    return {
        "status": "camera_ready",
        "stream_id": first.stream_id,
        "camera": camera.model_dump(mode="json"),
        "reprojection_rmse_px": float(rms),
        "metric_accuracy_verified": False,
    }
