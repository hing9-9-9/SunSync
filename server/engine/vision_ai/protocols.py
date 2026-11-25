"""Protocols and adapters for vision-based furniture classification and depth estimation."""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

import numpy as np

FurnitureLabel = Literal["bookshelf", "desk", "chair", "other"]
DetectionSource = Literal["yolov8", "synthetic_contour", "client_hint"]
DepthEstimationSource = Literal["depth_anything_v2", "synthetic_geometric"]
# auto: use the real models when they are installed and fall back quietly (the default);
# on: the real models are required and a failure is raised; off: never use them.
VisionMode = Literal["auto", "on", "off"]

logger = logging.getLogger(__name__)


class VisionModelUnavailable(RuntimeError):
    """The real models are required (mode "on") but are not installed or failed to run."""


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


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
        exclude_boxes: Sequence[tuple[float, float, float, float]] | None = None,
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
                    box = (float(x), float(y), float(x + w), float(y + h))
                    if exclude_boxes and _overlaps_any_box(box, exclude_boxes):
                        continue
                    aspect = w / max(1, h)
                    label: FurnitureLabel = "desk" if aspect > 1.2 else "bookshelf"
                    detected.append(
                        DetectedObject2D(
                            box_xyxy=box,
                            label=label,
                            confidence=0.85,
                            raw_class_name=f"synthetic_contour_{label}",
                        )
                    )
            if detected:
                return detected

        return list(self.default_detections)


def _overlaps_any_box(
    box: tuple[float, float, float, float],
    exclude_boxes: Sequence[tuple[float, float, float, float]],
) -> bool:
    """Return True if a candidate box represents an excluded region (e.g. an ArUco marker).

    Checks overlap relative to the *candidate* box area so that a large foreground
    furniture box whose corner merely touches a small background window marker is
    not erroneously discarded.
    """
    bx1, by1, bx2, by2 = box
    cx, cy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
    b_area = max(1.0, (bx2 - bx1) * (by2 - by1))
    for ex1, ey1, ex2, ey2 in exclude_boxes:
        e_area = max(1.0, (ex2 - ex1) * (ey2 - ey1))
        if ex1 <= cx <= ex2 and ey1 <= cy <= ey2 and b_area <= e_area * 4.0:
            return True
        ix1, iy1 = max(bx1, ex1), max(by1, ey1)
        ix2, iy2 = min(bx2, ex2), min(by2, ey2)
        if ix2 > ix1 and iy2 > iy1:
            inter = (ix2 - ix1) * (iy2 - iy1)
            if inter / b_area > 0.4:
                return True
    return False


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


def _box_iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Compute intersection-over-union between two xyxy bounding boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _deduplicate_overlapping_detections(
    detections: list[DetectedObject2D],
    iou_thresh: float = 0.65,
) -> list[DetectedObject2D]:
    """Suppress duplicate cross-class boxes on the same object (class-agnostic NMS).

    When a specific furniture class (bookshelf/desk/chair) and generic ``other``
    (e.g. ``tv``) fire on the same bounding box with comparable confidence,
    prefer the specific furniture class while letting high-confidence ``other``
    detections win on genuinely non-furniture appliances.
    """
    if len(detections) <= 1:
        return detections

    def _priority(det: DetectedObject2D) -> tuple[float, float]:
        bonus = 0.20 if det.label != "other" else 0.0
        return (det.confidence + bonus, det.confidence)

    ordered = sorted(detections, key=_priority, reverse=True)
    kept: list[DetectedObject2D] = []
    for candidate in ordered:
        if any(_box_iou(candidate.box_xyxy, prev.box_xyxy) >= iou_thresh for prev in kept):
            continue
        kept.append(candidate)
    return kept


