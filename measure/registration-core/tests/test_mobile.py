import base64

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from sunsync_registration.api import app
from sunsync_registration.mobile import (
    AndroidCalibration,
    AppleCalibration,
    AutomaticSetup,
    NativeFrame,
    calibrate_automatically,
    normalize_camera,
)
from sunsync_registration.models import Board


def frame(calibration=None, width=800, height=600):
    pixels = np.full((height, width), 255, np.uint8)
    encoded = cv2.imencode(".png", pixels)[1]
    return NativeFrame(
        capture_id="photo1",
        point_id="P1",
        captured_at="2026-11-05T10:00:00+09:00",
        stream_id="stream1",
        lens_id="back1",
        width=width,
        height=height,
        pixel_rotation_degrees=0,
        mirrored=False,
        image_base64=base64.b64encode(encoded).decode(),
        board=Board(board_id="board", marker_side_m=0.115, gap_x_m=0.02, gap_y_m=0.02),
        calibration=calibration,
    )


def test_android_intrinsics_crop_scale_and_coefficient_order():
    metadata = AndroidCalibration(
        kind="android_camera2",
        intrinsic=[2000, 2100, 2000, 1500, 0],
        distortion=[0.1, 0.2, 0.3, 0.004, 0.005],
        reference_width=4000,
        reference_height=3000,
        crop=[0, 0, 4000, 3000],
        distortion_correction="off",
        stabilization="off",
    )
    value = frame(metadata, 1920, 1080)
    camera, _ = normalize_camera(value, np.zeros((1080, 1920), np.uint8))
    assert [camera.fx, camera.fy, camera.cx, camera.cy] == pytest.approx(
        [960, 1008, 960, 540]
    )
    assert camera.distortion == [0.1, 0.2, 0.004, 0.005, 0.3]
    assert camera.pixel_space == "raw_opencv"


def apple_metadata(lookup):
    return AppleCalibration(
        kind="ios_avfoundation",
        intrinsic=[600, 0, 400, 0, 610, 300, 0, 0, 1],
        reference_width=800,
        reference_height=600,
        distortion_center=[400, 300],
        distortion_lookup=lookup,
        full_reference_frame=True,
    )


def test_apple_lut_rectifies_pixels_before_using_zero_distortion():
    pixels = np.arange(800 * 600, dtype=np.uint8).reshape(600, 800)
    camera, corrected = normalize_camera(frame(apple_metadata([0, 0])), pixels)
    assert np.array_equal(corrected, pixels)
    assert camera.distortion == [] and camera.pixel_space == "rectified"
    _, corrected = normalize_camera(frame(apple_metadata([0.1, 0.1])), pixels)
    assert corrected[300, 500] == pixels[300, 510]  # output -> distorted input sampling


def test_missing_camera_uses_guided_setup_never_default_focal_length():
    response = TestClient(app).post(
        "/v1/mobile/captures", json=frame().model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "automatic_setup_required"
    assert response.json()["camera"] is None


@pytest.mark.parametrize("change", ["dimensions", "image", "rotation", "timestamp"])
def test_invalid_native_capture_is_rejected(change):
    payload = frame().model_dump(mode="json")
    if change == "dimensions":
        payload["width"] = 900
    elif change == "image":
        payload["image_base64"] = base64.b64encode(b"invalid image").decode()
    elif change == "rotation":
        payload["pixel_rotation_degrees"] = 90
    else:
        payload["captured_at"] = "2026-11-05T10:00:00"
    assert TestClient(app).post("/v1/mobile/captures", json=payload).status_code == 422


def setup_fixture():
    board = frame().board
    k = np.array([[900.0, 0, 640], [0, 910.0, 480], [0, 0, 1]])
    distortion = np.array([0.04, -0.015, 0.001, -0.002, 0.0])
    rng = np.random.default_rng(7)
    views = []
    for i in range(20):
        rotation = np.array(
            [
                (-0.45, -0.15, 0.2, 0.45)[i % 4],
                (-0.4, -0.1, 0.2, 0.45)[i // 5],
                0.05 * (i % 3),
            ]
        )
        r = cv2.Rodrigues(rotation)[0]
        translation = np.array(
            [
                (-0.22, -0.08, 0.08, 0.22)[i % 4],
                (-0.15, 0, 0.15)[i % 3],
                0.7 + 0.05 * (i % 3),
            ]
        ) - r @ [0.125, 0.125, 0]
        detections = []
        for code, objects in board.objects().items():
            pixels = cv2.projectPoints(objects, rotation, translation, k, distortion)[
                0
            ].reshape(-1, 2)
            pixels += rng.normal(0, 0.03, pixels.shape)
            detections.append({"marker_id": code, "corners_px": pixels.tolist()})
        views.append(
            {
                "capture_id": str(i),
                "stream_id": "locked",
                "lens_id": "back",
                "width": 1280,
                "height": 960,
                "detections": detections,
            }
        )
    return {"board": board.model_dump(), "views": views}, k


def test_guided_setup_recovers_intrinsics_and_validates_held_out_views():
    request, k = setup_fixture()
    result = calibrate_automatically(AutomaticSetup.model_validate(request))
    camera = result["camera"]
    assert camera["fx"] == pytest.approx(k[0, 0], rel=0.01)
    assert camera["fy"] == pytest.approx(k[1, 1], rel=0.01)
    assert len(camera["distortion"]) == 5
    assert result["metric_accuracy_verified"] is False


@pytest.mark.parametrize(
    "change", ["lens", "stream", "resolution", "duplicates", "same_view"]
)
def test_guided_setup_rejects_mixed_and_repeated_frames(change):
    request, _ = setup_fixture()
    if change == "lens":
        request["views"][1]["lens_id"] = "ultrawide"
    elif change == "stream":
        request["views"][1]["stream_id"] = "zoom_changed"
    elif change == "resolution":
        request["views"][1]["width"] = 1600
    elif change == "duplicates":
        request["views"][1]["capture_id"] = "0"
    else:
        request["views"][1]["detections"] = request["views"][0]["detections"]
    with pytest.raises(ValueError):
        calibrate_automatically(AutomaticSetup.model_validate(request))
