"""Small ENU geometry checks; tolerances are numerical, not measurement accuracy."""

from collections.abc import Sequence
from math import cos, radians, sin

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

EPS = 1e-6
AREA_EPS = 1e-8
Vector = Sequence[float]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def subtract(a: Vector, b: Vector) -> tuple[float, ...]:
    return tuple(x - y for x, y in zip(a, b, strict=True))


def dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def cross(a: Vector, b: Vector) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def close(a: Vector, b: Vector) -> bool:
    return all(abs(x - y) <= EPS for x, y in zip(a, b, strict=True))


def unit(vector: Vector, label: str) -> None:
    require(abs(dot(vector, vector) - 1) <= EPS, f"{label}: normal/axis must be unit length")


def projection_axes(normal: Vector) -> list[int]:
    omitted = max(range(3), key=lambda axis: abs(normal[axis]))
    return [axis for axis in range(3) if axis != omitted]


def project(point: Vector, axes: Sequence[int]) -> tuple[float, ...]:
    return tuple(point[axis] for axis in axes)


def polygon(vertices: Sequence[Vector], normal: Vector, label: str) -> Polygon:
    """Check an open ring with right-hand winding and return its 2D projection."""
    unit(normal, label)
    require(
        len({tuple(point) for point in vertices}) == len(vertices),
        f"{label}: duplicate ring vertex",
    )
    require(
        all(abs(dot(subtract(point, vertices[0]), normal)) <= EPS for point in vertices),
        f"{label}: polygon must be planar",
    )
    edges = list(zip(vertices, [*vertices[1:], vertices[0]], strict=True))
    require(all(not close(a, b) for a, b in edges), f"{label}: zero-length edge")
    # Use relative coordinates to avoid cancellation for a room far from the ENU origin.
    area_vector = tuple(
        sum(cross(subtract(a, vertices[0]), subtract(b, vertices[0]))[axis] for a, b in edges) / 2
        for axis in range(3)
    )
    require(dot(area_vector, normal) > AREA_EPS, f"{label}: zero area or winding opposite normal")
    result = Polygon([project(point, projection_axes(normal)) for point in vertices])
    require(
        result.is_valid and result.area > AREA_EPS, f"{label}: polygon must be simple and nonzero"
    )
    return result


def xy_geometry(vertices: Sequence[Vector]) -> BaseGeometry:
    """Project a surface to the floor, retaining its full interior or line boundary."""
    points = [(point[0], point[1]) for point in vertices]
    shape = Polygon(points)
    return shape if shape.area > AREA_EPS else LineString([*points, points[0]])


def in_room(vertices: Sequence[Vector], floor: Polygon, height: float, label: str) -> None:
    require(
        all(-EPS <= point[2] <= height + EPS for point in vertices)
        and floor.buffer(EPS).covers(xy_geometry(vertices)),
        f"{label}: geometry outside room",
    )


def sample_on_surface(
    point: Vector, vertices: Sequence[Vector], normal: Vector, shape: Polygon
) -> bool:
    return abs(dot(subtract(point, vertices[0]), normal)) <= EPS and shape.buffer(EPS).covers(
        Point(project(point, projection_axes(normal)))
    )


def cuboid_footprint(
    base: Vector, dimensions: Vector, yaw: float
) -> list[tuple[float, float, float]]:
    c, s = cos(radians(yaw)), sin(radians(yaw))
    width, depth, _ = dimensions
    return [
        (base[0] + x * c - y * s, base[1] + x * s + y * c, base[2])
        for x, y in [
            (-width / 2, -depth / 2),
            (width / 2, -depth / 2),
            (width / 2, depth / 2),
            (-width / 2, depth / 2),
        ]
    ]


def cuboid_local(vector: Vector, yaw: float) -> tuple[float, float, float]:
    c, s = cos(radians(yaw)), sin(radians(yaw))
    return (vector[0] * c + vector[1] * s, -vector[0] * s + vector[1] * c, vector[2])