def _disparity_to_relative_depth(disparity: np.ndarray) -> np.ndarray:
    """Convert positive inverse depth (disparity) to scale-preserving relative depth.

    Depth Anything V2 outputs affine-invariant inverse depth (larger = closer).
    Inverting ``1 / max(disp, floor)`` and dividing by its median preserves the
    zero origin required by proportional ArUco calibration (``Z = alpha * d_rel``).
    """
    arr = np.asarray(disparity, dtype=np.float32)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    p95 = float(np.percentile(arr, 95)) if arr.size > 0 else 0.0
    floor = max(p95 * 0.02, 1e-3)
    inv = 1.0 / np.maximum(arr, floor)
    median = float(np.median(inv)) if inv.size > 0 else 1.0
    if not np.isfinite(median) or median <= 1e-6:
        return np.ones_like(arr, dtype=np.float32)
    return (inv / median).astype(np.float32)


def _merge_book_clusters(detections: list[DetectedObject2D]) -> list[DetectedObject2D]:
    """Merge clusters of detected books into unified bookshelf bounding boxes."""
    books = [det for det in detections if det.raw_class_name == "book"]
    if len(books) <= 1:
        return detections
    non_books = [det for det in detections if det.raw_class_name != "book"]

    visited = [False] * len(books)
    merged_books: list[DetectedObject2D] = []

    def _near(a: DetectedObject2D, b: DetectedObject2D) -> bool:
        ax1, ay1, ax2, ay2 = a.box_xyxy
        bx1, by1, bx2, by2 = b.box_xyxy
        pad = max(ax2 - ax1, ay2 - ay1, bx2 - bx1, by2 - by1) * 0.4 + 25.0
        return not (ax2 + pad < bx1 or bx2 + pad < ax1 or ay2 + pad < by1 or by2 + pad < ay1)

    for i in range(len(books)):
        if visited[i]:
            continue
        stack = [i]
        visited[i] = True
        cluster: list[DetectedObject2D] = []
        while stack:
            cur = stack.pop()
            cluster.append(books[cur])
            for j in range(len(books)):
                if not visited[j] and _near(books[cur], books[j]):
                    visited[j] = True
                    stack.append(j)
        if len(cluster) == 1:
            merged_books.append(cluster[0])
        else:
            x1 = min(d.box_xyxy[0] for d in cluster)
            y1 = min(d.box_xyxy[1] for d in cluster)
            x2 = max(d.box_xyxy[2] for d in cluster)
            y2 = max(d.box_xyxy[3] for d in cluster)
            conf = float(min(max(max(d.confidence for d in cluster), 0.0), 1.0))
            merged_books.append(
                DetectedObject2D(
                    box_xyxy=(x1, y1, x2, y2),
                    label="bookshelf",
                    confidence=conf,
                    raw_class_name="book_cluster_bookshelf",
                )
            )
    return [*non_books, *merged_books]


class YoloV8DetectorAdapter:
    """YOLOv8 adapter for real photo furniture detection experiments.

    Lazy-loads `ultralytics` when invoked and gracefully informs the user
    if dependencies or weights are not installed.
    """

    COCO_TO_FURNITURE: ClassVar[dict[str, FurnitureLabel]] = {
        "chair": "chair",
        "couch": "chair",
        "sofa": "chair",
        "bench": "chair",
        "bed": "desk",
        "dining table": "desk",
        "desk": "desk",
        "book": "bookshelf",
        "bookshelf": "bookshelf",
        "refrigerator": "other",
        "tv": "other",
        "microwave": "other",
        "oven": "other",
    }

    def __init__(self, model_path: str = "yolov8m.pt", conf_thresh: float = 0.25) -> None:
        self.model_path = model_path
        self.conf_thresh = conf_thresh
        self._model: Any = None
        # The adapter is shared by all requests: load once, run one inference at a time.
        self._lock = threading.RLock()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not _installed("ultralytics"):
                raise RuntimeError(
                    "YOLOv8 dependency 'ultralytics' is not installed. "
                    "Install it via `pip install ultralytics` to run real-photo experiments."
                )
            from ultralytics import YOLO  # type: ignore[import-untyped]

            self._model = YOLO(self.model_path)
            logger.info("vision model loaded: YOLOv8 from %s", self.model_path)

    def detect(
        self,
        image_rgb: np.ndarray | None,
        hints: Sequence[DetectedObject2D] | None = None,
    ) -> list[DetectedObject2D]:
        if hints is not None and len(hints) > 0:
            return list(hints)

        if image_rgb is None:
            return []

        with self._lock:
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
        return _deduplicate_overlapping_detections(_merge_book_clusters(detections))


