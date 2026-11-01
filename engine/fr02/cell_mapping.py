"""FR-02.1: map the window crossings of exposed protected-region tiles to film cells.

Window-local coordinates (u, v) are in metres from the window corner `origin_m`, with u along
`u_axis` and v along `v_axis`. Row 0 is the row with the largest v (the top of a vertical window)
and column 0 starts at u = 0 (the left when looking from inside), as in the Scene's Window.

Assumptions: the window geometry comes from a validated Scene window (unit, perpendicular axes),
and each `window_hit_m` lies on its window plane, as FR-01 guarantees; the distance to the plane
is not checked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import ceil, floor

from engine.fr01.geometry import dot, subtract
from engine.fr01.models import Vector3
from engine.fr01.tiling import TILE_SIZE_M

HALF_TILE_M = TILE_SIZE_M / 2
EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class FilmWindow:
    """The film grid of one window, in the window's own frame."""

    window_id: str
    origin_m: Vector3
    u_axis: Vector3
    v_axis: Vector3
    width_m: float
    height_m: float
    film_rows: int
    film_cols: int

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("window_id must be a non-empty string")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError("window width and height must be positive")
        if self.film_rows < 1 or self.film_cols < 1:
            raise ValueError("the film grid needs at least one row and one column")


@dataclass(frozen=True, slots=True)
class ExposedTile:
    """A tile that sunlight reaches through a window (FR-01 `clear_path`)."""

    timestamp: datetime
    region_id: str
    sample_index: int
    window_id: str
    window_hit_m: Vector3


@dataclass(frozen=True, slots=True)
class CellCause:
    region_id: str
    sample_index: int


@dataclass(frozen=True, slots=True)
class CellCandidate:
    """A film cell that must be considered for shading at one timestamp."""

    timestamp: datetime
    window_id: str
    row: int
    col: int
    causes: tuple[CellCause, ...]


def _window_position(window: FilmWindow, point_m: Vector3) -> tuple[float, float]:
    """Return the (u, v) position of an ENU point on the window plane."""

    offset = subtract(point_m, window.origin_m)
    return dot(offset, window.u_axis), dot(offset, window.v_axis)


def _cell_range(low: float, high: float, size: float, count: int) -> range:
    """Indices of the cells that overlap the interval (low, high) with a positive length."""

    first = floor((low + EPSILON) / size)
    last = ceil((high - EPSILON) / size) - 1
    return range(max(first, 0), min(last, count - 1) + 1)


def cells_for_hit(
    window: FilmWindow, hit_m: Vector3, half_size_m: float = HALF_TILE_M
) -> tuple[tuple[int, int], ...]:
    """Return the (row, col) of every film cell that the widened crossing point overlaps.

    The point grows into a square of side 2 * half_size_m, which is cut at the window edges.
    A square that no longer overlaps the window with any area gives no cells, so a point less
    than half_size_m outside the window edge still gives the cells along that edge.
    """

    u, v = _window_position(window, hit_m)
    u_low, u_high = max(u - half_size_m, 0.0), min(u + half_size_m, window.width_m)
    v_low, v_high = max(v - half_size_m, 0.0), min(v + half_size_m, window.height_m)
    if u_high - u_low <= EPSILON or v_high - v_low <= EPSILON:
        return ()
    cols = _cell_range(u_low, u_high, window.width_m / window.film_cols, window.film_cols)
    # Rows count from the top edge, so the distance from the top is height - v.
    rows = _cell_range(
        window.height_m - v_high,
        window.height_m - v_low,
        window.height_m / window.film_rows,
        window.film_rows,
    )
    return tuple((row, col) for row in rows for col in cols)


def map_tiles_to_cells(
    tiles: Iterable[ExposedTile], windows: Iterable[FilmWindow]
) -> list[CellCandidate]:
    """Merge the cells of all tiles by (timestamp, window, row, col), keeping every cause."""

    by_id = {window.window_id: window for window in windows}
    causes: dict[tuple[datetime, str, int, int], list[CellCause]] = defaultdict(list)
    for tile in tiles:
        window = by_id.get(tile.window_id)
        if window is None:
            raise ValueError(f"tile refers to an unknown window: {tile.window_id}")
        for row, col in cells_for_hit(window, tile.window_hit_m):
            key = (tile.timestamp, tile.window_id, row, col)
            causes[key].append(CellCause(tile.region_id, tile.sample_index))
    return [
        CellCandidate(timestamp, window_id, row, col, tuple(found))
        for (timestamp, window_id, row, col), found in sorted(causes.items())
    ]
