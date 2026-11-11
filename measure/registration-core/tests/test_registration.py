import base64
import copy
import importlib.util
from pathlib import Path
import cv2
import numpy as np
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sunsync_registration.models import Camera, PlanRequest, MeasureRequest
from sunsync_registration.core import plan, measure, compose_scene
from sunsync_registration.geometry import fit_surface
from sunsync_registration.pose import estimate, detect
from sunsync_registration.api import app

spec = importlib.util.spec_from_file_location(
    "example_fixture", Path(__file__).parents[1] / "examples/build_synthetic_example.py"
)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def test_metric_pose_rectangle_height_enu_scene():
    request = example.fixture()
    result = measure(request)
    assert result["status"] == "model_ready_unreviewed"
    assert np.allclose(result["model"]["dimensions_m"], [0.6, 0.45, 0.75], atol=1e-5)
    assert np.allclose(result["model"]["base_center_enu_m"], [0, -1.425, 0], atol=1e-5)
    assert all("point_enu_m" in p for p in result["points"].values())
    assert compose_scene([result])["coordinate_frame"] == "ENU"


def test_no_camera_guess_or_raw_distortion_default():
    d = example.fixture().captures[0].camera.model_dump()
    del d["fx"]
    with pytest.raises(ValidationError):
        Camera.model_validate(d)
    d = example.fixture().captures[0].camera.model_dump()
    d["pixel_space"] = "raw_opencv"
    with pytest.raises(ValidationError):
        Camera.model_validate(d)


def test_floor_height_is_not_north_alignment():
    d = example.fixture().model_dump()
    d["alignment"] = None
    d["floor"] = {"board_origin_height_m": 1.2, "up_board": [0, -1, 0]}
    result = measure(MeasureRequest.model_validate(d))
    assert result["model"] and "T_enu_model" not in result["model"]
    assert compose_scene([result])["coordinate_frame"] == "board"


def test_missing_floor_or_samples_remains_incomplete():
    d = example.fixture().model_dump()
    d["alignment"] = None
    result = measure(MeasureRequest.model_validate(d))
    assert result["model"] is None and result["surface"]["height_above_floor_m"] is None
    d = example.fixture().model_dump()
    d["captures"] = d["captures"][:-1]
    result = measure(MeasureRequest.model_validate(d))
    assert result["points"]["P4"]["status"] == "insufficient_samples"
    assert result["model"] is None


def test_duplicate_screen_markers_rejected():
    r = example.fixture()
    sample = r.captures[0]
    with pytest.raises(ValueError, match="Duplicate"):
        estimate(r.board, sample.camera, sample.detections + sample.detections)


@pytest.mark.parametrize(
    "kind,number", [("rectangle", 4), ("circle", 4), ("ellipse", 6), ("polygon", 6)]
)
def test_shape_plan_and_repeated_sample_counts(kind, number):
    req = example.fixture().plan.model_dump()
    req["shape"] = {"kind": kind}
    if kind == "polygon":
        req["shape"]["vertices"] = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]]
    result = plan(PlanRequest.model_validate(req))
    assert len(result["steps"]) == number
    assert result["steps"][0]["required_samples"] == 3
    assert len({s["point_id"] for s in result["steps"]}) == number


def test_self_crossing_polygon_rejected():
    req = example.fixture().plan.model_dump()
    req["shape"] = {"kind": "polygon", "vertices": [[0, 0], [1, 1], [0, 1], [1, 0]]}
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(req)


def test_non_rectangle_shape_geometry():
    angle = np.deg2rad([90, 45, 0, -90, -135, 180])
    ellipse = np.column_stack(
        [0.4 * np.cos(angle), np.full(6, 0.45), -1.3 + 0.2 * np.sin(angle)]
    )
    fitted = fit_surface("ellipse", ellipse, 0.01)
    assert np.allclose([fitted["width"], fitted["depth"]], [0.8, 0.4], atol=1e-5)
    circle = np.array(
        [[0, 0.45, -1.1], [0.2, 0.45, -1.3], [0, 0.45, -1.5], [-0.2, 0.45, -1.3]]
    )
    fitted = fit_surface("circle", circle, 0.01)
    assert np.isclose(fitted["width"], 0.4)
    polygon = np.array(
        [
            [0, 0.45, -1],
            [0.6, 0.45, -1],
            [0.6, 0.45, -1.2],
            [0.3, 0.45, -1.2],
            [0.3, 0.45, -1.5],
            [0, 0.45, -1.5],
        ]
    )
    fitted = fit_surface("polygon", polygon, 0.01)
    assert np.allclose(sorted([fitted["width"], fitted["depth"]]), [0.5, 0.6])


