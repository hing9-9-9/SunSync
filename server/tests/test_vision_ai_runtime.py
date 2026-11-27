"""Runtime behaviour of the real vision models: one shared load, one inference at a time, switch."""
# ruff: noqa: F811

from __future__ import annotations

import json
import threading
import time
from typing import Any

import numpy as np
import pytest
from app.api import vision_ai as vision_api
from app.config import vision_ai_mode
from app.models.scene import SceneRecord
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session
from test_fr03_vision_ai import (  # noqa: F401
    _make_synthetic_room_png_bytes,
    client,
    database_engine,
    scene_payload,
)

from engine.vision_ai import protocols
from engine.vision_ai.protocols import (
    DEFAULT_METRIC_DEPTH_MODEL,
    CompositeDepthEstimator,
    CompositeFurnitureDetector,
    DetectedObject2D,
    MonocularDepthAdapter,
    VisionModelUnavailable,
    YoloV8DetectorAdapter,
    _deduplicate_overlapping_detections,
    _disparity_to_relative_depth,
    shared_depth_adapter,
    shared_yolo_adapter,
)

IMAGE = np.zeros((32, 32, 3), dtype=np.uint8)


@pytest.fixture(autouse=True)
def fresh_shared_adapters(monkeypatch):
    monkeypatch.setattr(protocols, "_shared_yolo", None)
    monkeypatch.setattr(protocols, "_shared_depth", None)
    monkeypatch.delenv("SUNSYNC_YOLO_WEIGHTS", raising=False)
    monkeypatch.delenv("SUNSYNC_DEPTH_MODEL", raising=False)
    monkeypatch.delenv("SUNSYNC_VISION_AI", raising=False)


class Failing:
    def detect(self, *_args):
        raise RuntimeError("model file missing")

    def estimate_relative_depth(self, *_args):
        raise RuntimeError("model file missing")


def test_the_adapters_are_shared_not_rebuilt_per_request():
    assert shared_yolo_adapter() is shared_yolo_adapter()
    assert shared_depth_adapter() is shared_depth_adapter()
    first = CompositeFurnitureDetector(mode="on")
    second = CompositeFurnitureDetector(mode="on")
    assert first._primary is second._primary


def test_the_yolo_weights_file_comes_from_the_environment(monkeypatch):
    assert shared_yolo_adapter().model_path == "yolov8m.pt"
    monkeypatch.setattr(protocols, "_shared_yolo", None)
    monkeypatch.setenv("SUNSYNC_YOLO_WEIGHTS", "/models/custom.pt")
    assert shared_yolo_adapter().model_path == "/models/custom.pt"


def test_mode_off_never_builds_a_real_model(monkeypatch):
    monkeypatch.setattr(protocols, "_installed", lambda _name: True)
    assert CompositeFurnitureDetector(mode="off")._primary is None
    assert CompositeDepthEstimator(mode="off")._primary is None


def test_mode_auto_follows_what_is_installed(monkeypatch):
    monkeypatch.setattr(protocols, "_installed", lambda _name: False)
    assert CompositeFurnitureDetector(mode="auto")._primary is None
    assert CompositeDepthEstimator(mode="auto")._primary is None
    monkeypatch.setattr(protocols, "_installed", lambda _name: True)
    assert isinstance(CompositeFurnitureDetector(mode="auto")._primary, YoloV8DetectorAdapter)
    assert isinstance(CompositeDepthEstimator(mode="auto")._primary, MonocularDepthAdapter)


def test_mode_on_always_selects_real_adapters_even_before_they_are_loaded(monkeypatch):
    monkeypatch.setattr(protocols, "_installed", lambda _name: False)
    assert isinstance(CompositeFurnitureDetector(mode="on")._primary, YoloV8DetectorAdapter)
    assert isinstance(CompositeDepthEstimator(mode="on")._primary, MonocularDepthAdapter)


def test_auto_falls_back_quietly_but_on_reports_the_failure():
    quiet = CompositeFurnitureDetector(primary_detector=Failing(), mode="auto")
    assert quiet.detect(IMAGE) is not None
    assert quiet.last_source == "synthetic_contour"
    with pytest.raises(VisionModelUnavailable, match="model file missing"):
        CompositeFurnitureDetector(primary_detector=Failing(), mode="on").detect(IMAGE)

    assert CompositeDepthEstimator(primary=Failing(), mode="auto").estimate_relative_depth(
        IMAGE, 32, 32
    ).shape == (32, 32)
    with pytest.raises(VisionModelUnavailable):
        CompositeDepthEstimator(primary=Failing(), mode="on").estimate_relative_depth(IMAGE, 32, 32)


