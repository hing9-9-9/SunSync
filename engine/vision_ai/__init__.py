"""FR-03 & FR-06 Vision AI Automatic Furniture Classification & 3D Bounding Box Estimation."""

from engine.vision_ai.pipeline import (
    CLASS_GEOMETRIC_PRIORS,
    CameraPoseResult,
    DepthCalibrationResult,
    VisionRegistrationPipeline,
)
from engine.vision_ai.protocols import (
    CompositeDepthEstimator,
    CompositeFurnitureDetector,
    DepthEstimatorProtocol,
    DetectedObject2D,
    DetectionSource,
    FurnitureLabel,
    GeometricSyntheticDepthEstimator,
    GeometricSyntheticDetector,
    MonocularDepthAdapter,
    ObjectDetectorProtocol,
    YoloV8DetectorAdapter,
    _merge_book_clusters,
)

__all__ = [
    "CLASS_GEOMETRIC_PRIORS",
    "CameraPoseResult",
    "CompositeDepthEstimator",
    "CompositeFurnitureDetector",
    "DepthCalibrationResult",
    "DepthEstimatorProtocol",
    "DetectedObject2D",
    "DetectionSource",
    "FurnitureLabel",
    "GeometricSyntheticDepthEstimator",
    "GeometricSyntheticDetector",
    "MonocularDepthAdapter",
    "ObjectDetectorProtocol",
    "VisionRegistrationPipeline",
    "YoloV8DetectorAdapter",
    "_merge_book_clusters",
]
