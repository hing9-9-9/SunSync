"""Synthetic fixture only. Does not demonstrate real-world accuracy."""

import json
from pathlib import Path
import cv2
import numpy as np
from sunsync_registration.models import Camera, Board, PlanRequest, MeasureRequest
from sunsync_registration.core import plan, measure, compose_scene
from sunsync_registration.fr06 import export_fragment


def fixture():
    camera = Camera(
        width=1920,
        height=1440,
        fx=1400,
        fy=1400,
        cx=960,
        cy=720,
        pixel_space="rectified",
        distortion=[],
        source="synthetic_fixture",
        lens_id="synthetic_camera",
    )
    board = Board(
        board_id="window_board_01", marker_side_m=0.115, gap_x_m=0.02, gap_y_m=0.02
    )
    request = PlanRequest(
        space_id="room_01",
        furniture_id="desk_01",
        furniture_kind="desk",
        model_id="catalog/desk_normalized",
        shape={"kind": "rectangle"},
    )
    positions = [
        [-0.3, 0.45, -1.2],
        [0.3, 0.45, -1.2],
        [0.3, 0.45, -1.65],
        [-0.3, 0.45, -1.65],
    ]
    captures = []
    k, d = camera.matrices()
    for index, position in enumerate(positions):
        pos = np.array(position)
        f = np.array([0.125, 0.125, 0]) - pos
        f /= np.linalg.norm(f)
        right = np.cross([0, 1, 0], f)
        right /= np.linalg.norm(right)
        down = np.cross(f, right)
        r = np.array([right, down, f])
        rv, _ = cv2.Rodrigues(r)
        tv = -r @ pos
        detections = []
        for code, obj in board.objects().items():
            px, _ = cv2.projectPoints(obj, rv, tv, k, d)
            detections.append(
                {"marker_id": code, "corners_px": px.reshape(4, 2).tolist()}
            )
        for sample in range(3):
            captures.append(
                {
                    "capture_id": f"P{index+1}-{sample+1}",
                    "point_id": f"P{index+1}",
                    "captured_at": f"2026-11-04T10:00:{index * 3 + sample:02d}+09:00",
                    "camera": camera.model_dump(),
                    "detections": detections,
                }
            )
    # Board x=east, y=down, z=north, board origin at ENU height 1.2m.
    transform = np.array(
        [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 1.2], [0, 0, 0, 1]], float
    )
    return MeasureRequest(
        plan=request,
        board=board,
        captures=captures,
        alignment={
            "T_enu_board": transform.ravel().tolist(),
            "north_confirmed": True,
            "floor_confirmed": True,
        },
    )


if __name__ == "__main__":
    dest = Path(__file__).parent
    value = fixture()
    result = measure(value)
    for name, data in [
        ("plan-request.json", value.plan.model_dump()),
        ("plan-response.json", plan(value.plan)),
        ("measurement-request.json", value.model_dump()),
        ("measurement-response.json", result),
        ("scene-response.json", compose_scene([result])),
        ("fr06-fragment.json", export_fragment(result)),
    ]:
        (dest / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(result["status"], result["model"]["dimensions_m"])
