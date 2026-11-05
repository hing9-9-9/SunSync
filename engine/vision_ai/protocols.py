"""Protocols and adapters for vision-based furniture classification and depth estimation."""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, runtime_checkable

import numpy as np

FurnitureLabel = Literal["bookshelf", "desk", "chair", "other"]


@dataclass(frozen=True)
class DetectedObject2D:
    """A 2D bounding box detection with furniture class and confidence."""

    box_xyxy: tuple[float, float, float, float]  # pixel coordinates (x1, y1, x2, y2)
    label: FurnitureLabel
    confidence: float
    raw_class_name: str = ""


@runtime_checkable
class ObjectDetectorProtocol(Protocol):
    """Protocol for 2D furniture detection models."""

    def detect(
        self,
        image_rgb: np.ndarray | None,
        hints: Sequence[DetectedObject2D] | None = None,
    ) -> list[DetectedObject2D]:
        """Detect furniture objects in the image or passthrough/refine hints."""
        ...


@runtime_checkable
class DepthEstimatorProtocol(Protocol):
    """Protocol for monocular depth estimation models."""

    def estimate_relative_depth(
        self,
        image_rgb: np.ndarray | None,
        width: int,
        height: int,
    ) -> np.ndarray:
        """Estimate relative depth map of shape (height, width) as float32."""
        ...


class GeometricSyntheticDetector:
    """Deterministic 2D furniture detector for CI tests and synthetic experiments.

    Supports:
    1. Passthrough / refinement of pre-extracted 2D bounding boxes (hints).
    2. Contour-based detection of colored rectangular objects on synthetic images.
    """

    def __init__(
        self,
        default_detections: Sequence[DetectedObject2D] | None = None,
        min_confidence: float = 0.5,
    ) -> None:
        self.default_detections = list(default_detections) if default_detections else []
        self.min_confidence = min_confidence

    def detect(
        self,
        image_rgb: np.ndarray | None,
        hints: Sequence[DetectedObject2D] | None = None,
    ) -> list[DetectedObject2D]:
        if hints is not None and len(hints) > 0:
            return [hint for hint in hints if hint.confidence >= self.min_confidence]

        if image_rgb is not None and len(self.default_detections) == 0:
            import cv2

            gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
            thresh = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
            )
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            detected: list[DetectedObject2D] = []
            h_img, w_img = image_rgb.shape[:2]
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                area = w * h
                if 400 < area < (w_img * h_img * 0.9):
                    aspect = w / max(1, h)
                    label: FurnitureLabel = "desk" if aspect > 1.2 else "bookshelf"
                    detected.append(
                        DetectedObject2D(
                            box_xyxy=(float(x), float(y), float(x + w), float(y + h)),
                            label=label,
                            confidence=0.85,
                            raw_class_name=f"synthetic_contour_{label}",
                        )
                    )
            if detected:
                return detected

        return list(self.default_detections)


class GeometricSyntheticDepthEstimator:
    """Deterministic relative depth estimator for CI and synthetic simulations.

    Generates smooth planar/gradient depth maps with foreground object patches
    at configured relative depths.
    """

    def __init__(
        self,
        background_depth: float = 5.0,
        object_depths: dict[tuple[int, int, int, int], float] | None = None,
    ) -> None:
        self.background_depth = background_depth
        self.object_depths = object_depths or {}

    def estimate_relative_depth(
        self,
        image_rgb: np.ndarray | None,
        width: int,
        height: int,
    ) -> np.ndarray:
        y_indices = np.linspace(0.8, 1.2, height, dtype=np.float32)[:, None]
        depth_map = np.ones((height, width), dtype=np.float32) * self.background_depth * y_indices

        for (x1, y1, x2, y2), d_val in self.object_depths.items():
            cx1 = max(0, min(width - 1, x1))
            cy1 = max(0, min(height - 1, y1))
            cx2 = max(0, min(width, x2))
            cy2 = max(0, min(height, y2))
            if cx2 > cx1 and cy2 > cy1:
                depth_map[cy1:cy2, cx1:cx2] = float(d_val)

        return depth_map