class CompositeFurnitureDetector:
    """Primary YOLOv8 detector with automatic fallback to contour heuristics for synthetic/empty scenes."""

    def __init__(
        self,
        *,
        prefer_yolo: bool = True,
        primary_detector: ObjectDetectorProtocol | None = None,
        fallback_detector: GeometricSyntheticDetector | None = None,
        mode: VisionMode = "auto",
    ) -> None:
        self._mode = mode
        if primary_detector is not None:
            self._primary: ObjectDetectorProtocol | None = primary_detector
        elif mode == "on" or (mode == "auto" and prefer_yolo and _installed("ultralytics")):
            self._primary = shared_yolo_adapter()
        else:
            self._primary = None
        self._fallback = fallback_detector or GeometricSyntheticDetector()
        self.last_source: DetectionSource = "synthetic_contour"

    def detect(
        self,
        image_rgb: np.ndarray | None,
        hints: Sequence[DetectedObject2D] | None = None,
        exclude_boxes: Sequence[tuple[float, float, float, float]] | None = None,
    ) -> list[DetectedObject2D]:
        if hints is not None and len(hints) > 0:
            self.last_source = "client_hint"
            return list(hints)

        if self._primary is not None and image_rgb is not None:
            try:
                raw = self._primary.detect(image_rgb)
                if exclude_boxes:
                    raw = [d for d in raw if not _overlaps_any_box(d.box_xyxy, exclude_boxes)]
                if len(raw) > 0:
                    self.last_source = "yolov8"
                    return raw
            except (RuntimeError, OSError, ValueError) as error:
                if self._mode == "on":
                    raise VisionModelUnavailable(f"YOLOv8 detection failed: {error}") from error

        # Strict mode is used by a field experiment.  An empty genuine YOLO
        # result must remain empty so a contour-derived object cannot be
        # mistaken for an AI result.
        if self._mode == "on":
            self.last_source = "yolov8"
            return []

        self.last_source = "synthetic_contour"
        return self._fallback.detect(image_rgb, exclude_boxes=exclude_boxes)


DEFAULT_METRIC_DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"