def test_alignment_and_scene_reference_validation():
    d = example.fixture().model_dump()
    d["alignment"]["T_enu_board"][0] = 2
    with pytest.raises(ValidationError):
        MeasureRequest.model_validate(d)
    d = example.fixture().model_dump()
    d["floor"] = {"board_origin_height_m": 2, "up_board": [0, -1, 0]}
    with pytest.raises(ValueError, match="disagrees"):
        measure(MeasureRequest.model_validate(d))
    a = measure(example.fixture())
    b = copy.deepcopy(a)
    b["furniture_id"] = "chair_01"
    b["reference"]["space_id"] = "other"
    with pytest.raises(ValueError):
        compose_scene([a, b])
    with pytest.raises(ValueError, match="Duplicate"):
        compose_scene([a, a])


def test_real_image_detector_and_pixel_dimensions():
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    image = np.full((800, 800), 255, np.uint8)
    for row, ids in enumerate([[7, 8], [9, 10]]):
        for col, code in enumerate(ids):
            y, x = 100 + row * 300, 100 + col * 300
            image[y : y + 240, x : x + 240] = cv2.aruco.generateImageMarker(
                dictionary, code, 240
            )
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    data = {
        "board": example.fixture().board.model_dump(),
        "camera": {
            "width": 800,
            "height": 800,
            "fx": 900,
            "fy": 900,
            "cx": 400,
            "cy": 400,
            "pixel_space": "rectified",
            "distortion": [],
            "source": "synthetic",
            "lens_id": "test",
        },
        "image_base64": base64.b64encode(encoded).decode(),
    }
    from sunsync_registration.models import DetectRequest

    result = detect(DetectRequest.model_validate(data))
    assert {v["marker_id"] for v in result["detections"]} == {7, 8, 9, 10}
    data["camera"]["width"] = 799
    with pytest.raises(ValueError, match="dimensions"):
        detect(DetectRequest.model_validate(data))


def test_http_end_to_end():
    client = TestClient(app)
    request = example.fixture()
    assert client.get("/health").status_code == 200
    planned = client.post("/v1/plans", json=request.plan.model_dump())
    assert planned.status_code == 200
    response = client.post("/v1/measurements", json=request.model_dump())
    assert response.status_code == 200
    assert response.json()["model"]["dimensions_m"] == pytest.approx(
        [0.6, 0.45, 0.75], abs=1e-5
    )
    scene = client.post("/v1/scenes", json={"measurements": [response.json()]})
    assert scene.status_code == 200
    assert scene.json()["coordinate_frame"] == "ENU"
    assert (
        client.post("/v1/fr06", json={"measurement": response.json()}).status_code
        == 200
    )
    d = request.model_dump()
    d["captures"][0]["point_id"] = "wrong"
    assert client.post("/v1/measurements", json=d).status_code == 422


def test_guided_session_automatic_point_assignment_and_retake():
    from sunsync_registration.session import CaptureSession

    r = example.fixture()
    session = CaptureSession(r.plan, r.board)
    assert session.current_step["point_id"] == "P1"
    c = r.captures[0]
    ticket = session.begin_capture("failed")
    assert not session.accept(
        "failed",
        ticket["point_id"],
        c.camera,
        {"pose": {"status": "insufficient_markers"}},
    )
    assert len(session.captures) == 0
    for capture in r.captures:
        result = {
            "detections": [d.model_dump() for d in capture.detections],
            "pose": estimate(r.board, capture.camera, capture.detections),
        }
        ticket = session.begin_capture(capture.capture_id)
        assert session.accept(
            capture.capture_id, ticket["point_id"], capture.camera, result
        )
    assert session.current_step is None
    assert measure(session.measurement_request(alignment=r.alignment))["model"]
    session.retake("P2")
    assert session.current_step["point_id"] == "P2"
    with pytest.raises(ValueError):
        session.measurement_request()


