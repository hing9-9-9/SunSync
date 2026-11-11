"""The same bounded selection policy for burst preview and final measurement."""

import numpy as np

from .models import rigid
from .pose import estimate, point_covariance
from .robust import fuse


def select_observations(request):
    if len({c.point_id for c in request.captures}) != 1:
        raise ValueError("Burst must belong to one point")
    if len({c.capture_id for c in request.captures}) != len(request.captures):
        raise ValueError("capture_id must be unique")
    seen = set()
    samples = []
    diagnostics = []
    for capture in request.captures:
        status = {"capture_id": capture.capture_id}
        if capture.image_sha256 and capture.image_sha256 in seen:
            diagnostics.append({**status, "status": "duplicate_image"})
            continue
        if capture.image_sha256:
            seen.add(capture.image_sha256)
        try:
            result = estimate(
                request.board, capture.camera, capture.detections, request.max_reprojection_rmse_px
            )
        except ValueError as e:
            diagnostics.append({**status, "status": "invalid_observation", "reason": str(e)})
            continue
        diagnostics.append({**status, **result})
        if result["status"] != "ok":
            continue
        covariance = point_covariance(result, capture.camera_to_point_m)
        if (
            covariance is None
            or np.sqrt(np.linalg.eigvalsh(covariance)[-1]) > request.max_point_uncertainty_m
        ):
            diagnostics[-1]["status"] = "pose_uncertainty_too_large"
            continue
        t = rigid(result["T_board_camera"])
        samples.append(
            {
                "point": t[:3, :3] @ capture.camera_to_point_m + t[:3, 3],
                "covariance": covariance,
                "transform": t,
                "capture": capture,
                "pixel_sigma": result["pixel_sigma_px"],
                "signature": np.array(
                    [d.corners_px for d in sorted(capture.detections, key=lambda d: d.marker_id)]
                ).ravel(),
            }
        )
    centre, selected, info = fuse(
        samples,
        request.required_samples,
        request.max_repeat_spread_m,
        request.max_selected,
        request.placement_sigma_m,
    )
    ids = {s["capture"].capture_id for s in selected}
    for d in diagnostics:
        d["selected"] = d["capture_id"] in ids
    return {
        "status": "ok" if centre is not None else "retake",
        "raw_samples": len(request.captures),
        "valid_samples": len(samples),
        "selected_samples": len(selected),
        "selected_capture_ids": list(info.get("selected_capture_ids", [])),
        "diagnostics": diagnostics,
        "quality": info,
        "required_actions": [] if centre is not None else ["recapture_same_point"],
    }
