"""Analytic ENU ray intersections used by the FR-01 predictor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import cos, radians, sin

from .models import Cuboid, Room, Vector3, Window

DISTANCE_EPSILON_M = 1e-6
ANGULAR_EPSILON = 1e-9


def add(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def subtract(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(vector: Vector3, scalar: float) -> Vector3:
    return (vector[0] * scalar, vector[1] * scalar, vector[2] * scalar)


def dot(a: Vector3, b: Vector3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vector3, b: Vector3) -> Vector3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


@dataclass(frozen=True, slots=True)
class RayHit:
    """Positive-distance intersection along a unit-direction ray."""

    distance_m: float
    hit_m: Vector3
    object_id: str
    kind: str


class WindowIntersectionKind(StrEnum):
    HIT = "hit"
    MISS = "miss"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class WindowIntersection:
    kind: WindowIntersectionKind
    hit: RayHit | None = None


def _window_dimensions(window: Window) -> tuple[float, float]:
    origin, u_end, _, v_end = window.vertices_m
    return dot(subtract(u_end, origin), window.u_axis), dot(subtract(v_end, origin), window.v_axis)


def intersect_window(origin: Vector3, direction: Vector3, window: Window) -> WindowIntersection:
    """Intersect a ray with a front-facing window's strict inner rectangle."""

    if dot(window.inward_normal, direction) >= -ANGULAR_EPSILON:
        return WindowIntersection(WindowIntersectionKind.MISS)
    plane_origin = window.vertices_m[0]
    denominator = dot(direction, window.inward_normal)
    distance = dot(subtract(plane_origin, origin), window.inward_normal) / denominator
    if distance <= DISTANCE_EPSILON_M:
        return WindowIntersection(WindowIntersectionKind.MISS)
    hit = add(origin, scale(direction, distance))
    width, height = _window_dimensions(window)
    u = dot(subtract(hit, plane_origin), window.u_axis)
    v = dot(subtract(hit, plane_origin), window.v_axis)
    if (
        DISTANCE_EPSILON_M < u < width - DISTANCE_EPSILON_M
        and DISTANCE_EPSILON_M < v < height - DISTANCE_EPSILON_M
    ):
        return WindowIntersection(
            WindowIntersectionKind.HIT,
            RayHit(distance, hit, window.window_id, "window"),
        )
    if (
        -DISTANCE_EPSILON_M <= u <= width + DISTANCE_EPSILON_M
        and -DISTANCE_EPSILON_M <= v <= height + DISTANCE_EPSILON_M
    ):
        return WindowIntersection(WindowIntersectionKind.AMBIGUOUS)
    return WindowIntersection(WindowIntersectionKind.MISS)


def _point_in_floor(room: Room, point: Vector3) -> bool:
    """Return whether an XY point is inside or on a simple floor polygon."""

    x, y = point[0], point[1]
    vertices = room.floor_vertices_m
    inside = False
    for start, end in zip(vertices, (*vertices[1:], vertices[0]), strict=True):
        ax, ay = start[0], start[1]
        bx, by = end[0], end[1]
        edge_x, edge_y = bx - ax, by - ay
        relative_x, relative_y = x - ax, y - ay
        cross_z = edge_x * relative_y - edge_y * relative_x
        projection = relative_x * edge_x + relative_y * edge_y
        length_squared = edge_x * edge_x + edge_y * edge_y
        if (
            abs(cross_z) <= DISTANCE_EPSILON_M
            and -DISTANCE_EPSILON_M <= projection <= length_squared + DISTANCE_EPSILON_M
        ):
            return True
        if (ay > y) != (by > y):
            x_at_y = edge_x * (y - ay) / (by - ay) + ax
            if x < x_at_y:
                inside = not inside
    return inside


