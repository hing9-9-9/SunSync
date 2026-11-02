"""FR-02.1: map the window crossings of exposed tiles to film cells.

Window coordinates (u, v) are metres from `origin_m` along `u_axis` and `v_axis`. Row 0 has the
largest v and column 0 starts at u = 0, as in the Scene's Window. The window geometry comes from a
validated Scene, and each `window_hit_m` is assumed to lie on its window plane, as FR-01 gives it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from math import ceil, floor

from engine.fr01.geometry import dot, subtract
from engine.fr01.models import Vector3
from engine.fr01.tiling import TILE_SIZE_M

from .models import CellCandidate, CellCause, ExposedTile, FilmWindow

HALF_TILE_M = TILE_SIZE_M / 2
EPSILON = 1e-9


def _window_position(window: FilmWindow, point_m: Vector3) -> tuple[float, float]:
    """Return the (u, v) position of an ENU point on the window plane."""

    offset = subtract(point_m, window.origin_m)
    return dot(offset, window.u_axis), dot(offset, window.v_axis)


def _cell_range(low: float, high: float, size: float, count: int) -> range:
    """Indices of the cells that overlap (low, high) with a positive length."""

    first = floor((low + EPSILON) / size)
    last = ceil((high - EPSILON) / size) - 1
    return range(max(first, 0), min(last, count - 1) + 1)


def cells_for_hit(window: FilmWindow, hit_m: Vector3) -> tuple[tuple[int, int], ...]:
    """Return the (row, col) of every cell that the hit point, widened to one tile, overlaps."""

    u, v = _window_position(window, hit_m)
    u_low, u_high = max(u - HALF_TILE_M, 0.0), min(u + HALF_TILE_M, window.width_m)
    v_low, v_high = max(v - HALF_TILE_M, 0.0), min(v + HALF_TILE_M, window.height_m)
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
) -> tuple[CellCandidate, ...]:
    """Merge the cells of all tiles by (timestamp, window, row, col), keeping every cause."""

    by_id = {window.window_id: window for window in windows}
    causes: dict[tuple[datetime, str, int, int], list[CellCause]] = defaultdict(list)
    for tile in tiles:
        window = by_id.get(tile.window_id)
        if window is None:
            raise ValueError(f"tile refers to an unknown window: {tile.window_id}")
        for row, col in cells_for_hit(window, tile.window_hit_m):
            key = (tile.timestamp, tile.window_id, row, col)
            causes[key].append(
                CellCause(tile.region_id, tile.sample_index, tile.direct_irradiance_W_m2)
            )
    return tuple(
        CellCandidate(timestamp, window_id, row, col, tuple(found))
        for (timestamp, window_id, row, col), found in sorted(causes.items())
    )
