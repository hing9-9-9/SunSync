import base64
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from .models import Board, Camera, Detection, DetectRequest


def detect(request: DetectRequest):
    try:
        raw = base64.b64decode(request.image_base64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid image base64") from exc
    try:
        with Image.open(BytesIO(raw)) as header:
            if header.format not in ("JPEG", "PNG"):
                raise ValueError("Only JPEG and PNG are accepted")
            if list(header.size) != [request.camera.width, request.camera.height]:
                raise ValueError("Encoded pixel dimensions do not match camera")
            header.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or oversized image") from exc
    image = cv2.imdecode(
        np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
    )
    if image is None:
        raise ValueError("Image decode failed")
    if [image.shape[1], image.shape[0]] != [
        request.camera.width,
        request.camera.height,
    ]:
        raise ValueError(
            "Decoded pixel dimensions do not match camera; EXIF is deliberately ignored"
        )
    params = cv2.aruco.DetectorParameters()
    params.markerBorderBits = request.board.border_bits
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params
    )
    corners, ids, _ = detector.detectMarkers(image)
    wanted = request.board.objects()
    found = [
        Detection(marker_id=int(i), corners_px=c.reshape(4, 2).tolist())
        for c, i in zip(corners, ids.ravel() if ids is not None else [])
        if int(i) in wanted
    ]
    # Reject duplicate board identities instead of choosing a screen by apparent size.
    if len({f.marker_id for f in found}) != len(found):
        raise ValueError(
            "Duplicate target marker IDs; hide other boards or use an app-provided ROI with matching intrinsics"
        )
    return {
        "detections": [v.model_dump() for v in found],
        "pose": estimate(request.board, request.camera, found),
    }


def estimate(board: Board, camera: Camera, detections: list[Detection], max_rmse=2.0):
    objmap = board.objects()
    found = {}
    for entry in detections:
        if entry.marker_id not in objmap:
            continue
        if entry.marker_id in found:
            raise ValueError("Duplicate target marker ID")
        pixels = np.array(entry.corners_px, float)
        if (
            np.any(pixels < 0)
            or np.any(pixels[:, 0] >= camera.width)
            or np.any(pixels[:, 1] >= camera.height)
        ):
            raise ValueError("Marker corners outside declared image")
        if abs(cv2.contourArea(pixels.astype(np.float32))) < 16:
            raise ValueError("Marker area too small")
        found[entry.marker_id] = pixels
    if len(found) < (1 if len(objmap) == 1 else 2):
        return {"status": "insufficient_markers", "detected_ids": sorted(found)}
    ids = sorted(found)
    obj = np.concatenate([objmap[i] for i in ids])
    pixels = np.concatenate([found[i] for i in ids])
    k, d = camera.matrices()
    _, rs, ts, _ = cv2.solvePnPGeneric(obj, pixels, k, d, flags=cv2.SOLVEPNP_IPPE)
    candidates = []
    for r, t in zip(rs, ts):
        rot, _ = cv2.Rodrigues(r)
        if np.min((obj @ rot.T + t.reshape(3))[:, 2]) <= 0:
            continue
        projected, _ = cv2.projectPoints(obj, r, t, k, d)
        error = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - pixels) ** 2, axis=1))))
        candidates.append((error, r, t, rot))
    candidates.sort(key=lambda a: a[0])
    if not candidates:
        return {"status": "pnp_failed"}
    initial = [v[0] for v in candidates]
    if len(candidates) > 1 and initial[1] - initial[0] < 0.05:
        angle = np.degrees(
            np.arccos(np.clip((np.trace(candidates[0][3].T @ candidates[1][3]) - 1) / 2, -1, 1))
        )
        if angle > 3:
            return {"status": "planar_pose_ambiguous", "candidate_rmse_px": initial}
    r, t = cv2.solvePnPRefineLM(obj, pixels, k, d, candidates[0][1].copy(), candidates[0][2].copy())
    rot, _ = cv2.Rodrigues(r)
    projected, _ = cv2.projectPoints(obj, r, t, k, d)
    rmse = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - pixels) ** 2, axis=1))))
    if np.min((obj @ rot.T + t.reshape(3))[:, 2]) <= 0 or rmse > max_rmse:
        return {"status": "reprojection_error", "reprojection_rmse_px": rmse}
    transform = np.eye(4)
    transform[:3, :3] = rot.T
    transform[:3, 3] = -rot.T @ t.reshape(3)
    uncertainty = pose_uncertainty(obj, r, t, k, d, max(0.5, rmse))
    return {
        **uncertainty,
        "status": "ok",
        "T_board_camera": transform.ravel().tolist(),
        "camera_position_board_m": transform[:3, 3].tolist(),
        "reprojection_rmse_px": rmse,
        "candidate_rmse_px": initial,
        "detected_ids": ids,
    }


def pose_uncertainty(obj, r, t, k, d, sigma_px):
    """Local pixel-noise covariance, conditional on calibrated optics and marker size.

    This is not an accuracy guarantee: calibration, scale and hand placement bias
    are not observable from repeated views of a single fixed marker.
    """
    _, jac = cv2.projectPoints(obj, r, t, k, d)
    j = jac[:, :6]
    _, singular, vt = np.linalg.svd(j, full_matrices=False)
    if singular[-1] <= singular[0] * 1e-10:
        return {"pose_covariance": None, "pixel_sigma_px": float(sigma_px), "pose_condition": None}
    covariance = (vt.T * (sigma_px / singular) ** 2) @ vt
    return {
        "pose_covariance": covariance.tolist(),
        "pixel_sigma_px": float(sigma_px),
        "pose_condition": float(singular[0] / singular[-1]),
    }


def point_covariance(result, offset):
    """Propagate rvec/tvec covariance to the camera-attached measuring point."""
    if result.get("pose_covariance") is None:
        return None
    transform = np.asarray(result["T_board_camera"]).reshape(4, 4)
    rotation = transform[:3, :3].T
    r = cv2.Rodrigues(rotation)[0].ravel()
    t = -rotation @ transform[:3, 3]
    x = np.r_[r, t]

    def point(v):
        return cv2.Rodrigues(v[:3])[0].T @ (np.asarray(offset) - v[3:])

    step = 1e-6
    j = np.column_stack(
        [
            (point(x + np.eye(6)[i] * step) - point(x - np.eye(6)[i] * step)) / (2 * step)
            for i in range(6)
        ]
    )
    covariance = j @ np.asarray(result["pose_covariance"]) @ j.T
    return (covariance + covariance.T) / 2