def test_offset_correction_and_highest_point_and_manual_height():
    req = example.fixture().model_dump()
    # Same offset in each camera frame can change geometry; validate exact transform formula.
    req["captures"][0]["camera_to_point_m"] = [0, 0.01, 0]
    r = MeasureRequest.model_validate(req)
    capture = r.captures[0]
    pose = estimate(r.board, capture.camera, capture.detections)
    from sunsync_registration.models import rigid

    t = rigid(pose["T_board_camera"])
    expected = t[:3, :3] @ capture.camera_to_point_m + t[:3, 3]
    req["plan"]["samples_per_point"] = 1
    req["captures"] = [req["captures"][i] for i in [0, 3, 6, 9]]
    result = measure(MeasureRequest.model_validate(req))
    assert np.allclose(result["points"]["P1"]["raw_point_board_m"], expected)
    req = example.fixture().model_dump()
    req["plan"].update(height_mode="manual", manual_height_m=0.9)
    assert (
        measure(MeasureRequest.model_validate(req))["model"]["dimensions_m"][2] == 0.9
    )
    req["plan"].update(height_mode="capture_highest", manual_height_m=None)
    result = measure(MeasureRequest.model_validate(req))
    assert (
        result["model"] is None
        and result["points"]["H1"]["status"] == "insufficient_samples"
    )


def test_fr06_marker_frame_conversion_and_missing_alignment():
    from sunsync_registration.fr06 import export_fragment
    from sunsync_registration.models import rigid

    r = measure(example.fixture())
    fragment = export_fragment(r)
    marker = fragment["markers"][0]
    obs = fragment["observations"][0]
    t = rigid(marker["T_ENU_marker"])
    transformed = np.array(obs["points_marker_m"]) @ t[:3, :3].T + t[:3, 3]
    assert np.allclose(transformed, obs["points_enu_m"])
    assert fragment["occluders"][0]["quality"]["confirmed"] is False
    req = example.fixture().model_dump()
    req["alignment"] = None
    req["floor"] = {"board_origin_height_m": 1.2, "up_board": [0, -1, 0]}
    with pytest.raises(ValueError, match="ENU"):
        export_fragment(measure(MeasureRequest.model_validate(req)))


def test_highest_point_height_and_multiple_furniture():
    req = example.fixture().model_dump()
    req["plan"]["height_mode"] = "capture_highest"
    camera = example.fixture().captures[0].camera
    k, d = camera.matrices()
    pos = np.array([0, -0.1, -1.4])
    f = np.array([0.125, 0.125, 0]) - pos
    f /= np.linalg.norm(f)
    right = np.cross([0, 1, 0], f)
    right /= np.linalg.norm(right)
    rotation = np.array([right, np.cross(f, right), f])
    rv, _ = cv2.Rodrigues(rotation)
    detections = []
    for code, obj in example.fixture().board.objects().items():
        px, _ = cv2.projectPoints(obj, rv, -rotation @ pos, k, d)
        detections.append({"marker_id": code, "corners_px": px.reshape(4, 2).tolist()})
    for i in range(3):
        req["captures"].append(
            {
                "capture_id": f"H1-{i}",
                "point_id": "H1",
                "camera": camera.model_dump(),
                "detections": detections,
            }
        )
    a = measure(MeasureRequest.model_validate(req))
    assert a["model"]["dimensions_m"][2] == pytest.approx(1.3, abs=1e-5)
    other = example.fixture().model_dump()
    other["plan"]["furniture_id"] = "desk_02"
    b = measure(MeasureRequest.model_validate(other))
    assert len(compose_scene([a, b])["furniture"]) == 2


def test_inconsistent_repeated_placement_and_wrong_enu_model():
    d = example.fixture().model_dump()
    d["captures"][0]["camera_to_point_m"] = [0.2, 0, 0]
    r = measure(MeasureRequest.model_validate(d))
    assert r["points"]["P1"]["status"] == "inconsistent_samples"
    assert r["model"] is None
    r = measure(example.fixture())
    r["model"]["T_enu_model"][3] += 0.1
    with pytest.raises(ValueError, match="inconsistent"):
        compose_scene([r])
