"""Pure-Python primitives for FR-01 sunlight-path prediction.

This package intentionally has no dependency on FastAPI, SQLModel, or the
backend package.  Geometry and prediction are added in later stages.
"""

from .models import (
    Cuboid,
    PredictionOptions,
    ProtectedRegion,
    Room,
    Site,
    SolarPosition,
    Window,
)
from .predictor import evaluate_sample, predict_future_exposure, predict_region_exposure
from .solar import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_SECONDS,
    calculate_solar_positions,
    make_prediction_grid,
    sun_direction_enu,
)
from .tiling import TILE_SIZE_M, TileCentre, tile_centres

__all__ = [
    "DEFAULT_DURATION_MINUTES",
    "DEFAULT_INTERVAL_SECONDS",
    "TILE_SIZE_M",
    "Cuboid",
    "PredictionOptions",
    "ProtectedRegion",
    "Room",
    "Site",
    "SolarPosition",
    "TileCentre",
    "Window",
    "calculate_solar_positions",
    "evaluate_sample",
    "make_prediction_grid",
    "predict_future_exposure",
    "predict_region_exposure",
    "sun_direction_enu",
    "tile_centres",
]
