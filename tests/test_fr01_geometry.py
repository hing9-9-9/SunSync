"""Analytic intersection tests for Stage 2 FR-01 geometry."""

from math import cos, radians, sin, sqrt

import pytest

from engine.fr01.geometry import (
    WindowIntersectionKind,
    first_room_boundary,
    intersect_cuboid,
    intersect_window,
    point_strictly_inside_cuboid,
)
from engine.fr01.models import Cuboid, Room, Window


def room() -> Room:
    return Room("room", ((0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)), 3)


def south_window() -> Window:
    return Window(
        "south_window",
        "room",
        ((5, 0, 1), (1, 0, 1), (1, 0, 2.8), (5, 0, 2.8)),
        (0, 1, 0),
        (-1, 0, 0),
        (0, 0, 1),
    )


def test_south_window_hit_uses_window_local_frame():
    direction = (0.0, -1 / sqrt(2), 1 / sqrt(2))
    result = intersect_window((3, 2, 0.5), direction, south_window())
    assert result.kind is WindowIntersectionKind.HIT
    assert result.hit is not None
    assert result.hit.hit_m == pytest.approx((3, 0, 2.5))
    assert result.hit.distance_m == pytest.approx(2 * sqrt(2))


def test_rotated_window_frame_and_parallel_or_boundary_rays():
    window = Window(
        "west_window",
        "room",
        ((0, 1, 1), (0, 1, 2), (0, 3, 2), (0, 3, 1)),
        (1, 0, 0),
        (0, 0, 1),
        (0, 1, 0),
    )
    hit = intersect_window((2, 2, 1.5), (-1, 0, 0), window)
    assert hit.kind is WindowIntersectionKind.HIT
    assert hit.hit is not None and hit.hit.hit_m == pytest.approx((0, 2, 1.5))
    assert (
        intersect_window((2, 2, 1.5), (0, -1, 0), window).kind
        is WindowIntersectionKind.MISS
    )
    edge = intersect_window((2, 1, 1), (-1, 0, 0), window)
    assert edge.kind is WindowIntersectionKind.AMBIGUOUS


def test_room_boundary_only_allows_window_aperture_and_reports_floor_ceiling():
    direction = (0.0, -1 / sqrt(2), 1 / sqrt(2))
    boundary = first_room_boundary((3, 2, 0.5), direction, room())
    assert boundary is not None
    assert boundary.kind == "wall"
    assert boundary.object_id == "wall:0"
    assert boundary.hit_m == pytest.approx((3, 0, 2.5))

    ceiling = first_room_boundary((3, 2, 2), (0, 0, 1), room())
    assert ceiling is not None and ceiling.kind == "ceiling"
    floor = first_room_boundary((3, 2, 1), (0, 0, -1), room())
    assert floor is not None and floor.kind == "floor"


def test_concave_room_first_boundary_blocks_a_later_window_line():
    concave = Room(
        "room",
        (
            (0, 0, 0),
            (6, 0, 0),
            (6, 6, 0),
            (4, 6, 0),
            (4, 2, 0),
            (2, 2, 0),
            (2, 6, 0),
            (0, 6, 0),
        ),
        3,
    )
    hit = first_room_boundary((3, 5, 1), (0, -1, 0), concave)
    assert hit is not None
    assert hit.object_id == "wall:4"
    assert hit.hit_m == pytest.approx((3, 2, 1))


@pytest.mark.parametrize("yaw", [0, 37, 90])
def test_cuboid_slab_intersection_for_multiple_yaws(yaw):
    cuboid = Cuboid("box", "room", (3, 2, 0), (2, 1, 2), yaw)
    angle = radians(yaw)
    outward = (cos(angle), sin(angle), 0)
    origin = (3 + outward[0] * 3, 2 + outward[1] * 3, 1)
    direction = (-outward[0], -outward[1], 0)
    hit = intersect_cuboid(origin, direction, cuboid)
    assert hit is not None
    assert hit.object_id == "box"
    assert hit.distance_m == pytest.approx(2)


def test_cuboid_parallel_tangent_nearest_and_surface_self_intersection():
    box = Cuboid("box", "room", (3, 2, 0), (2, 1, 2), 0)
    assert intersect_cuboid((0, 0, 1), (1, 0, 0), box) is None
    tangent = intersect_cuboid((0, 1.5, 1), (1, 0, 0), box)
    assert tangent is not None and tangent.distance_m == pytest.approx(2)
    assert intersect_cuboid((3, 1.5, 1), (0, -1, 0), box) is None
    inward = intersect_cuboid((3, 2.5, 1), (0, -1, 0), box)
    assert inward is not None and inward.distance_m == pytest.approx(0)
    assert point_strictly_inside_cuboid((3, 2, 1), box)
    assert not point_strictly_inside_cuboid((3, 1.5, 1), box)
