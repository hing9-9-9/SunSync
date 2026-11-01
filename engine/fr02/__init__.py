"""Pure-Python selective-shading decisions for FR-02.

Like engine.fr01, this package has no dependency on FastAPI, SQLModel, or the backend package.
"""

from .cell_mapping import (
    CellCandidate,
    CellCause,
    ExposedTile,
    FilmWindow,
    cells_for_hit,
    map_tiles_to_cells,
)

__all__ = [
    "CellCandidate",
    "CellCause",
    "ExposedTile",
    "FilmWindow",
    "cells_for_hit",
    "map_tiles_to_cells",
]