def test_mode_on_does_not_turn_an_empty_yolo_result_into_a_synthetic_detection():
    class Empty:
        def detect(self, *_args):
            return []

    detector = CompositeFurnitureDetector(primary_detector=Empty(), mode="on")
    assert detector.detect(IMAGE) == []
    assert detector.last_source == "yolov8"


def test_mode_on_without_the_packages_fails_visibly_at_use(monkeypatch):
    monkeypatch.setattr(protocols, "_installed", lambda _name: False)
    detector = CompositeFurnitureDetector(mode="on")  # building never raises
    with pytest.raises(VisionModelUnavailable, match="ultralytics"):
        detector.detect(IMAGE)


def test_the_model_is_loaded_once_and_inference_runs_one_at_a_time(monkeypatch):
    loads: list[int] = []
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    class FakeModel:
        def __call__(self, *_args, **_kwargs):
            with guard:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.02)
            with guard:
                active["now"] -= 1
            return []

    adapter = YoloV8DetectorAdapter()
    monkeypatch.setattr(protocols, "_installed", lambda _name: True)

    def fake_ensure() -> None:
        with adapter._lock:
            if adapter._model is None:
                loads.append(1)
                adapter._model = FakeModel()

    monkeypatch.setattr(adapter, "_ensure_loaded", fake_ensure)
    threads = [threading.Thread(target=adapter.detect, args=(IMAGE,)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(loads) == 1
    assert active["max"] == 1


def test_the_switch_reads_the_environment(monkeypatch):
    assert vision_ai_mode() == "auto"
    monkeypatch.setenv("SUNSYNC_VISION_AI", " ON ")
    assert vision_ai_mode() == "on"
    monkeypatch.setenv("SUNSYNC_VISION_AI", "maybe")
    with pytest.raises(ValueError, match="auto, on or off"):
        vision_ai_mode()


def stored_scene(engine: Engine, payload: dict[str, Any]) -> str:
    with Session(engine) as session:
        session.add(
            SceneRecord(
                scene_id=payload["scene_id"],
                revision=payload["revision"],
                scene_json=json.dumps(payload),
            )
        )
        session.commit()
    return payload["scene_id"]


def test_the_api_answers_503_when_the_required_models_are_missing(
    client: TestClient, database_engine, scene_payload, monkeypatch
):
    scene_id = stored_scene(database_engine, scene_payload)
    monkeypatch.setattr(protocols, "_installed", lambda _name: False)
    monkeypatch.setenv("SUNSYNC_VISION_AI", "on")
    response = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?apply_to_scene=false",
        content=_make_synthetic_room_png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 503
    assert "not available" in response.json()["detail"]


def test_request_experiment_mode_requires_real_models_without_an_environment_switch(
    client: TestClient, database_engine, scene_payload, monkeypatch
):
    scene_id = stored_scene(database_engine, scene_payload)
    monkeypatch.setattr(protocols, "_installed", lambda _name: False)
    response = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?mode=experiment&apply_to_scene=false",
        content=_make_synthetic_room_png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 503
    assert "not available" in response.json()["detail"]


def test_experiment_dry_run_reports_real_model_sources_with_stubbed_adapters(
    client: TestClient, database_engine, scene_payload, monkeypatch
):
    """The field-only mode must not be mistaken for a synthetic fallback in the response."""
    scene_id = stored_scene(database_engine, scene_payload)

    class RealYoloStub:
        def detect(self, _image):
            return [
                DetectedObject2D(
                    box_xyxy=(420.0, 420.0, 780.0, 580.0),
                    label="desk",
                    confidence=0.91,
                    raw_class_name="dining table",
                )
            ]

    class RealDepthStub:
        def estimate_relative_depth(self, _image, width, height):
            return np.full((height, width), 2.0, dtype=np.float32)

    detector = CompositeFurnitureDetector(primary_detector=RealYoloStub(), mode="on")
    depth = CompositeDepthEstimator(primary=RealDepthStub(), mode="on")
    selected_modes: list[str] = []

    def detector_factory(*, prefer_yolo, mode):
        assert prefer_yolo is True
        selected_modes.append(mode)
        return detector

    def depth_factory(*, prefer_monocular, mode):
        assert prefer_monocular is True
        selected_modes.append(mode)
        return depth

    monkeypatch.setattr(vision_api, "create_default_detector", detector_factory)
    monkeypatch.setattr(vision_api, "create_default_depth_estimator", depth_factory)
    response = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?mode=experiment&apply_to_scene=false",
        content=_make_synthetic_room_png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert selected_modes == ["on", "on"]
    assert body["diagnostics"]["model"]["detector_source"] == "yolov8"
    assert body["diagnostics"]["model"]["depth_source"] == "depth_anything_v2"
    assert body["diagnostics"]["model"]["yolo_adapter_selected"] is True
    assert body["diagnostics"]["model"]["depth_anything_adapter_selected"] is True
    assert body["applied_to_scene"] is False


def test_the_api_keeps_working_in_off_mode_and_reports_the_source(
    client: TestClient, database_engine, scene_payload, monkeypatch
):
    scene_id = stored_scene(database_engine, scene_payload)
    monkeypatch.setenv("SUNSYNC_VISION_AI", "off")
    response = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?apply_to_scene=false",
        content=_make_synthetic_room_png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 200, response.text
    sources = {item["detection_source"] for item in response.json()["estimated_items"]}
    assert sources == {"synthetic_contour"}


def test_dry_run_response_contains_model_aruco_depth_and_furniture_diagnostics(
    client: TestClient, database_engine, scene_payload, monkeypatch
):
    scene_id = stored_scene(database_engine, scene_payload)
    monkeypatch.setenv("SUNSYNC_VISION_AI", "off")
    response = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?apply_to_scene=false",
        content=_make_synthetic_room_png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    diagnostics = body["diagnostics"]
    assert diagnostics["model"]["detector_source"] == "synthetic_contour"
    assert diagnostics["model"]["depth_source"] == "synthetic_geometric"
    assert diagnostics["aruco"]["marker_corners_px"] is not None
    assert diagnostics["depth"]["anchor_relative_depths"]
    furniture = body["estimated_items"][0]["diagnostics"]
    assert furniture["representative_depth_rule"] in (
        "enu_pointcloud_bbox",
        "central_60_percent_median",
    )
    assert furniture["representative_depth_region_xyxy_px"] is not None
    assert furniture["valid_depth_pixel_count"] > 0
    assert set(furniture["dimension_sources"]) == {"width", "depth", "height", "position"}


def test_a_bad_switch_value_is_a_visible_server_error(
    client: TestClient, database_engine, scene_payload, monkeypatch
):
    scene_id = stored_scene(database_engine, scene_payload)
    monkeypatch.setenv("SUNSYNC_VISION_AI", "maybe")
    response = client.post(
        f"/api/scenes/{scene_id}/vision-furniture-photo?apply_to_scene=false",
        content=_make_synthetic_room_png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 503


def test_disparity_to_relative_depth_preserves_near_far_order_and_ratios():
    # High disparity = near (4.0), low disparity = far (1.0) -> far/near depth ratio = 4.0
    disparity = np.array([[4.0, 2.0], [1.0, 2.0]], dtype=np.float32)
    rel_depth = _disparity_to_relative_depth(disparity)
    assert rel_depth[0, 0] < rel_depth[0, 1] < rel_depth[1, 0]
    assert np.isclose(rel_depth[1, 0] / rel_depth[0, 0], 4.0, rtol=1e-4)
    assert np.isclose(rel_depth[0, 1] / rel_depth[0, 0], 2.0, rtol=1e-4)


def test_monocular_depth_adapter_defaults_to_metric_indoor_and_respects_env(monkeypatch):
    default_adapter = shared_depth_adapter()
    assert default_adapter.model_name == DEFAULT_METRIC_DEPTH_MODEL
    assert default_adapter.is_metric_depth is True

    monkeypatch.setattr(protocols, "_shared_depth", None)
    monkeypatch.setenv("SUNSYNC_DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Small-hf")
    rel_adapter = shared_depth_adapter()
    assert rel_adapter.model_name == "depth-anything/Depth-Anything-V2-Small-hf"
    assert rel_adapter.is_metric_depth is False


def test_monocular_depth_adapter_metric_mode_preserves_metres_directly():
    # Metric indoor model outputs depth in metres directly (near = 0.9 m, far = 2.7 m)
    pred = np.vstack(
        [np.full((16, 32), 0.9, dtype=np.float32), np.full((16, 32), 2.7, dtype=np.float32)]
    )

    class FakeMetricPipe:
        def __call__(self, _pil_img):
            return {"predicted_depth": pred, "depth": np.zeros((32, 32), dtype=np.uint8)}

    adapter = MonocularDepthAdapter()
    adapter._pipe = FakeMetricPipe()
    depth = adapter.estimate_relative_depth(IMAGE, 32, 32)
    assert np.isclose(float(np.median(depth[:16, :])), 0.9, rtol=1e-4)
    assert np.isclose(float(np.median(depth[16:, :])), 2.7, rtol=1e-4)


def test_monocular_depth_adapter_relative_mode_inverts_disparity():
    # Relative model outputs inverse depth (near = 5.0 disparity, far = 1.25 disparity)
    pred = np.vstack(
        [np.full((16, 32), 5.0, dtype=np.float32), np.full((16, 32), 1.25, dtype=np.float32)]
    )

    class FakeRelativePipe:
        def __call__(self, _pil_img):
            return {"predicted_depth": pred, "depth": np.zeros((32, 32), dtype=np.uint8)}

    adapter = MonocularDepthAdapter(model_name="depth-anything/Depth-Anything-V2-Small-hf")
    adapter._pipe = FakeRelativePipe()
    rel = adapter.estimate_relative_depth(IMAGE, 32, 32)
    near_val = float(np.median(rel[:16, :]))
    far_val = float(np.median(rel[16:, :]))
    assert near_val < far_val
    assert np.isclose(far_val / near_val, 4.0, rtol=1e-4)


def test_deduplicate_overlapping_detections_resolves_cross_class_duplicates():
    same_box = (60.0, 420.0, 560.0, 1000.0)
    near_same_box = (61.0, 422.0, 559.0, 1015.0)
    separate_box = (100.0, 220.0, 220.0, 255.0)

    # Ambiguous chair back detected as both tv (other, 0.48) and chair (0.30) -> chair wins
    deduped = _deduplicate_overlapping_detections(
        [
            DetectedObject2D(same_box, "other", 0.48, "tv"),
            DetectedObject2D(near_same_box, "chair", 0.30, "chair"),
            DetectedObject2D(separate_box, "bookshelf", 0.27, "book"),
        ]
    )
    assert [d.label for d in deduped] == ["chair", "bookshelf"]

    # High-confidence appliance (tv, 0.88) beats weak spurious chair (0.28) on same box
    tv_wins = _deduplicate_overlapping_detections(
        [
            DetectedObject2D(same_box, "other", 0.88, "tv"),
            DetectedObject2D(near_same_box, "chair", 0.28, "chair"),
        ]
    )
    assert [d.label for d in tv_wins] == ["other"]

    # Two overlapping specific furniture classes -> higher confidence wins
    specific = _deduplicate_overlapping_detections(
        [
            DetectedObject2D(same_box, "chair", 0.55, "chair"),
            DetectedObject2D(near_same_box, "desk", 0.72, "dining table"),
        ]
    )
    assert [d.label for d in specific] == ["desk"]


def test_overlaps_any_box_keeps_large_furniture_touching_small_marker():
    marker_box = (128.0, 157.0, 199.0, 234.0)
    large_chair_box = (98.7, 193.3, 564.6, 1023.7)
    marker_contour_box = (124.0, 154.0, 203.0, 238.0)
    inner_marker_bit = (140.0, 170.0, 180.0, 210.0)
    host_tv_box = (40.0, 90.0, 360.0, 310.0)

    assert protocols._overlaps_any_box(large_chair_box, [marker_box]) is False
    assert protocols._overlaps_any_box(marker_contour_box, [marker_box]) is True
    assert protocols._overlaps_any_box(inner_marker_bit, [marker_box]) is True
    # P0-3: TV display hosting >=80% of ArUco marker excluded when exclude_container=True
    assert protocols._overlaps_any_box(host_tv_box, [marker_box], exclude_container=True) is True
    assert (
        protocols._overlaps_any_box(large_chair_box, [marker_box], exclude_container=True) is False
    )


def test_monocular_depth_adapter_resizes_large_input_to_1024_and_restores_output_shape():
    seen_sizes: list[tuple[int, int]] = []

    class _SpyPipe:
        def __call__(self, pil_img):
            seen_sizes.append(pil_img.size)
            w, h = pil_img.size
            return {"predicted_depth": np.full((h, w), 2.5, dtype=np.float32)}

    big_img = np.zeros((3024, 4032, 3), dtype=np.uint8)
    adapter = MonocularDepthAdapter()
    adapter._pipe = _SpyPipe()
    depth = adapter.estimate_relative_depth(big_img, 4032, 3024)
    assert seen_sizes == [(1024, 768)]
    assert depth.shape == (3024, 4032)
    assert np.isclose(float(np.median(depth)), 2.5, rtol=1e-4)