def first_room_boundary(origin: Vector3, direction: Vector3, room: Room) -> RayHit | None:
    """Return the nearest room wall, floor, or ceiling hit; corners are ambiguous."""

    hits: list[RayHit] = []
    vertices = room.floor_vertices_m
    for index, (start, end) in enumerate(zip(vertices, (*vertices[1:], vertices[0]), strict=True)):
        edge = subtract(end, start)
        normal = (edge[1], -edge[0], 0.0)
        denominator = dot(direction, normal)
        if abs(denominator) <= ANGULAR_EPSILON:
            continue
        distance = dot(subtract(start, origin), normal) / denominator
        if distance <= DISTANCE_EPSILON_M:
            continue
        hit = add(origin, scale(direction, distance))
        edge_length_squared = edge[0] * edge[0] + edge[1] * edge[1]
        along = dot(subtract(hit, start), edge) / edge_length_squared
        if (
            -DISTANCE_EPSILON_M <= along <= 1.0 + DISTANCE_EPSILON_M
            and -DISTANCE_EPSILON_M <= hit[2] <= room.height_m + DISTANCE_EPSILON_M
        ):
            hits.append(RayHit(distance, hit, f"wall:{index}", "wall"))

    for height, object_id, kind in (
        (0.0, "floor", "floor"),
        (room.height_m, "ceiling", "ceiling"),
    ):
        if abs(direction[2]) <= ANGULAR_EPSILON:
            continue
        distance = (height - origin[2]) / direction[2]
        if distance <= DISTANCE_EPSILON_M:
            continue
        hit = add(origin, scale(direction, distance))
        if _point_in_floor(room, hit):
            hits.append(RayHit(distance, hit, object_id, kind))

    if not hits:
        return None
    hits.sort(key=lambda hit: (hit.distance_m, hit.object_id))
    nearest = hits[0]
    if len(hits) > 1 and abs(hits[1].distance_m - nearest.distance_m) <= DISTANCE_EPSILON_M:
        return RayHit(nearest.distance_m, nearest.hit_m, "boundary:ambiguous", "ambiguous")
    return nearest


def _localize(point: Vector3, cuboid: Cuboid) -> Vector3:
    angle = radians(cuboid.yaw_deg_ccw_from_east)
    cosine, sine = cos(angle), sin(angle)
    relative = subtract(point, cuboid.base_center_m)
    return (
        relative[0] * cosine + relative[1] * sine,
        -relative[0] * sine + relative[1] * cosine,
        relative[2],
    )


def _local_direction(direction: Vector3, cuboid: Cuboid) -> Vector3:
    angle = radians(cuboid.yaw_deg_ccw_from_east)
    cosine, sine = cos(angle), sin(angle)
    return (
        direction[0] * cosine + direction[1] * sine,
        -direction[0] * sine + direction[1] * cosine,
        direction[2],
    )


def point_strictly_inside_cuboid(point: Vector3, cuboid: Cuboid) -> bool:
    """Return whether point is in the cuboid interior, excluding its faces."""

    local = _localize(point, cuboid)
    width, depth, height = cuboid.dimensions_m
    return (
        -width / 2 + DISTANCE_EPSILON_M < local[0] < width / 2 - DISTANCE_EPSILON_M
        and -depth / 2 + DISTANCE_EPSILON_M < local[1] < depth / 2 - DISTANCE_EPSILON_M
        and DISTANCE_EPSILON_M < local[2] < height - DISTANCE_EPSILON_M
    )


def intersect_cuboid(origin: Vector3, direction: Vector3, cuboid: Cuboid) -> RayHit | None:
    """Return the first positive opaque intersection using a local-frame slab test."""

    local_origin = _localize(origin, cuboid)
    local_direction = _local_direction(direction, cuboid)
    width, depth, height = cuboid.dimensions_m
    bounds = ((-width / 2, width / 2), (-depth / 2, depth / 2), (0.0, height))
    entry, exit = float("-inf"), float("inf")
    for coordinate, component, (low, high) in zip(
        local_origin, local_direction, bounds, strict=True
    ):
        if abs(component) <= ANGULAR_EPSILON:
            if coordinate < low - DISTANCE_EPSILON_M or coordinate > high + DISTANCE_EPSILON_M:
                return None
            continue
        first = (low - coordinate) / component
        second = (high - coordinate) / component
        entry, exit = max(entry, min(first, second)), min(exit, max(first, second))
        if entry > exit + DISTANCE_EPSILON_M:
            return None
    if exit <= DISTANCE_EPSILON_M:
        return None
    if entry <= DISTANCE_EPSILON_M:
        shifted = add(origin, scale(direction, DISTANCE_EPSILON_M * 10))
        if not point_strictly_inside_cuboid(shifted, cuboid):
            return None
        distance = 0.0
    else:
        distance = entry
    return RayHit(
        distance,
        add(origin, scale(direction, distance)),
        cuboid.occluder_id,
        "occluder",
    )
