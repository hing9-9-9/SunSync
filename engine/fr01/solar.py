"""Timezone-safe pvlib solar-position calculations for FR-01."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from math import cos, isfinite, radians, sin, sqrt

import pandas as pd  # type: ignore[import-untyped]
from pvlib.solarposition import get_solarposition  # type: ignore[import-untyped]

from .models import PredictionOptions, Site, SolarPosition, Vector3

DEFAULT_DURATION_MINUTES = 120
DEFAULT_INTERVAL_SECONDS = 300
MAX_TIMESTAMPS = 1441
ALTITUDE_M = 0.0
PRESSURE_PA = 101325.0
SOLAR_POSITION_METHOD = "nrel_numpy"


def _require_aware(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must include a timezone offset")
    return value.astimezone(UTC)


def sun_direction_enu(elevation_deg: float, azimuth_deg_true: float) -> Vector3:
    """Return the unit ENU vector pointing from a point toward the sun.

    Elevation is measured up from the horizon. Azimuth is true-north zero and
    increases clockwise, matching pvlib and the FR-06 window azimuth contract.
    """

    if not isinstance(elevation_deg, (int, float)) or not isfinite(elevation_deg):
        raise ValueError("elevation_deg must be finite")
    if not isinstance(azimuth_deg_true, (int, float)) or not isfinite(azimuth_deg_true):
        raise ValueError("azimuth_deg_true must be finite")
    elevation = radians(float(elevation_deg))
    azimuth = radians(float(azimuth_deg_true))
    result = (
        cos(elevation) * sin(azimuth),
        cos(elevation) * cos(azimuth),
        sin(elevation),
    )
    length = sqrt(sum(component * component for component in result))
    if not isfinite(length) or abs(length - 1.0) > 1e-12:
        raise ValueError("could not construct a finite unit solar vector")
    return result


def make_prediction_grid(
    start_time: datetime, options: PredictionOptions | None = None
) -> tuple[datetime, ...]:
    """Create a deterministic UTC grid including start and end exactly once."""

    start = _require_aware(start_time, "start_time")
    options = (
        options
        if options is not None
        else PredictionOptions(
            duration_minutes=DEFAULT_DURATION_MINUTES,
            interval_seconds=DEFAULT_INTERVAL_SECONDS,
        )
    )
    duration_seconds = options.duration_minutes * 60
    grid_size = duration_seconds // options.interval_seconds + 1
    if duration_seconds % options.interval_seconds:
        grid_size += 1
    if grid_size > MAX_TIMESTAMPS:
        raise ValueError(
            f"prediction grid exceeds the {MAX_TIMESTAMPS}-timestamp limit"
        )
    end = start + timedelta(minutes=options.duration_minutes)
    interval = timedelta(seconds=options.interval_seconds)
    timestamps = [start]
    current = start
    while current + interval <= end:
        current += interval
        timestamps.append(current)
    if timestamps[-1] != end:
        timestamps.append(end)
    return tuple(timestamps)


def calculate_solar_positions(
    site: Site, timestamps: Iterable[datetime]
) -> tuple[SolarPosition, ...]:
    """Calculate geometric, not apparent, solar position for UTC-normalized timestamps."""

    utc_timestamps = tuple(
        _require_aware(timestamp, "timestamp") for timestamp in timestamps
    )
    if not utc_timestamps:
        return ()
    if len(utc_timestamps) > MAX_TIMESTAMPS:
        raise ValueError(
            f"solar calculation exceeds the {MAX_TIMESTAMPS}-timestamp limit"
        )

    frame = get_solarposition(
        pd.DatetimeIndex(utc_timestamps),
        site.latitude_deg,
        site.longitude_deg,
        altitude=ALTITUDE_M,
        pressure=PRESSURE_PA,
        method=SOLAR_POSITION_METHOD,
    )
    positions: list[SolarPosition] = []
    for timestamp, (_, row) in zip(utc_timestamps, frame.iterrows(), strict=True):
        elevation = float(row["elevation"])
        azimuth = float(row["azimuth"])
        if not isfinite(elevation) or not isfinite(azimuth):
            raise ValueError("pvlib returned a non-finite solar position")
        positions.append(
            SolarPosition(
                timestamp=timestamp,
                elevation_deg=elevation,
                azimuth_deg_true=azimuth % 360.0,
                sun_direction_enu=sun_direction_enu(elevation, azimuth),
            )
        )
    return tuple(positions)
