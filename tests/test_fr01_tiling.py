import pytest

from engine.fr01.models import ProtectedRegion
from engine.fr01.tiling import tile_centres


def region(width: float, height: float) -> ProtectedRegion:
    return ProtectedRegion(
        "region", "room", ((0, 0, 0), (width, 0, 0), (width, 0, height), (0, 0, height)), (0, -1, 0)
    )


def test_tiles_are_row_major_and_keep_residual_width():
    assert len(tile_centres(region(0.60, 0.60))) == 12 * 12
    tiles = tile_centres(region(0.62, 0.60))
    assert len(tiles) == 13 * 12
    assert tiles[0].point_m == (0.025, 0.0, 0.025)
    assert tiles[12].point_m == pytest.approx((0.61, 0.0, 0.025))
    assert tiles[-1].sample_index == 155
    assert tiles[-1].row == 11 and tiles[-1].col == 12