class YoloV8DetectorAdapter:
    """YOLOv8 adapter for real photo furniture detection experiments.

    Lazy-loads `ultralytics` when invoked and gracefully informs the user
    if dependencies or weights are not installed.
    """

    COCO_TO_FURNITURE: ClassVar[dict[str, FurnitureLabel]] = {
        "chair": "chair",
        "couch": "chair",
        "sofa": "chair",
        "bed": "desk",
        "dining table": "desk",
        "desk": "desk",
        "book": "bookshelf",
        "bookshelf": "bookshelf",
    }

    def __init__(self, model_path: str = "yolov8m.pt", conf_thresh: float = 0.35) -> None:
        self.model_path = model_path
        self.conf_thresh = conf_thresh
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if importlib.util.find_spec("ultralytics") is None:
            raise RuntimeError(
                "YOLOv8 dependency 'ultralytics' is not installed. "
                "Install it via `pip install ultralytics` to run real-photo experiments."
            )
        from ultralytics import YOLO  # type: ignore[import-untyped]

        self._model = YOLO(self.model_path)

    def detect(
        self,
        image_rgb: np.ndarray | None,
        hints: Sequence[DetectedObject2D] | None = None,
    ) -> list[DetectedObject2D]:
        if hints is not None and len(hints) > 0:
            return list(hints)

        if image_rgb is None:
            return []

        self._ensure_loaded()
        assert self._model is not None

        results = self._model(image_rgb, conf=self.conf_thresh, verbose=False)
        detections: list[DetectedObject2D] = []

        for r in results:
            boxes = r.boxes
            if boxes is None:
                continue
            for box in boxes:
                cls_idx = int(box.cls[0].item())
                raw_name = r.names.get(cls_idx, "unknown").lower()
                conf = float(box.conf[0].item())
                xyxy = tuple(float(v) for v in box.xyxy[0].tolist())

                if raw_name in self.COCO_TO_FURNITURE:
                    label = self.COCO_TO_FURNITURE[raw_name]
                    detections.append(
                        DetectedObject2D(
                            box_xyxy=(xyxy[0], xyxy[1], xyxy[2], xyxy[3]),
                            label=label,
                            confidence=conf,
                            raw_class_name=raw_name,
                        )
                    )
        return detections


class MonocularDepthAdapter:
    """Monocular depth adapter supporting Depth Anything V2 or MiDaS via torch / transformers.

    Lazy-loads PyTorch/Transformers models and provides clear fallback messages in CI.
    """

    def __init__(
        self,
        model_name: str = "depth-anything-v2-small",
        device: str = "cpu",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._pipe = None

    def _ensure_loaded(self) -> None:
        if self._pipe is not None:
            return
        if (
            importlib.util.find_spec("torch") is None
            or importlib.util.find_spec("transformers") is None
        ):
            raise RuntimeError(
                "PyTorch / HuggingFace Transformers not found. "
                "Install `torch` and `transformers` to run real monocular depth models."
            )
        from transformers import pipeline  # type: ignore[import-untyped]

        self._pipe = pipeline(
            task="depth-estimation",
            model="depth-anything/Depth-Anything-V2-Small-hf",
            device=self.device,
        )

    def estimate_relative_depth(
        self,
        image_rgb: np.ndarray | None,
        width: int,
        height: int,
    ) -> np.ndarray:
        if image_rgb is None:
            return np.ones((height, width), dtype=np.float32)

        self._ensure_loaded()
        assert self._pipe is not None

        from PIL import Image

        pil_image = Image.fromarray(image_rgb)
        result = self._pipe(pil_image)
        depth_pil = result["depth"]
        depth_arr = np.array(depth_pil, dtype=np.float32)

        import cv2

        if depth_arr.shape[:2] != (height, width):
            depth_arr = cv2.resize(depth_arr, (width, height), interpolation=cv2.INTER_LINEAR)

        min_v, max_v = float(depth_arr.min()), float(depth_arr.max())
        if max_v - min_v > 1e-4:
            depth_arr = (depth_arr - min_v) / (max_v - min_v) * 9.9 + 0.1
        return depth_arr.astype(np.float32)
