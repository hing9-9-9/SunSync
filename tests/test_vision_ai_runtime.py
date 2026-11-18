"""Runtime behaviour of the real vision models: one shared load, one inference at a time, switch."""
# ruff: noqa: F811

from __future__ import annotations

import json
import threading
import time
from typing import Any

import numpy as np
import pytest
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
    CompositeDepthEstimator,
    CompositeFurnitureDetector,
    MonocularDepthAdapter,
    VisionModelUnavailable,
    YoloV8DetectorAdapter,
    shared_depth_adapter,
    shared_yolo_adapter,
)

IMAGE = np.zeros((32, 32, 3), dtype=np.uint8)


@pytest.fixture(autouse=True)
def fresh_shared_adapters(monkeypatch):
    monkeypatch.setattr(protocols, "_shared_yolo", None)
    monkeypatch.setattr(protocols, "_shared_depth", None)
    monkeypatch.delenv("SUNSYNC_YOLO_WEIGHTS", raising=False)
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
