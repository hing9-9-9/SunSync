"""Production film configuration and its measurement/ENU contract."""

import importlib.util
from pathlib import Path
import base64
import cv2
import numpy as np
import pytest
from sunsync_registration.models import Board, DetectRequest
from sunsync_registration.pose import detect, estimate
from sunsync_registration.core import measure
from sunsync_registration.fr06 import export_fragment


def fixture():
    path = Path(__file__).parents[1] / "examples/build_synthetic_example.py"
    spec = importlib.util.spec_from_file_location("single_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fixture(single=True)


def test_single_marker_recovers_metric_dimensions_and_enu_reference():
    request = fixture()
    result = measure(request)
    assert result["model"]["dimensions_m"] == pytest.approx([0.6, 0.45, 0.75], abs=1e-5)
    fragment = export_fragment(result)
    marker = fragment["markers"][0]
    assert marker["code_id"] == 0 and marker["side_length_m"] == 0.6
    te_m = np.array(marker["T_ENU_marker"]).reshape(4, 4)
    te_b = np.array(request.alignment.T_enu_board).reshape(4, 4)
    assert te_m[:3, 3] == pytest.approx((te_b @ [0.3, 0.3, 0, 1])[:3])
    assert te_m[:3, :3] == pytest.approx(te_b[:3, :3] @ np.diag([1, -1, -1]))


def test_detects_one_marker_and_does_not_accept_wrong_or_missing_id():
    image = np.full((1440, 1920), 255, np.uint8)
    image[500:800, 800:1100] = cv2.aruco.generateImageMarker(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 0, 300
    )
    payload = DetectRequest(
        board=Board(board_id="film"),
        camera=fixture().captures[0].camera,
        image_base64=base64.b64encode(cv2.imencode(".png", image)[1]).decode(),
    )
    result = detect(payload)
    assert [v["marker_id"] for v in result["detections"]] == [0]
    assert result["pose"]["status"] != "insufficient_markers"
    assert estimate(payload.board, payload.camera, [])["status"] == "insufficient_markers"
    payload.board = Board(board_id="other", ids_by_row=((7,),))
    assert detect(payload)["pose"]["status"] == "insufficient_markers"


@pytest.mark.parametrize("layout", [(), ((0, 1),), ((0, 0), (1, 2)), ((0,), (1,)), ((50,),)])
def test_invalid_layouts_are_rejected(layout):
    with pytest.raises(ValueError):
        Board(board_id="bad", ids_by_row=layout)


def test_single_marker_rejects_inter_marker_gap():
    with pytest.raises(ValueError):
        Board(board_id="bad", gap_x_m=0.04)
