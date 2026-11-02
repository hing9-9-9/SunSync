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
from .grade_a import grade_a_needs
from .grade_bc import grade_bc_needs
from .plan import Need, build_plan

__all__ = [
    "CellCandidate",
    "CellCause",
    "ExposedTile",
    "FilmWindow",
    "Need",
    "build_plan",
    "cells_for_hit",
    "grade_a_needs",
    "grade_bc_needs",
    "map_tiles_to_cells",
]
