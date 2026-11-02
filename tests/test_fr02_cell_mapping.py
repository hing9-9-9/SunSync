"""FR-02.1 tests: window crossings of exposed tiles become film cells."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.schemas.scene import Scene

from engine.fr02 import (
    CellCause,
    ExposedTile,
    FilmWindow,
    cells_for_hit,
    map_tiles_to_cells,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"
NOON = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 31, 12, 5, tzinfo=UTC)


def unit_window() -> FilmWindow:
    """A 1 m x 1 m window in the x-z plane with a 10 x 10 film grid of 10 cm cells."""

    return FilmWindow("window_001", (0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 1.0, 10, 10, 0.0)


def hit(u: float, v: float) -> tuple[float, float, float]:
    return (u, 0.0, v)


def tile(sample_index: int, u: float, v: float, timestamp=NOON, region="region_A") -> ExposedTile:
    return ExposedTile(timestamp, region, sample_index, "window_001", hit(u, v))


def test_point_inside_one_cell_gives_that_cell():
    assert cells_for_hit(unit_window(), hit(0.45, 0.55)) == ((4, 4),)


def test_point_near_a_cell_edge_gives_both_cells():
    assert cells_for_hit(unit_window(), hit(0.42, 0.63)) == ((3, 3), (3, 4))


def test_point_near_a_cell_corner_gives_four_cells():
    assert cells_for_hit(unit_window(), hit(0.4, 0.6)) == ((3, 3), (3, 4), (4, 3), (4, 4))


@pytest.mark.parametrize(
    ("u", "v", "expected"),
    [
        (-0.2, 0.5, ()),
        (1.2, 0.5, ()),
        (0.5, 1.2, ()),
        (0.5, -0.2, ()),
        (-0.01, 0.45, ((5, 0),)),
        (-0.03, 0.45, ()),
        (0.0, 0.45, ((5, 0),)),
        (0.0, 0.0, ((9, 0),)),
        (1.0, 1.0, ((0, 9),)),
    ],
    ids=[
        "left",
        "right",
        "above",
        "below",
        "just_outside_left",
        "outside_beyond_the_margin",
        "left_edge",
        "bottom_left",
        "top_right",
    ],
)
def test_points_outside_or_on_the_window_edge(u, v, expected):
    assert cells_for_hit(unit_window(), hit(u, v)) == expected


def test_tiles_on_the_same_cell_and_time_merge_and_keep_their_causes():
    candidates = map_tiles_to_cells(
        [
            tile(1, 0.45, 0.55),
            tile(2, 0.46, 0.54, region="region_B"),
            tile(3, 0.45, 0.55, timestamp=LATER),
        ],
        [unit_window()],
    )
    assert [(c.timestamp, c.row, c.col) for c in candidates] == [(NOON, 4, 4), (LATER, 4, 4)]
    assert candidates[0].causes == (CellCause("region_A", 1), CellCause("region_B", 2))
    assert candidates[1].causes == (CellCause("region_A", 3),)


def test_only_the_lit_part_of_a_shelf_makes_cells():
    """A 60 cm shelf (12 x 12 tiles) at u 0.2-0.8 and v 0.2-0.8; only its right third is lit."""

    lit = [
        tile(index, 0.2 + 0.025 + 0.05 * column, 0.2 + 0.025 + 0.05 * row)
        for index, (column, row) in enumerate((c, r) for c in range(8, 12) for r in range(12))
    ]
    candidates = map_tiles_to_cells(lit, [unit_window()])
    assert {(c.row, c.col) for c in candidates} == {
        (row, col) for row in range(2, 8) for col in (6, 7)
    }


def test_unknown_window_is_rejected():
    stray = ExposedTile(NOON, "region_A", 0, "other_window", hit(0.5, 0.5))
    with pytest.raises(ValueError, match="unknown window"):
        map_tiles_to_cells([stray], [unit_window()])


def test_cell_numbers_agree_with_the_scene_window():
    """Row 0 is the top row and column 0 the left column, exactly as the Scene's Window says."""

    scene = Scene.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    window = scene.windows[0]
    film = FilmWindow(
        window.window_id,
        tuple(window.vertices_m[0]),
        tuple(window.u_axis),
        tuple(window.v_axis),
        window.width_m,
        window.height_m,
        window.film_rows,
        window.film_cols,
        0.0,
    )
    for row in range(window.film_rows):
        for col in range(window.film_cols):
            u, v = window.cell_to_uv(row, col)
            point = window.uv_to_enu(u, v)
            assert cells_for_hit(film, point, half_size_m=1e-4) == ((row, col),)
