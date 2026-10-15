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
from .solar import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_INTERVAL_SECONDS,
    calculate_solar_positions,
    make_prediction_grid,
    sun_direction_enu,
)

__all__ = [
    "DEFAULT_DURATION_MINUTES",
    "DEFAULT_INTERVAL_SECONDS",
    "Cuboid",
    "PredictionOptions",
    "ProtectedRegion",
    "Room",
    "Site",
    "SolarPosition",
    "Window",
    "calculate_solar_positions",
    "make_prediction_grid",
    "sun_direction_enu",
]
