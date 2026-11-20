"""Immutable computation DTOs for FR-01.

All coordinates and dimensions are metres in a right-handed ENU frame:
x east, y north, and z up.  These types are independent from the FR-06
Pydantic schema; a future backend adapter owns conversion between contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite, sqrt
from typing import TypeAlias, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Vector3: TypeAlias = tuple[float, float, float]


def _finite_number(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _vector(value: Vector3, label: str) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{label} must contain exactly three values")
    return cast(Vector3, tuple(_finite_number(component, label) for component in value))


def _unit_vector(value: Vector3, label: str) -> Vector3:
    result = _vector(value, label)
    length = sqrt(sum(component * component for component in result))
    if abs(length - 1.0) > 1e-6:
        raise ValueError(f"{label} must be a unit vector")
    return result


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class Site:
    """Representative geographic location used for solar-position calculation."""

    latitude_deg: float
    longitude_deg: float
    timezone: str

    def __post_init__(self) -> None:
        latitude = _finite_number(self.latitude_deg, "latitude_deg")
        longitude = _finite_number(self.longitude_deg, "longitude_deg")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("latitude_deg must be between -90 and 90")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("longitude_deg must be between -180 and 180")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise ValueError("timezone must be a non-empty IANA timezone name")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone name") from error
        object.__setattr__(self, "latitude_deg", latitude)
        object.__setattr__(self, "longitude_deg", longitude)


@dataclass(frozen=True, slots=True)
class Room:
    """Room floor polygon and constant ceiling height in metres."""

    room_id: str
    floor_vertices_m: tuple[Vector3, ...]
    height_m: float

    def __post_init__(self) -> None:
        if len(self.floor_vertices_m) < 3:
            raise ValueError("floor_vertices_m must contain at least three vertices")
        height = _finite_number(self.height_m, "height_m")
        if height <= 0:
            raise ValueError("height_m must be positive")
        object.__setattr__(self, "room_id", _identifier(self.room_id, "room_id"))
        object.__setattr__(
            self,
            "floor_vertices_m",
            tuple(_vector(v, "floor vertex") for v in self.floor_vertices_m),
        )
        object.__setattr__(self, "height_m", height)


@dataclass(frozen=True, slots=True)
class Window:
    """FR-06-compatible rectangular window geometry in metres."""

    window_id: str
    room_id: str
    vertices_m: tuple[Vector3, Vector3, Vector3, Vector3]
    inward_normal: Vector3
    u_axis: Vector3
    v_axis: Vector3

    def __post_init__(self) -> None:
        if len(self.vertices_m) != 4:
            raise ValueError("vertices_m must contain exactly four vertices")
        object.__setattr__(self, "window_id", _identifier(self.window_id, "window_id"))
        object.__setattr__(self, "room_id", _identifier(self.room_id, "room_id"))
        object.__setattr__(
            self,
            "vertices_m",
            tuple(_vector(v, "window vertex") for v in self.vertices_m),
        )
        object.__setattr__(self, "inward_normal", _unit_vector(self.inward_normal, "inward_normal"))
        object.__setattr__(self, "u_axis", _unit_vector(self.u_axis, "u_axis"))
        object.__setattr__(self, "v_axis", _unit_vector(self.v_axis, "v_axis"))


@dataclass(frozen=True, slots=True)
class Cuboid:
    """Opaque cuboid using FR-06 base-centre and yaw conventions."""

    occluder_id: str
    room_id: str
    base_center_m: Vector3
    dimensions_m: Vector3
    yaw_deg_ccw_from_east: float

    def __post_init__(self) -> None:
        dimensions = _vector(self.dimensions_m, "dimensions_m")
        if any(dimension <= 0 for dimension in dimensions):
            raise ValueError("dimensions_m values must be positive")
        object.__setattr__(self, "occluder_id", _identifier(self.occluder_id, "occluder_id"))
        object.__setattr__(self, "room_id", _identifier(self.room_id, "room_id"))
        object.__setattr__(self, "base_center_m", _vector(self.base_center_m, "base_center_m"))
        object.__setattr__(self, "dimensions_m", dimensions)
        object.__setattr__(
            self,
            "yaw_deg_ccw_from_east",
            _finite_number(self.yaw_deg_ccw_from_east, "yaw_deg_ccw_from_east"),
        )


@dataclass(frozen=True, slots=True)
class ProtectedRegion:
    """Rectangular protected surface in metres."""

    region_id: str
    room_id: str
    vertices_m: tuple[Vector3, ...]
    receiving_normal: Vector3
    host_occluder_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.vertices_m) != 4:
            raise ValueError("vertices_m must contain exactly four vertices")
        if self.host_occluder_id is not None:
            _identifier(self.host_occluder_id, "host_occluder_id")
        object.__setattr__(self, "region_id", _identifier(self.region_id, "region_id"))
        object.__setattr__(self, "room_id", _identifier(self.room_id, "room_id"))
        object.__setattr__(
            self,
            "vertices_m",
            tuple(_vector(v, "protected-region vertex") for v in self.vertices_m),
        )
        object.__setattr__(
            self,
            "receiving_normal",
            _unit_vector(self.receiving_normal, "receiving_normal"),
        )
        points = self.vertices_m
        u = tuple(points[1][i] - points[0][i] for i in range(3))
        v = tuple(points[3][i] - points[0][i] for i in range(3))
        if sum(x * x for x in u) <= 1e-12 or sum(x * x for x in v) <= 1e-12 or abs(sum(a * b for a, b in zip(u, v, strict=True))) > 1e-9:
            raise ValueError("protected region must have nonzero perpendicular edges")
        if any(abs(points[2][i] - points[1][i] - v[i]) > 1e-9 for i in range(3)):
            raise ValueError("protected region must be rectangular")


@dataclass(frozen=True, slots=True)
class PredictionOptions:
    """Prediction time-range options. Duration is minutes; interval is seconds."""

    duration_minutes: int = 120
    interval_seconds: int = 300

    def __post_init__(self) -> None:
        for field_name, value, minimum, maximum in (
            ("duration_minutes", self.duration_minutes, 1, 1440),
            ("interval_seconds", self.interval_seconds, 1, 3600),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"{field_name} must be an integer between {minimum} and {maximum}")


@dataclass(frozen=True, slots=True)
class SolarPosition:
    """Geometric solar position and ENU direction at one UTC timestamp."""

    timestamp: datetime
    elevation_deg: float
    azimuth_deg_true: float
    sun_direction_enu: Vector3

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        elevation = _finite_number(self.elevation_deg, "elevation_deg")
        azimuth = _finite_number(self.azimuth_deg_true, "azimuth_deg_true")
        if not -90.0 <= elevation <= 90.0:
            raise ValueError("elevation_deg must be between -90 and 90")
        if not 0.0 <= azimuth < 360.0:
            raise ValueError("azimuth_deg_true must be between 0 and 360")
        object.__setattr__(self, "elevation_deg", elevation)
        object.__setattr__(self, "azimuth_deg_true", azimuth)
        object.__setattr__(
            self,
            "sun_direction_enu",
            _unit_vector(self.sun_direction_enu, "sun_direction_enu"),
        )


class ExposureReason(StrEnum):
    CLEAR_PATH = "clear_path"
    SUN_BELOW_HORIZON = "sun_below_horizon"
    BACK_FACING = "back_facing"
    NO_WINDOW = "no_window"
    WINDOW_MISSED = "window_missed"
    BOUNDARY_AMBIGUOUS = "boundary_ambiguous"
    ROOM_BLOCKED = "room_blocked"
    OCCLUDER_BLOCKED = "occluder_blocked"


@dataclass(frozen=True, slots=True)
class CandidatePath:
    window_id: str
    window_hit_m: Vector3
    sample_to_window_distance_m: float
    reverse_direction_enu: Vector3
    incoming_direction_enu: Vector3


@dataclass(frozen=True, slots=True)
class Blocker:
    kind: str
    object_id: str
    hit_m: Vector3
    distance_m: float


@dataclass(frozen=True, slots=True)
class SampleResult:
    sample_index: int
    point_m: Vector3
    exposed: bool
    reason: ExposureReason
    candidate_path: CandidatePath | None = None
    blocker: Blocker | None = None


@dataclass(frozen=True, slots=True)
class RegionTimeResult:
    timestamp: datetime
    exposed: bool
    samples: tuple[SampleResult, ...]


@dataclass(frozen=True, slots=True)
class ExposureInterval:
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True, slots=True)
class RegionPrediction:
    region_id: str
    first_predicted_exposure_time: datetime | None
    exposed_at_start: bool
    timeline: tuple[RegionTimeResult, ...]
    exposure_intervals: tuple[ExposureInterval, ...]
