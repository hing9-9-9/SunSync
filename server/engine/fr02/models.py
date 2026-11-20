"""Immutable data types for FR-02."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.fr01.models import Vector3

NEED_STATES = ("opaque", "infeasible", "unknown")
GRADES = ("A", "B", "C", "D")


@dataclass(frozen=True, slots=True)
class FilmWindow:
    """Film grid of one window in the window's frame; metres.

    `film_min_transmittance` is the share of direct sunlight the darkest film still lets through.
    """

    window_id: str
    origin_m: Vector3
    u_axis: Vector3
    v_axis: Vector3
    width_m: float
    height_m: float
    film_rows: int
    film_cols: int
    film_min_transmittance: float
    u_edges_m: tuple[float, ...] = ()
    v_edges_m: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("window_id must be a non-empty string")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError("window width and height must be positive")
        if self.film_rows < 1 or self.film_cols < 1:
            raise ValueError("the film grid needs at least one row and one column")
        if not 0 <= self.film_min_transmittance <= 1:
            raise ValueError("film_min_transmittance must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ExposedTile:
    """A tile that sunlight reaches through a window (FR-01 `clear_path`)."""

    timestamp: datetime
    region_id: str
    sample_index: int
    window_id: str
    window_hit_m: Vector3
    direct_irradiance_W_m2: float | None = None


@dataclass(frozen=True, slots=True)
class CellCause:
    region_id: str
    sample_index: int
    direct_irradiance_W_m2: float | None = None


@dataclass(frozen=True, slots=True)
class CellCandidate:
    """A film cell to consider for shading at one timestamp."""

    timestamp: datetime
    window_id: str
    row: int
    col: int
    causes: tuple[CellCause, ...]


@dataclass(frozen=True, slots=True)
class Need:
    """One film cell must be in `state` from `start_time` to `end_time` because of one region."""

    window_id: str
    row: int
    col: int
    start_time: datetime
    end_time: datetime
    state: str
    region_id: str
    grade: str

    def __post_init__(self) -> None:
        if self.state not in NEED_STATES:
            raise ValueError(f"state must be one of {NEED_STATES}")
        if self.grade not in GRADES:
            raise ValueError(f"grade must be one of {GRADES}")
        if self.start_time >= self.end_time:
            raise ValueError("a need must end after it starts")
