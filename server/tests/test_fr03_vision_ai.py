"""Unit and API tests for FR-03 & FR-06 Vision AI 3D furniture estimation."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from app.main import create_app
from app.models.scene import SceneRecord
from app.schemas.scene import Marker, Scene, Window
from app.schemas.vision_ai import (
    CameraIntrinsicsInput,
    Detection2DInput,
    MarkerAnchorInput,
)
from fastapi.testclient import TestClient
from shapely.geometry import Polygon
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from engine.vision_ai.pipeline import (
    CLASS_GEOMETRIC_PRIORS,
    DepthCalibrationResult,
    VisionRegistrationPipeline,
)
from engine.vision_ai.protocols import (
    CompositeDepthEstimator,
    CompositeFurnitureDetector,
    DetectedObject2D,
    GeometricSyntheticDepthEstimator,
    GeometricSyntheticDetector,
    MonocularDepthAdapter,
    YoloV8DetectorAdapter,
    _merge_book_clusters,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    payload = json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))
    # Physical Vision requests use a registered module, not a guessed window centre.
    window = payload["windows"][0]
    window["vertices_m"][2][2] = window["vertices_m"][3][2] = 2.0
    window["film_rows"], window["film_cols"] = 10, 30
    frame = Window.model_validate(window)
    payload["markers"].append(
        Marker(
            marker_id="film_reference",
            family="aruco",
            dictionary="DICT_4X4_50",
            code_id=0,
            side_length_m=0.6,
            plane="window",
            window_id=frame.window_id,
            T_ENU_marker=frame.marker_transform(0.5, 0.5),
        ).model_dump(mode="json")
    )
    return payload


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'vision_test.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    app = create_app(database_engine)
    with TestClient(app) as test_client:
        yield test_client


# =============================================================================
# 1. Protocols and Adapters Tests
# =============================================================================


def test_detected_object_2d_dataclass() -> None:
    obj = DetectedObject2D(
        box_xyxy=(10.0, 20.0, 110.0, 220.0),
        label="bookshelf",
        confidence=0.88,
        raw_class_name="book",
    )
    assert obj.box_xyxy == (10.0, 20.0, 110.0, 220.0)
    assert obj.label == "bookshelf"
    assert obj.confidence == 0.88
    assert obj.raw_class_name == "book"


def test_geometric_synthetic_detector_hints_and_filter() -> None:
    hints = [
        DetectedObject2D(box_xyxy=(0, 0, 10, 10), label="desk", confidence=0.9),
        DetectedObject2D(box_xyxy=(20, 20, 30, 30), label="chair", confidence=0.4),
    ]
    detector = GeometricSyntheticDetector(min_confidence=0.5)
    filtered = detector.detect(image_rgb=None, hints=hints)
    assert len(filtered) == 1
    assert filtered[0].label == "desk"


def test_geometric_synthetic_depth_estimator() -> None:
    estimator = GeometricSyntheticDepthEstimator(
        background_depth=6.0, object_depths={(10, 10, 50, 50): 2.5}
    )
    depth_map = estimator.estimate_relative_depth(image_rgb=None, width=100, height=100)
    assert depth_map.shape == (100, 100)
    assert depth_map.dtype == np.float32
    assert math.isclose(float(depth_map[20, 20]), 2.5, abs_tol=1e-3)
    assert float(depth_map[80, 80]) > 4.0


def test_real_photo_adapters_fallback() -> None:
    yolo_adapter = YoloV8DetectorAdapter()
    assert yolo_adapter.COCO_TO_FURNITURE["chair"] == "chair"
    assert yolo_adapter.COCO_TO_FURNITURE["desk"] == "desk"

    depth_adapter = MonocularDepthAdapter()
    default_d = depth_adapter.estimate_relative_depth(None, 64, 64)
    assert default_d.shape == (64, 64)


# =============================================================================
# 2. Camera Pose Estimation Tests
# =============================================================================


def test_resolve_camera_pose_from_aruco() -> None:
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

    u_win = np.array([-1.0, 0.0, 0.0])
    v_win = np.array([0.0, 0.0, 1.0])
    n_win = np.cross(u_win, v_win)
    tx, ty, tz = 3.0, 5.0, 1.6
    T_enu_m = np.eye(4, dtype=np.float64)
    T_enu_m[0:3, 0] = u_win
    T_enu_m[0:3, 1] = v_win
    T_enu_m[0:3, 2] = n_win
    T_enu_m[0:3, 3] = [tx, ty, tz]

    cam_pos_gt = np.array([3.0, 1.0, 1.6])
    R_enu_c = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
    T_enu_c_gt = np.eye(4, dtype=np.float64)
    T_enu_c_gt[0:3, 0:3] = R_enu_c
    T_enu_c_gt[0:3, 3] = cam_pos_gt

    s = 0.6
    obj_pts = np.array(
        [
            [-s / 2.0, s / 2.0, 0.0],
            [s / 2.0, s / 2.0, 0.0],
            [s / 2.0, -s / 2.0, 0.0],
            [-s / 2.0, -s / 2.0, 0.0],
        ],
        dtype=np.float32,
    )

    T_c_m = np.linalg.inv(T_enu_c_gt) @ T_enu_m
    corners_c = [T_c_m[0:3, 0:3] @ pt + T_c_m[0:3, 3] for pt in obj_pts]
    proj = [K @ pt / pt[2] for pt in corners_c]
    corners_px = [(float(p[0]), float(p[1])) for p in proj]

    marker_input = MarkerAnchorInput(code_id=0, side_length_m=s, corners_px=corners_px)

    res = VisionRegistrationPipeline.resolve_camera_pose_from_aruco(
        camera=cam_intrinsics,
        marker_anchor=marker_input,
        T_ENU_marker=[float(v) for v in T_enu_m.flatten()],
    )

    cam_pos_est = np.array(res.camera_position_enu_m)
    assert np.allclose(cam_pos_est, cam_pos_gt, atol=1e-2)
    assert len(res.marker_corner_depths_metric) == 4
    for depth in res.marker_corner_depths_metric:
        assert math.isclose(depth, 4.0, abs_tol=1e-2)


def test_resolve_camera_pose_presolved() -> None:
    cam_intrinsics = CameraIntrinsicsInput()
    T_enu_c = [1, 0, 0, 2.0, 0, 1, 0, 3.0, 0, 0, 1, 1.5, 0, 0, 0, 1]
    T_enu_m = [1, 0, 0, 2.0, 0, 1, 0, 6.0, 0, 0, 1, 1.5, 0, 0, 0, 1]
    marker_input = MarkerAnchorInput(side_length_m=0.6)

    res = VisionRegistrationPipeline.resolve_camera_pose_from_aruco(
        camera=cam_intrinsics,
        marker_anchor=marker_input,
        T_ENU_marker=T_enu_m,
        T_ENU_camera_presolved=T_enu_c,
    )
    assert res.camera_position_enu_m == (2.0, 3.0, 1.5)
    assert len(res.marker_corner_depths_metric) == 4


# =============================================================================
# 3. Metric Depth Calibration Tests
# =============================================================================


def test_calibrate_monocular_depth_least_squares() -> None:
    d_rel = [1.0, 2.0, 3.0, 4.0]
    z_true = [2.5, 4.5, 6.5, 8.5]
    res = VisionRegistrationPipeline.calibrate_monocular_depth(d_rel, z_true)
    assert math.isclose(res.alpha, 2.0, abs_tol=1e-4)
    assert math.isclose(res.beta, 0.5, abs_tol=1e-4)

    assert math.isclose(float(res.to_metric_depth(2.0)), 4.5, abs_tol=1e-4)
    assert float(res.to_metric_depth(-10.0)) == 0.3
    assert float(res.to_metric_depth(100.0)) == 25.0


def test_calibrate_monocular_depth_single_anchor() -> None:
    d_rel = [2.0]
    z_true = [6.0]
    res = VisionRegistrationPipeline.calibrate_monocular_depth(d_rel, z_true)
    assert math.isclose(res.alpha, 3.0, abs_tol=1e-4)
    assert math.isclose(res.beta, 0.0, abs_tol=1e-4)


# =============================================================================
# 4. 3D Cuboid Back-Projection & Floor Refinement Tests
# =============================================================================


def test_backproject_box_desk_and_clamping() -> None:
    camera = CameraIntrinsicsInput(width=1280, height=720, fx=900.0, fy=900.0, cx=640.0, cy=360.0)
    T_enu_c = [
        1.0,
        0.0,
        0.0,
        3.0,
        0.0,
        0.0,
        1.0,
        1.0,
        0.0,
        -1.0,
        0.0,
        1.5,
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    depth_calib = DepthCalibrationResult(alpha=1.0, beta=0.0)
    room_floor_poly = Polygon([(0.0, 0.0), (6.0, 0.0), (6.0, 5.0), (0.0, 5.0)])

    det = Detection2DInput(
        box_xyxy=(480.0, 400.0, 800.0, 600.0),
        label="desk",
        confidence=0.95,
        observed_depth_rel=2.5,
        protection_grade="C",
    )

    result = VisionRegistrationPipeline.backproject_box_to_3d_cuboid(
        detection=det,
        camera=camera,
        T_ENU_camera=T_enu_c,
        depth_calibration=depth_calib,
        room_floor_poly=room_floor_poly,
        room_height_m=3.0,
        room_id="room_1",
        occluder_id="occ_desk_1",
        observation_id="obs_1",
        policy_id="policy_1",
        auto_create_protected_regions=True,
    )

    occ = result.occluder
    assert occ.occluder_id == "occ_desk_1"
    assert occ.kind == "cuboid"
    assert occ.type == "desk"
    assert math.isclose(occ.base_center_m[2], 0.0, abs_tol=1e-4)
    assert occ.dimensions_m[0] > 0.0
    assert occ.dimensions_m[1] == CLASS_GEOMETRIC_PRIORS["desk"]["depth"]

    pr = result.protected_region
    assert pr is not None
    assert pr.host_occluder_id == "occ_desk_1"
    assert pr.grade == "C"
    assert pr.receiving_normal == (0.0, 0.0, 1.0)
    assert len(pr.vertices_m) == 4


def test_backproject_box_bookshelf_front_region() -> None:
    camera = CameraIntrinsicsInput(width=1280, height=720, fx=900.0, fy=900.0, cx=640.0, cy=360.0)
    T_enu_c = [
        1.0,
        0.0,
        0.0,
        3.0,
        0.0,
        0.0,
        1.0,
        1.0,
        0.0,
        -1.0,
        0.0,
        1.5,
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    depth_calib = DepthCalibrationResult(alpha=1.0, beta=0.0)
    room_floor_poly = Polygon([(0.0, 0.0), (6.0, 0.0), (6.0, 5.0), (0.0, 5.0)])

    det = Detection2DInput(
        box_xyxy=(200.0, 100.0, 500.0, 550.0),
        label="bookshelf",
        confidence=0.91,
        observed_depth_rel=3.0,
        protection_grade="B",
    )

    result = VisionRegistrationPipeline.backproject_box_to_3d_cuboid(
        detection=det,
        camera=camera,
        T_ENU_camera=T_enu_c,
        depth_calibration=depth_calib,
        room_floor_poly=room_floor_poly,
        room_height_m=3.0,
        room_id="room_1",
        occluder_id="occ_shelf_1",
        observation_id="obs_1",
        policy_id="policy_1",
        auto_create_protected_regions=True,
    )

    pr = result.protected_region
    assert pr is not None
    assert pr.grade == "B"
    assert abs(pr.receiving_normal[2]) <= 1e-4


def test_backprojection_records_depth_patch_fallback_and_background_mixing_evidence() -> None:
    camera = CameraIntrinsicsInput(width=100, height=100, fx=100.0, fy=100.0, cx=50.0, cy=50.0)
    room_floor_poly = Polygon([(0.0, 0.0), (8.0, 0.0), (8.0, 8.0), (0.0, 8.0)])
    det = Detection2DInput(box_xyxy=(20.0, 20.0, 80.0, 80.0), label="desk", confidence=0.9)
    kwargs = {
        "detection": det,
        "camera": camera,
        "T_ENU_camera": [
            1.0,
            0.0,
            0.0,
            4.0,
            0.0,
            1.0,
            0.0,
            4.0,
            0.0,
            0.0,
            1.0,
            1.5,
            0.0,
            0.0,
            0.0,
            1.0,
        ],
        "depth_calibration": DepthCalibrationResult(alpha=1.0, beta=0.0),
        "room_floor_poly": room_floor_poly,
        "room_height_m": 3.0,
        "room_id": "room_1",
        "occluder_id": "occ_diag",
        "observation_id": "obs_diag",
    }

    invalid = VisionRegistrationPipeline.backproject_box_to_3d_cuboid(
        **kwargs, depth_map_metric=np.full((100, 100), np.nan, dtype=np.float32)
    )
    assert invalid.diagnostics is not None
    assert invalid.diagnostics.depth_fallback_reason == "non_finite_depth_patch"
    assert invalid.diagnostics.valid_depth_pixel_count == 0
    assert invalid.diagnostics.dimension_sources["depth"] == "prior"

    depth_map = np.full((100, 100), 1.0, dtype=np.float32)
    depth_map[32:68, 50:68] = 5.0
    mixed = VisionRegistrationPipeline.backproject_box_to_3d_cuboid(
        **(kwargs | {"occluder_id": "occ_mixed"}), depth_map_metric=depth_map
    )
    assert mixed.diagnostics is not None
    assert mixed.diagnostics.representative_depth_rule == "central_60_percent_median"
    assert mixed.diagnostics.background_pixel_mixing_risk == "high"
    assert mixed.diagnostics.depth_iqr_m is not None
    assert set(mixed.diagnostics.dimension_sources) == {"width", "depth", "height", "position"}


# =============================================================================
# 5. REST API Integration Tests
# =============================================================================


def test_api_vision_furniture_estimate_persists(
    client: TestClient, scene_payload: dict[str, Any], database_engine: Engine
) -> None:
    resp = client.post("/api/scenes", json=scene_payload)
    assert resp.status_code == 201
    scene_id = scene_payload["scene_id"]
    initial_rev = scene_payload["revision"]

    req_payload = {
        "camera": {
            "width": 1280,
            "height": 720,
            "fx": 900.0,
            "fy": 900.0,
            "cx": 640.0,
            "cy": 360.0,
        },
        "marker_anchor": {
            "code_id": 0,
            "side_length_m": 0.6,
            "corners_px": [
                [550.0, 270.0],
                [730.0, 270.0],
                [730.0, 450.0],
                [550.0, 450.0],
            ],
            "anchor_depth_rel": 3.0,
        },
        "detections": [
            {
                "box_xyxy": [450.0, 300.0, 750.0, 550.0],
                "label": "desk",
                "confidence": 0.94,
                "observed_depth_rel": 2.2,
                "name": "Vision AI Desk",
                "protection_grade": "C",
            },
            {
                "box_xyxy": [150.0, 200.0, 350.0, 600.0],
                "label": "bookshelf",
                "confidence": 0.89,
                "observed_depth_rel": 2.8,
                "name": "Vision AI Bookshelf",
                "protection_grade": "B",
            },
        ],
        "apply_to_scene": True,
        "auto_create_protected_regions": True,
    }

    resp = client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["scene_id"] == scene_id
    assert data["scene_revision"] == initial_rev + 1
    assert data["applied_to_scene"] is True
    assert len(data["estimated_items"]) == 2

    with Session(database_engine) as session:
        record = session.get(SceneRecord, scene_id)
        assert record is not None
        assert record.revision == initial_rev + 1
        validated = Scene.model_validate_json(record.scene_json)
        assert validated.revision == initial_rev + 1
        assert any(occ.name == "Vision AI Desk" for occ in validated.occluders)
        assert any(occ.name == "Vision AI Bookshelf" for occ in validated.occluders)
        assert any("Desk" in r.name for r in validated.protected_regions)
        assert any("Bookshelf" in r.name for r in validated.protected_regions)


def test_api_vision_furniture_estimate_dry_run_does_not_mutate_db(
    client: TestClient, scene_payload: dict[str, Any], database_engine: Engine
) -> None:
    resp = client.post("/api/scenes", json=scene_payload)
    assert resp.status_code == 201
    scene_id = scene_payload["scene_id"]
    initial_rev = scene_payload["revision"]

    req_payload = {
        "camera": {
            "width": 1280,
            "height": 720,
            "fx": 900.0,
            "fy": 900.0,
            "cx": 640.0,
            "cy": 360.0,
        },
        "marker_anchor": {
            "code_id": 0,
            "side_length_m": 0.6,
            "corners_px": [[550.0, 270.0], [730.0, 270.0], [730.0, 450.0], [550.0, 450.0]],
            "anchor_depth_rel": 3.0,
        },
        "detections": [
            {
                "box_xyxy": [450.0, 300.0, 750.0, 550.0],
                "label": "chair",
                "confidence": 0.9,
                "observed_depth_rel": 2.0,
            }
        ],
        "apply_to_scene": False,
    }

    resp = client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_to_scene"] is False
    assert data["scene_revision"] == initial_rev

    with Session(database_engine) as session:
        record = session.get(SceneRecord, scene_id)
        assert record is not None
        assert record.revision == initial_rev


def test_api_vision_furniture_estimate_not_found(client: TestClient) -> None:
    resp = client.post(
        "/api/scenes/nonexistent_scene/vision-furniture-estimate",
        json={
            "camera": {
                "width": 1280,
                "height": 720,
                "fx": 900.0,
                "fy": 900.0,
                "cx": 640.0,
                "cy": 360.0,
            },
            "marker_anchor": {
                "code_id": 0,
                "side_length_m": 0.6,
                "corners_px": [[550.0, 270.0], [730.0, 270.0], [730.0, 450.0], [550.0, 450.0]],
                "anchor_depth_rel": 3.0,
            },
            "detections": [
                {
                    "box_xyxy": [10.0, 10.0, 50.0, 50.0],
                    "label": "chair",
                    "confidence": 0.8,
                }
            ],
        },
    )
    assert resp.status_code == 404


def test_vision_registration_singular_collinear_depth_calibration() -> None:
    """Verify depth calibration gracefully handles ill-conditioned or collinear points without NaN/crash."""
    pipeline = VisionRegistrationPipeline()

    # 1. Collinear points where relative depths are nearly identical
    collinear_rel = [2.0, 2.00001, 2.00002]
    collinear_met = [3.0, 3.5, 4.0]
    calib = pipeline.calibrate_monocular_depth(collinear_rel, collinear_met)
    assert calib.alpha > 0.01
    assert math.isfinite(calib.alpha)
    assert math.isfinite(calib.beta)

    # 2. Inverted / negative slope data
    inv_rel = [1.0, 2.0, 3.0]
    inv_met = [5.0, 3.0, 1.0]  # negative slope
    calib2 = pipeline.calibrate_monocular_depth(inv_rel, inv_met)
    assert calib2.alpha > 0.01
    assert math.isfinite(calib2.alpha)
    assert math.isfinite(calib2.beta)

    # 3. to_metric_depth produces valid bounded output
    depth_m = calib2.to_metric_depth(2.5)
    assert 0.3 <= depth_m <= 25.0


def test_api_vision_furniture_estimate_multiple_apply_id_uniqueness(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
) -> None:
    """Verify calling apply_to_scene=True multiple times generates collision-free IDs and valid Scene."""
    scene_id = scene_payload["scene_id"]
    with Session(database_engine) as session:
        rec = SceneRecord(
            scene_id=scene_id,
            revision=scene_payload["revision"],
            scene_json=json.dumps(scene_payload),
        )
        session.add(rec)
        session.commit()

    req_payload = {
        "camera": {
            "width": 1280,
            "height": 720,
            "fx": 900.0,
            "fy": 900.0,
            "cx": 640.0,
            "cy": 360.0,
        },
        "marker_anchor": {
            "code_id": 0,
            "side_length_m": 0.6,
            "corners_px": [[550.0, 270.0], [730.0, 270.0], [730.0, 450.0], [550.0, 450.0]],
            "anchor_depth_rel": 3.0,
        },
        "detections": [
            {
                "box_xyxy": [450.0, 300.0, 750.0, 550.0],
                "label": "desk",
                "confidence": 0.92,
                "observed_depth_rel": 2.2,
                "protection_grade": "B",
            }
        ],
        "apply_to_scene": True,
        "auto_create_protected_regions": True,
    }

    initial_rev = scene_payload["revision"]

    # First call: applies revision initial_rev -> initial_rev + 1
    resp1 = client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=req_payload)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["applied_to_scene"] is True
    assert data1["scene_revision"] == initial_rev + 1
    item1 = data1["estimated_items"][0]
    occ_id_1 = item1["occluder"]["occluder_id"]
    reg_id_1 = item1["protected_region"]["region_id"]

    # Second call: applies revision initial_rev + 1 -> initial_rev + 2 without ID collision
    resp2 = client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=req_payload)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["applied_to_scene"] is True
    assert data2["scene_revision"] == initial_rev + 2
    item2 = data2["estimated_items"][0]
    occ_id_2 = item2["occluder"]["occluder_id"]
    reg_id_2 = item2["protected_region"]["region_id"]

    # Assert new IDs are distinct from first call
    assert occ_id_1 != occ_id_2
    assert reg_id_1 != reg_id_2

    # Verify scene in database is valid and contains both occluders and regions
    with Session(database_engine) as session:
        record = session.get(SceneRecord, scene_id)
        assert record is not None
        assert record.revision == initial_rev + 2
        persisted_scene = Scene.model_validate_json(record.scene_json)
        all_ids = (
            [occ.occluder_id for occ in persisted_scene.occluders]
            + [reg.region_id for reg in persisted_scene.protected_regions]
            + [win.window_id for win in persisted_scene.windows]
            + [persisted_scene.room.room_id]
        )
        assert len(all_ids) == len(set(all_ids)), "All scene IDs must be strictly unique"
        assert occ_id_1 in [occ.occluder_id for occ in persisted_scene.occluders]
        assert occ_id_2 in [occ.occluder_id for occ in persisted_scene.occluders]


def _make_synthetic_room_png_bytes() -> bytes:
    """Create a 1280x720 PNG containing ArUco ID 0 and a rectangular dark desk silhouette."""
    import cv2

    canvas = np.full((720, 1280, 3), 245, dtype=np.uint8)
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker_img = cv2.aruco.generateImageMarker(aruco_dict, 0, 180)
    marker_rgb = cv2.cvtColor(marker_img, cv2.COLOR_GRAY2RGB)
    # Place white quiet-zone border + 180x180 ArUco marker near upper center
    canvas[120:300, 550:730] = marker_rgb
    # Place a dark rectangular desk contour away from the marker box
    canvas[420:580, 420:780] = (45, 50, 55)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
    assert ok
    return encoded.tobytes()


def test_api_vision_furniture_photo_base64_and_multipart(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
) -> None:
    """Direct image_base64 and /vision-furniture-photo endpoints run detection + ArUco estimation."""
    import base64

    scene_id = scene_payload["scene_id"]
    with Session(database_engine) as session:
        session.add(
            SceneRecord(
                scene_id=scene_id,
                revision=scene_payload["revision"],
                scene_json=json.dumps(scene_payload),
            )
        )
        session.commit()

    png_bytes = _make_synthetic_room_png_bytes()
    b64_img = base64.b64encode(png_bytes).decode("ascii")

    # 1. JSON with image_base64 and empty detections
    resp1 = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-estimate",
        json={
            "image_base64": b64_img,
            "marker_anchor": {"code_id": 0, "side_length_m": 0.6},
            "apply_to_scene": False,
        },
    )
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["applied_to_scene"] is False
    assert len(data1["estimated_items"]) >= 1
    assert data1["registration_fragment"] is not None
    assert data1["registration_fragment"]["schema_version"] == "sunsync.fr06-fragment/1"

    # 2. Multipart/form-data upload to /vision-furniture-photo
    resp2 = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?apply_to_scene=true",
        files={"image": ("room.png", png_bytes, "image/png")},
    )
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["applied_to_scene"] is True
    assert data2["scene_revision"] == scene_payload["revision"] + 1
    assert len(data2["estimated_items"]) >= 1

    # 3. Raw bytes upload to /vision-furniture-photo
    resp3 = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?apply_to_scene=false",
        content=png_bytes,
        headers={"Content-Type": "image/png"},
    )
    assert resp3.status_code == 200, resp3.text
    assert len(resp3.json()["estimated_items"]) >= 1


def test_detection_2d_input_normalizes_ui_aliases() -> None:
    """Detection2DInput accepts UI labels (bookcase, table, cabinet, display) seamlessly."""
    assert Detection2DInput(box_xyxy=(0, 0, 100, 200), label="bookcase").label == "bookshelf"  # type: ignore[arg-type]
    assert Detection2DInput(box_xyxy=(0, 0, 100, 200), label="table").label == "desk"  # type: ignore[arg-type]
    assert Detection2DInput(box_xyxy=(0, 0, 100, 200), label="cabinet").label == "other"  # type: ignore[arg-type]
    assert Detection2DInput(box_xyxy=(0, 0, 100, 200), label="chair").label == "chair"


def test_vision_estimate_syncs_ui_space_furniture_regions_and_revision(client: TestClient) -> None:
    """Vision estimation via replace_scene syncs UI furniture (bookcase/chair), regions, and 409 check."""
    ui_payload = {
        "id": "vision-ui-space",
        "building": "도서관",
        "floor": "1층",
        "name": "열람실",
        "width": 8.0,
        "depth": 6.0,
        "height": 3.0,
        "zone": "구역 1",
        "zones": ["구역 1"],
        "source": "manual",
        "revision": 1,
        "site": {
            "latitude": 37.5,
            "longitude": 127.0,
            "timezone": "Asia/Seoul",
            "northConfirmed": True,
        },
        "windowDimensions": {
            "width": 3.0,
            "height": 2.0,
            "wall": "north",
            "offset": 2.5,
            "sill": 0.8,
            "captureModule": {"row": 0, "col": 1},
        },
        "furniture": [],
        "regions": [],
    }
    created = client.post("/api/ui/spaces", json=ui_payload)
    assert created.status_code == 201, created.text
    space = created.json()
    scene_id = space["serverSceneId"]

    # Stale expected_revision is rejected with 409
    stale_req = {
        "camera": {
            "width": 1280,
            "height": 720,
            "fx": 900.0,
            "fy": 900.0,
            "cx": 640.0,
            "cy": 360.0,
        },
        "marker_anchor": {
            "code_id": 0,
            "side_length_m": 0.6,
            "corners_px": [[550.0, 210.0], [730.0, 210.0], [730.0, 390.0], [550.0, 390.0]],
            "anchor_depth_rel": 3.0,
        },
        "expected_revision": 99,
        "detections": [
            {
                "box_xyxy": [180.0, 220.0, 360.0, 520.0],
                "label": "bookcase",
                "confidence": 0.91,
                "observed_depth_rel": 2.4,
                "protection_grade": "B",
            },
            {
                "box_xyxy": [860.0, 340.0, 990.0, 520.0],
                "label": "chair",
                "confidence": 0.88,
                "observed_depth_rel": 2.1,
                "protection_grade": "C",
            },
        ],
        "apply_to_scene": True,
        "auto_create_protected_regions": True,
    }
    assert (
        client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=stale_req).status_code
        == 409
    )

    valid_req = {**stale_req, "expected_revision": 1}
    res = client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=valid_req)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scene_revision"] == 2
    assert len(body["estimated_items"]) == 2
    assert body["registration_fragment"]["occluders"][0]["type"] == "bookshelf"
    assert body["registration_fragment"]["occluders"][1]["type"] == "chair"

    # Verify UI Space projection is immediately synchronized!
    synced_space = client.get(f"/api/ui/spaces/{space['id']}").json()
    assert synced_space["serverRevision"] == 2
    assert [f["type"] for f in synced_space["furniture"]] == ["bookcase", "chair"]
    assert len(synced_space["regions"]) == 2
    assert synced_space["regions"][0]["furnitureId"] == synced_space["furniture"][0]["id"]
    assert synced_space["regions"][1]["furnitureId"] == synced_space["furniture"][1]["id"]
    assert synced_space["regions"][1]["face"] == "상단"


def test_merge_book_clusters_combines_nearby_books_into_bookshelf() -> None:
    """Clustered YOLO book boxes should merge into one bookshelf while keeping isolated items."""
    detections = [
        DetectedObject2D(
            box_xyxy=(100.0, 100.0, 130.0, 160.0),
            label="bookshelf",
            confidence=0.70,
            raw_class_name="book",
        ),
        DetectedObject2D(
            box_xyxy=(140.0, 105.0, 175.0, 165.0),
            label="bookshelf",
            confidence=0.85,
            raw_class_name="book",
        ),
        DetectedObject2D(
            box_xyxy=(700.0, 300.0, 740.0, 360.0),
            label="bookshelf",
            confidence=0.60,
            raw_class_name="book",
        ),
        DetectedObject2D(
            box_xyxy=(500.0, 300.0, 620.0, 480.0),
            label="chair",
            confidence=0.90,
            raw_class_name="chair",
        ),
    ]
    merged = _merge_book_clusters(detections)
    assert len(merged) == 3
    assert merged[0].label == "chair"

    clustered = [d for d in merged if d.raw_class_name == "book_cluster_bookshelf"]
    assert len(clustered) == 1
    assert clustered[0].box_xyxy == (100.0, 100.0, 175.0, 165.0)
    assert clustered[0].confidence == pytest.approx(0.85)
    assert any(d.box_xyxy == (700.0, 300.0, 740.0, 360.0) for d in merged)


def test_composite_detector_uses_primary_and_falls_back_gracefully() -> None:
    """CompositeFurnitureDetector records source and falls back on empty or erroring primary."""
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    yolo_box = DetectedObject2D(
        box_xyxy=(200.0, 200.0, 360.0, 420.0),
        label="desk",
        confidence=0.89,
        raw_class_name="dining table",
    )
    fallback_box = DetectedObject2D(
        box_xyxy=(50.0, 120.0, 180.0, 400.0),
        label="bookshelf",
        confidence=0.72,
        raw_class_name="contour_fallback",
    )

    class _StubDetector:
        def __init__(self, items: list[DetectedObject2D] | None = None, fail: bool = False) -> None:
            self._items = items or []
            self._fail = fail

        def detect(
            self,
            image_rgb: np.ndarray | None,
            hints: Any = None,
        ) -> list[DetectedObject2D]:
            del image_rgb, hints
            if self._fail:
                raise RuntimeError("weights missing")
            return list(self._items)

    primary_ok = CompositeFurnitureDetector(
        prefer_yolo=True,
        primary_detector=_StubDetector(items=[yolo_box]),
        fallback_detector=GeometricSyntheticDetector(default_detections=[fallback_box]),
    )
    out_ok = primary_ok.detect(dummy_img)
    assert out_ok == [yolo_box]
    assert primary_ok.last_source == "yolov8"

    primary_empty = CompositeFurnitureDetector(
        prefer_yolo=True,
        primary_detector=_StubDetector(items=[]),
        fallback_detector=GeometricSyntheticDetector(default_detections=[fallback_box]),
    )
    out_empty = primary_empty.detect(dummy_img)
    assert out_empty == [fallback_box]
    assert primary_empty.last_source == "synthetic_contour"

    primary_fail = CompositeFurnitureDetector(
        prefer_yolo=True,
        primary_detector=_StubDetector(fail=True),
        fallback_detector=GeometricSyntheticDetector(default_detections=[fallback_box]),
    )
    out_fail = primary_fail.detect(dummy_img)
    assert out_fail == [fallback_box]
    assert primary_fail.last_source == "synthetic_contour"


def test_composite_depth_estimator_falls_back_on_runtime_error() -> None:
    """CompositeDepthEstimator returns geometric depth map if neural backend fails."""
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)

    class _FailingDepth:
        def estimate_relative_depth(
            self,
            image_rgb: np.ndarray | None,
            width: int,
            height: int,
        ) -> np.ndarray:
            del image_rgb, width, height
            raise RuntimeError("depth model load failed")

    estimator = CompositeDepthEstimator(
        prefer_monocular=True,
        primary=_FailingDepth(),
        fallback=GeometricSyntheticDepthEstimator(),
    )
    depth_map = estimator.estimate_relative_depth(dummy_img, width=64, height=64)
    assert depth_map.shape == (64, 64)
    assert depth_map.dtype == np.float32


def test_calibrate_monocular_depth_uses_proportional_scale_for_flat_marker_depths() -> None:
    """Narrow marker depth spread avoids noisy linear slopes and falls back to beta=0."""
    rel_depths = [3.00, 3.04, 3.02, 3.01]
    metric_depths = [2.40, 2.45, 2.42, 2.41]
    res = VisionRegistrationPipeline.calibrate_monocular_depth(rel_depths, metric_depths)
    assert res.beta == pytest.approx(0.0)
    assert res.alpha == pytest.approx(float(np.mean(metric_depths) / np.mean(rel_depths)))


def test_vision_estimate_returns_quality_uncertainty_guidance_and_corners(
    client: TestClient,
) -> None:
    """Estimate response exposes uncertainty score, Korean ArUco guidance, and 4 ENU corners."""
    ui_payload = {
        "id": "vision-guidance-space",
        "building": "도서관",
        "floor": "2층",
        "name": "연구실",
        "width": 8.0,
        "depth": 6.0,
        "height": 3.0,
        "zone": "구역 1",
        "zones": ["구역 1"],
        "source": "manual",
        "revision": 1,
        "site": {
            "latitude": 37.5,
            "longitude": 127.0,
            "timezone": "Asia/Seoul",
            "northConfirmed": True,
        },
        "windowDimensions": {
            "width": 3.0,
            "height": 2.0,
            "wall": "north",
            "offset": 2.5,
            "sill": 0.8,
            "captureModule": {"row": 0, "col": 1},
        },
        "furniture": [],
        "regions": [],
    }
    created = client.post("/api/ui/spaces", json=ui_payload)
    assert created.status_code == 201, created.text
    scene_id = created.json()["serverSceneId"]

    req = {
        "camera": {
            "width": 1280,
            "height": 720,
            "fx": 900.0,
            "fy": 900.0,
            "cx": 640.0,
            "cy": 360.0,
        },
        "marker_anchor": {
            "code_id": 0,
            "side_length_m": 0.6,
            "corners_px": [[550.0, 210.0], [730.0, 210.0], [730.0, 390.0], [550.0, 390.0]],
            "anchor_depth_rel": 3.0,
        },
        "detections": [
            {
                "box_xyxy": [180.0, 220.0, 360.0, 520.0],
                "label": "bookcase",
                "confidence": 0.52,
                "observed_depth_rel": 6.2,
                "protection_grade": "B",
            },
            {
                "box_xyxy": [540.0, 360.0, 740.0, 560.0],
                "label": "desk",
                "confidence": 0.92,
                "observed_depth_rel": 2.2,
                "protection_grade": "C",
            },
        ],
        "apply_to_scene": False,
        "auto_create_protected_regions": True,
    }
    res = client.post(f"/api/scenes/{scene_id}/vision-furniture-estimate", json=req)
    assert res.status_code == 200, res.text
    items = res.json()["estimated_items"]
    assert len(items) == 2

    uncertain_item, confident_item = items[0], items[1]
    assert uncertain_item["detection_source"] == "client_hint"
    assert uncertain_item["estimation_quality"] == "low_needs_aruco"
    assert uncertain_item["needs_aruco_refinement"] is True
    assert "신뢰도" in uncertain_item["uncertainty_reason_ko"]
    assert "P1~P4" in uncertain_item["aruco_capture_guidance_ko"]
    assert uncertain_item["recommended_capture_shape"] == "rectangle"
    assert len(uncertain_item["estimated_corners_enu_m"]) == 4
    assert all(len(pt) == 2 for pt in uncertain_item["estimated_corners_enu_m"])

    assert confident_item["detection_source"] == "client_hint"
    assert confident_item["estimation_quality"] == "high"
    assert confident_item["needs_aruco_refinement"] is False
    assert "P1~P4" in confident_item["aruco_capture_guidance_ko"]
    assert len(confident_item["estimated_corners_enu_m"]) == 4
