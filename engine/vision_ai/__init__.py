"""FR-03 & FR-06 Vision AI Automatic Furniture Classification & 3D Bounding Box Estimation."""

from engine.vision_ai.pipeline import (
    CLASS_GEOMETRIC_PRIORS,
    CameraPoseResult,
    DepthCalibrationResult,
    VisionRegistrationPipeline,
)
from engine.vision_ai.protocols import (
    DepthEstimatorProtocol,
    DetectedObject2D,
    FurnitureLabel,
    GeometricSyntheticDepthEstimator,
    GeometricSyntheticDetector,
    MonocularDepthAdapter,
    ObjectDetectorProtocol,
    YoloV8DetectorAdapter,
)

__all__ = [
    "CLASS_GEOMETRIC_PRIORS",
    "CameraPoseResult",
    "DepthCalibrationResult",
    "DepthEstimatorProtocol",
    "DetectedObject2D",
    "FurnitureLabel",
    "GeometricSyntheticDepthEstimator",
    "GeometricSyntheticDetector",
    "MonocularDepthAdapter",
    "ObjectDetectorProtocol",
    "VisionRegistrationPipeline",
    "YoloV8DetectorAdapter",
]