class MonocularDepthAdapter:
    """Monocular depth adapter supporting Depth Anything V2 (metric indoor or relative disparity).

    Lazy-loads PyTorch/Transformers models and provides clear fallback messages in CI.
    Metric indoor checkpoints predict depth directly in metres (smaller = nearer),
    whereas relative checkpoints predict affine-invariant inverse depth (disparity)
    and are converted via ``_disparity_to_relative_depth``.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_METRIC_DEPTH_MODEL,
        device: str = "cpu",
        is_metric_depth: bool | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.is_metric_depth = (
            ("metric" in model_name.lower()) if is_metric_depth is None else is_metric_depth
        )
        self._pipe: Any = None
        # The adapter is shared by all requests: load once, run one inference at a time.
        self._lock = threading.RLock()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._pipe is not None:
                return
            if not _installed("torch") or not _installed("transformers"):
                raise RuntimeError(
                    "PyTorch / HuggingFace Transformers not found. "
                    "Install `torch` and `transformers` to run real monocular depth models."
                )
            from transformers import pipeline  # type: ignore[import-untyped]

            self._pipe = pipeline(
                task="depth-estimation",
                model=self.model_name,
                device=self.device,
            )
            logger.info("vision model loaded: %s", self.model_name)

    def estimate_relative_depth(
        self,
        image_rgb: np.ndarray | None,
        width: int,
        height: int,
    ) -> np.ndarray:
        if image_rgb is None:
            return np.ones((height, width), dtype=np.float32)

        from PIL import Image

        pil_image = Image.fromarray(image_rgb)
        with self._lock:
            self._ensure_loaded()
            assert self._pipe is not None
            result = self._pipe(pil_image)

        raw_pred = result.get("predicted_depth")
        if raw_pred is not None:
            if hasattr(raw_pred, "detach"):
                raw_arr = raw_pred.detach().cpu().numpy().astype(np.float32)
            else:
                raw_arr = np.asarray(raw_pred, dtype=np.float32)
        else:
            raw_arr = np.asarray(result["depth"], dtype=np.float32)

        if self.is_metric_depth:
            clean = np.nan_to_num(raw_arr, nan=1.0, posinf=100.0, neginf=1e-3)
            depth_arr = np.clip(clean, 1e-3, 100.0).astype(np.float32)
        else:
            depth_arr = _disparity_to_relative_depth(raw_arr)

        import cv2

        if depth_arr.shape[:2] != (height, width):
            depth_arr = cv2.resize(depth_arr, (width, height), interpolation=cv2.INTER_LINEAR)

        return depth_arr.astype(np.float32)


class CompositeDepthEstimator:
    """Monocular neural depth estimator with safe fallback when offline or weights are unavailable."""

    def __init__(
        self,
        *,
        prefer_monocular: bool = True,
        primary: DepthEstimatorProtocol | None = None,
        fallback: DepthEstimatorProtocol | None = None,
        mode: VisionMode = "auto",
    ) -> None:
        self._mode = mode
        if primary is not None:
            self._primary: DepthEstimatorProtocol | None = primary
        elif mode == "on" or (
            mode == "auto"
            and prefer_monocular
            and _installed("torch")
            and _installed("transformers")
        ):
            self._primary = shared_depth_adapter()
        else:
            self._primary = None
        self._fallback = fallback or GeometricSyntheticDepthEstimator()
        self.last_source: DepthEstimationSource = "synthetic_geometric"

    def estimate_relative_depth(
        self,
        image_rgb: np.ndarray | None,
        width: int,
        height: int,
    ) -> np.ndarray:
        if self._primary is not None and image_rgb is not None:
            try:
                result = self._primary.estimate_relative_depth(image_rgb, width, height)
                self.last_source = "depth_anything_v2"
                return result
            except (RuntimeError, OSError, ValueError) as error:
                if self._mode == "on":
                    raise VisionModelUnavailable(f"Depth estimation failed: {error}") from error
        self.last_source = "synthetic_geometric"
        return self._fallback.estimate_relative_depth(image_rgb, width, height)


_shared_lock = threading.Lock()
_shared_yolo: YoloV8DetectorAdapter | None = None
_shared_depth: MonocularDepthAdapter | None = None


def shared_yolo_adapter() -> YoloV8DetectorAdapter:
    """The one YOLOv8 adapter of the process, so the weights are loaded once and not per request.

    `SUNSYNC_YOLO_WEIGHTS` names the weights file; without it the file `yolov8m.pt` is looked up
    in the working directory.
    """
    global _shared_yolo
    with _shared_lock:
        if _shared_yolo is None:
            weights = os.environ.get("SUNSYNC_YOLO_WEIGHTS", "").strip() or "yolov8m.pt"
            _shared_yolo = YoloV8DetectorAdapter(model_path=weights)
        return _shared_yolo


def shared_depth_adapter() -> MonocularDepthAdapter:
    """The one monocular depth adapter of the process (loaded once, one inference at a time).

    `SUNSYNC_DEPTH_MODEL` overrides the HuggingFace model id; without it the indoor metric
    checkpoint `depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf` is used.
    """
    global _shared_depth
    with _shared_lock:
        if _shared_depth is None:
            model_id = (
                os.environ.get("SUNSYNC_DEPTH_MODEL", "").strip() or DEFAULT_METRIC_DEPTH_MODEL
            )
            _shared_depth = MonocularDepthAdapter(model_name=model_id)
        return _shared_depth


def create_default_detector(
    prefer_yolo: bool = True, mode: VisionMode = "auto"
) -> ObjectDetectorProtocol:
    """Return composite YOLOv8 + contour-fallback detector."""
    return CompositeFurnitureDetector(prefer_yolo=prefer_yolo, mode=mode)


def create_default_depth_estimator(
    prefer_monocular: bool = True, mode: VisionMode = "auto"
) -> DepthEstimatorProtocol:
    """Return composite monocular + geometric-fallback depth estimator."""
    return CompositeDepthEstimator(prefer_monocular=prefer_monocular, mode=mode)
