"""Synthetic test observations with independent jitter and pixel noise, not real accuracy evidence."""

import importlib.util
from pathlib import Path

import cv2
import numpy as np

spec = importlib.util.spec_from_file_location(
    "base_fixture", Path(__file__).with_name("build_synthetic_example.py")
)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def noisy_fixture(
    seed=701, noise=0.12, count=8, outlier=False, kind="rectangle", point_offsets=None
):
    request = example.fixture(single=True).model_dump()
    if kind == "rectangle":
        xy = np.array([[-0.3, 0.225], [0.3, 0.225], [0.3, -0.225], [-0.3, -0.225]])
    elif kind == "circle":
        a = np.arange(4) * np.pi / 2
        xy = np.column_stack([0.3 * np.cos(a), 0.3 * np.sin(a)])
    elif kind == "ellipse":
        a = np.deg2rad([90, 45, 0, -90, -135, 180])
        xy = np.column_stack([0.3 * np.cos(a), 0.2 * np.sin(a)])
    else:
        xy = np.array([[-0.3, 0.2], [0.3, 0.2], [0.3, 0], [0, 0], [0, -0.2], [-0.3, -0.2]])
    request["plan"]["shape"] = {
        "kind": kind,
        **({"vertices": xy.tolist()} if kind == "polygon" else {}),
    }
    truth = np.column_stack([xy[:, 0], np.full(len(xy), 0.45), xy[:, 1] - 1.425])
    base = example.fixture(single=True)
    camera = base.captures[0].camera
    k, d = camera.matrices()
    rng = np.random.default_rng(seed)
    captures = []
    positions = truth if point_offsets is None else truth + np.asarray(point_offsets)
    for index, position in enumerate(positions):
        for sample in range(count):
            pos = position + rng.normal(0, 0.002, 3)
            if outlier and sample == count - 1:
                pos += np.array([0.09, 0, 0])
            f = np.array([0.3, 0.3, 0]) + rng.normal(0, 0.03, 3) - pos
            f /= np.linalg.norm(f)
            right = np.cross([0, 1, 0], f)
            right /= np.linalg.norm(right)
            down = np.cross(f, right)
            rotation = np.array([right, down, f])
            r = cv2.Rodrigues(rotation)[0]
            t = -rotation @ pos
            detections = []
            for code, obj in base.board.objects().items():
                pixels = cv2.projectPoints(obj, r, t, k, d)[0].reshape(4, 2) + rng.normal(
                    0, noise, (4, 2)
                )
                detections.append({"marker_id": code, "corners_px": pixels.tolist()})
            captures.append(
                {
                    "capture_id": f"P{index + 1}-{sample}",
                    "point_id": f"P{index + 1}",
                    "captured_at": "2026-11-10T01:00:00Z",
                    "camera": camera.model_dump(),
                    "detections": detections,
                }
            )
    request["captures"] = captures
    return request, truth
