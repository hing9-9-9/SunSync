"""Deterministic 5 cm centre tiles for FR-01 protected regions."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt
from typing import cast

from .models import ProtectedRegion, Vector3

TILE_SIZE_M = 0.05


@dataclass(frozen=True, slots=True)
class TileCentre:
    sample_index: int
    row: int
    col: int
    point_m: Vector3


def tile_centres(region: ProtectedRegion, tile_size_m: float = TILE_SIZE_M) -> tuple[TileCentre, ...]:
    if tile_size_m <= 0:
        raise ValueError("tile_size_m must be positive")
    p0, p1, _, p3 = region.vertices_m
    u = tuple(p1[i] - p0[i] for i in range(3))
    v = tuple(p3[i] - p0[i] for i in range(3))
    ul, vl = sqrt(sum(x * x for x in u)), sqrt(sum(x * x for x in v))
    ua, va = tuple(x / ul for x in u), tuple(x / vl for x in v)
    cols, rows = ceil(ul / tile_size_m), ceil(vl / tile_size_m)
    result: list[TileCentre] = []
    for row in range(rows):
        v0, v1 = row * tile_size_m, min((row + 1) * tile_size_m, vl)
        for col in range(cols):
            u0, u1 = col * tile_size_m, min((col + 1) * tile_size_m, ul)
            uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
            point = cast(Vector3, tuple(p0[i] + uc * ua[i] + vc * va[i] for i in range(3)))
            result.append(TileCentre(len(result), row, col, point))
    return tuple(result)
