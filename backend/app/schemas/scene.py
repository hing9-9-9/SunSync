"""FR06 v0.2.0: shared manual/ArUco scene input, without simulation or control.

Coordinates are ENU metres (x east, y north, z up), with the room floor at z=0.
The schema describes structure; model validators also enforce geometry and references.
"""

from __future__ import annotations

from datetime import datetime
from math import atan2, ceil, degrees, floor
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.geometry import LineString, Point, Polygon

from app.schemas.geometry import (
    EPS,
    close,
    cross,
    cuboid_footprint,
    cuboid_local,
    dot,
    in_room,
    polygon,
    require,
    subtract,
    unit,
)
from app.schemas.surface_fit import fit_surface

Number = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[Number, Field(gt=0)]
Nonnegative = Annotated[Number, Field(ge=0)]
Fraction = Annotated[Number, Field(ge=0, le=1)]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")]
# Container strictness is relaxed only for vectors: FastAPI passes JSON arrays as lists.
# Scalar strictness remains enabled, so "3", true, NaN and Infinity are not numbers.
Vector = Annotated[tuple[Number, Number, Number], Field(strict=False)]
Dimensions = Annotated[tuple[Positive, Positive, Positive], Field(strict=False)]
Ring = Annotated[list[Vector], Field(min_length=3)]


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    require(parsed.utcoffset() is not None, "timestamp must include a timezone offset")
    return value


Timestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
        json_schema_extra={"format": "date-time"},
    ),
    AfterValidator(_timestamp),
]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Quality(Model):
    observation_ids: Annotated[list[Identifier], Field(min_length=1)]
    confirmed: bool

    @field_validator("observation_ids")
    @classmethod
    def unique_observations(cls, values: list[str]) -> list[str]:
        require(len(values) == len(set(values)), "duplicate observation reference")
        return values


class Site(Model):
    latitude_deg: Annotated[Number, Field(ge=-90, le=90)]
    longitude_deg: Annotated[Number, Field(ge=-180, le=180)]
    timezone: str
    source: str

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class NorthAlignment(Model):
    source: Literal["assumed", "surveyed", "plan", "true_heading", "manual"]
    accuracy_deg: Nonnegative | None
    confirmed: bool
    note: str


class Room(Model):
    room_id: Identifier
    name: str
    floor_vertices_m: Ring
    height_m: Positive
    quality: Quality
    floor_level: Annotated[int, Field(ge=-100, le=1000)] | None = None

    @model_validator(mode="after")
    def valid_floor(self) -> Self:
        polygon(self.floor_vertices_m, (0, 0, 1), "room")
        require(
            all(abs(point[2]) <= EPS for point in self.floor_vertices_m),
            "room floor must be at z=0",
        )
        return self


class Window(Model):
    window_id: Identifier
    room_id: Identifier
    vertices_m: Annotated[list[Vector], Field(min_length=4, max_length=4)]
    inward_normal: Vector
    u_axis: Vector
    v_axis: Vector
    azimuth_deg_true: Annotated[Number, Field(ge=0, lt=360)]
    optical_profile_id: Identifier
    initial_film_direct_transmittance: Fraction
    quality: Quality
    film_rows: Annotated[int, Field(ge=1)] = 10
    film_cols: Annotated[int, Field(ge=1)] = 10

    @property
    def width_m(self) -> float:
        return float(dot(subtract(self.vertices_m[1], self.vertices_m[0]), self.u_axis))

    @property
    def height_m(self) -> float:
        return float(dot(subtract(self.vertices_m[3], self.vertices_m[0]), self.v_axis))

    @property
    def cell_width_m(self) -> float:
        return self.width_m / self.film_cols

    @property
    def cell_height_m(self) -> float:
        return self.height_m / self.film_rows

    @staticmethod
    def physical_cell_count(length_m: float) -> int:
        return max(1, ceil((length_m - EPS) / 0.1))

    @staticmethod
    def full_module_count(length_m: float) -> int:
        return floor(length_m + EPS)

    @staticmethod
    def _uses_physical_cells(length_m: float, count: int) -> bool:
        # Keep saved UI grids with the old rounded count readable. An outer
        # cell can absorb their sub-cell remainder without moving the marker.
        legacy_count = max(1, floor(length_m / 0.1 + 0.5))
        return count in (Window.physical_cell_count(length_m), legacy_count)

    def film_module_shape(self) -> tuple[int, int]:
        """Selectable 1m modules counted from the indoor bottom-left."""
        rows = self.full_module_count(self.height_m)
        cols = self.full_module_count(self.width_m)
        require(
            rows > 0
            and cols > 0
            and self._uses_physical_cells(self.height_m, self.film_rows)
            and self._uses_physical_cells(self.width_m, self.film_cols),
            "Capture requires at least one complete 1m film module with 10cm cells",
        )
        return rows, cols

    def module_marker_placement(self, row: int, col: int) -> tuple[int, int, float, float]:
        rows, cols = self.film_module_shape()
        require(0 <= row < rows and 0 <= col < cols, "Film module outside window layout")
        return self.film_rows - (row + 1) * 10 + 2, col * 10 + 2, col + 0.5, row + 0.5

    def marker_module_indices(self, start_row: int, start_col: int) -> tuple[int, int]:
        return (self.film_rows - start_row + 2) // 10 - 1, start_col // 10

    def grid_from_cell_size(self, cell_size_m: float = 0.1) -> tuple[int, int]:
        require(cell_size_m > 0, "cell_size_m must be positive")
        cols = round(self.width_m / cell_size_m)
        rows = round(self.height_m / cell_size_m)
        require(
            cols >= 1 and abs(self.width_m - cols * cell_size_m) <= EPS,
            "window width must be an integer multiple of cell_size_m",
        )
        require(
            rows >= 1 and abs(self.height_m - rows * cell_size_m) <= EPS,
            "window height must be an integer multiple of cell_size_m",
        )
        return (rows, cols)

    def uv_to_cell(self, u_m: float, v_m: float) -> tuple[int, int]:
        require(
            -EPS <= u_m <= self.width_m + EPS and -EPS <= v_m <= self.height_m + EPS,
            "point lies outside window bounds",
        )
        u_clamped = min(max(u_m, 0.0), self.width_m)
        v_clamped = min(max(v_m, 0.0), self.height_m)
        if self._uses_physical_cells(self.width_m, self.film_cols):
            col = min(floor((u_clamped + EPS) / 0.1), self.film_cols - 1)
        else:
            col = min(floor((u_clamped + EPS) / self.cell_width_m), self.film_cols - 1)

        if self._uses_physical_cells(self.height_m, self.film_rows):
            # At a shared edge, keep assigning the point to the cell below it.
            from_bottom = min(max(ceil((v_clamped - EPS) / 0.1) - 1, 0), self.film_rows - 1)
            row = self.film_rows - 1 - from_bottom
        else:
            top_dist = self.height_m - v_clamped
            row = min(floor((top_dist + EPS) / self.cell_height_m), self.film_rows - 1)

        return (row, col)

    def cell_to_uv_bounds(
        self, row: int, col: int
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        require(
            type(row) is int and type(col) is int,
            "cell row and col must be integers",
        )
        require(
            0 <= row < self.film_rows and 0 <= col < self.film_cols,
            "cell row/col outside window film grid",
        )
        if self._uses_physical_cells(self.width_m, self.film_cols):
            u_min = col * 0.1
            u_max = self.width_m if col == self.film_cols - 1 else (col + 1) * 0.1
        else:
            u_min, u_max = col * self.cell_width_m, (col + 1) * self.cell_width_m
        if self._uses_physical_cells(self.height_m, self.film_rows):
            from_bottom = self.film_rows - 1 - row
            v_min = from_bottom * 0.1
            v_max = self.height_m if row == 0 else (from_bottom + 1) * 0.1
        else:
            v_min = self.height_m - (row + 1) * self.cell_height_m
            v_max = self.height_m - row * self.cell_height_m
        return ((u_min, u_max), (v_min, v_max))

    def cell_to_uv(self, row: int, col: int) -> tuple[float, float]:
        (u_min, u_max), (v_min, v_max) = self.cell_to_uv_bounds(row, col)
        return ((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)

    def enu_to_uv(self, point_m: Vector) -> tuple[float, float]:
        rel = subtract(point_m, self.vertices_m[0])
        dist_normal = dot(rel, self.inward_normal)
        require(abs(dist_normal) <= EPS, "point is not on window plane")
        u_m = dot(rel, self.u_axis)
        v_m = dot(rel, self.v_axis)
        return (float(u_m), float(v_m))

    def uv_to_enu(self, u_m: float, v_m: float) -> tuple[float, float, float]:
        origin = self.vertices_m[0]
        return (
            float(origin[0] + u_m * self.u_axis[0] + v_m * self.v_axis[0]),
            float(origin[1] + u_m * self.u_axis[1] + v_m * self.v_axis[1]),
            float(origin[2] + u_m * self.u_axis[2] + v_m * self.v_axis[2]),
        )

    def marker_transform(self, u_m: float, v_m: float) -> list[float]:
        """Row-major local-to-ENU transform; local basis vectors are columns."""
        origin = self.uv_to_enu(u_m, v_m)
        return [
            value
            for axis in range(3)
            for value in (
                self.u_axis[axis],
                self.v_axis[axis],
                self.inward_normal[axis],
                origin[axis],
            )
        ] + [0.0, 0.0, 0.0, 1.0]

    @model_validator(mode="after")
    def valid_frame(self) -> Self:
        polygon(self.vertices_m, self.inward_normal, "window")
        unit(self.u_axis, "window u")
        unit(self.v_axis, "window v")
        require(
            close(cross(self.u_axis, self.v_axis), self.inward_normal)
            and abs(dot(self.u_axis, self.v_axis)) <= EPS,
            "invalid window frame",
        )
        points = self.vertices_m
        u_edge, v_edge = subtract(points[1], points[0]), subtract(points[3], points[0])
        require(
            dot(u_edge, self.u_axis) > 0 and close(cross(u_edge, self.u_axis), (0, 0, 0)),
            "window first edge must follow u_axis",
        )
        require(
            dot(v_edge, self.v_axis) > 0 and close(cross(v_edge, self.v_axis), (0, 0, 0)),
            "window last edge must follow v_axis",
        )
        require(close(subtract(points[2], points[1]), v_edge), "window must be rectangular")
        azimuth = degrees(atan2(-self.inward_normal[0], -self.inward_normal[1])) % 360
        require(
            abs((azimuth - self.azimuth_deg_true + 180) % 360 - 180) <= EPS,
            "window azimuth must describe the outward normal clockwise from true north",
        )
        return self


def window_uv_to_cell(window: Window, u_m: float, v_m: float) -> tuple[int, int]:
    return window.uv_to_cell(u_m, v_m)


def window_cell_to_uv_bounds(
    window: Window, row: int, col: int
) -> tuple[tuple[float, float], tuple[float, float]]:
    return window.cell_to_uv_bounds(row, col)


def window_cell_to_uv(window: Window, row: int, col: int) -> tuple[float, float]:
    return window.cell_to_uv(row, col)


FurnitureType = Literal["bookshelf", "desk", "chair", "other"]


class Occluder(Model):
    occluder_id: Identifier
    room_id: Identifier
    name: str
    kind: Literal["cuboid"]
    type: FurnitureType = "other"
    base_center_m: Vector
    dimensions_m: Dimensions
    yaw_deg_ccw_from_east: Number
    material: Literal["opaque"]
    quality: Quality


class ProtectedRegion(Model):
    region_id: Identifier
    room_id: Identifier
    name: str
    host_occluder_id: Identifier | None = None
    vertices_m: Annotated[list[Vector], Field(min_length=4, max_length=4)]
    receiving_normal: Vector
    grade: Literal["A", "B", "C", "D"]
    policy_id: Identifier
    quality: Quality

    @model_validator(mode="after")
    def valid_surface(self) -> Self:
        polygon(self.vertices_m, self.receiving_normal, "protected region")
        p0, p1, p2, p3 = self.vertices_m
        u_edge, v_edge = subtract(p1, p0), subtract(p3, p0)
        require(
            dot(u_edge, u_edge) > EPS and dot(v_edge, v_edge) > EPS,
            "protected region edges must be nonzero",
        )
        require(abs(dot(u_edge, v_edge)) <= EPS, "protected region edges must be perpendicular")
        require(
            close(subtract(p2, p1), v_edge) and close(subtract(p2, p3), u_edge),
            "protected region must be rectangular",
        )
        return self


class Marker(Model):
    marker_id: Identifier
    family: Literal["aruco"]
    dictionary: Literal["DICT_4X4_50"]
    code_id: Annotated[int, Field(ge=0, lt=50)]
    side_length_m: Positive
    plane: Literal["floor", "window", "vertical", "horizontal"]
    T_ENU_marker: Annotated[list[Number], Field(min_length=16, max_length=16)]
    window_id: Identifier | None = None
    start_row: Annotated[int, Field(ge=0)] | None = None
    start_col: Annotated[int, Field(ge=0)] | None = None
    marker_rows: Annotated[int, Field(ge=1)] | None = None
    marker_cols: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def rigid_transform_and_plane(self) -> Self:
        matrix = self.T_ENU_marker
        rows = [matrix[0:3], matrix[4:7], matrix[8:11]]
        require(close(matrix[12:16], (0, 0, 0, 1)), "transform last row must be [0,0,0,1]")
        require(
            all(
                abs(dot(rows[i], rows[j]) - (1 if i == j else 0)) <= EPS
                for i in range(3)
                for j in range(3)
            )
            and abs(dot(rows[0], cross(rows[1], rows[2])) - 1) <= EPS,
            "marker transform must be a right-handed rigid transform",
        )
        if self.plane == "floor":
            require(
                self.window_id is None
                and self.start_row is None
                and self.start_col is None
                and self.marker_rows is None
                and self.marker_cols is None,
                "floor marker cannot declare window cell placement",
            )
            require(
                close(rows[2], (0, 0, 1)) and abs(matrix[11]) <= EPS,
                "marker frame must lie on the z=0 floor",
            )
        elif self.plane == "window":
            require(self.window_id is not None, "window marker requires window_id")
            if self.start_row is None:
                self.start_row = 2
            if self.start_col is None:
                self.start_col = 2
            if self.marker_rows is None:
                self.marker_rows = 6
            if self.marker_cols is None:
                self.marker_cols = 6
            require(
                self.marker_rows == 6 and self.marker_cols == 6,
                "ArUco 4x4 window marker must occupy 6x6 cells including border",
            )
        else:
            require(
                all(
                    value is None
                    for value in (
                        self.window_id,
                        self.start_row,
                        self.start_col,
                        self.marker_rows,
                        self.marker_cols,
                    )
                ),
                "board marker cannot declare window cell placement",
            )
            normal_z = abs(matrix[10])
            require(
                (self.plane == "vertical" and normal_z < 0.05)
                or (self.plane == "horizontal" and normal_z > 0.999),
                "board marker plane disagrees with its ENU transform",
            )
        return self


class ManualObservation(Model):
    observation_id: Identifier
    method: Literal["manual"]
    captured_at: Timestamp
    uncertainty_m: Nonnegative | None
    confirmed: bool
    note: str


class ArucoObservation(Model):
    observation_id: Identifier
    method: Literal["aruco_planar"]
    captured_at: Timestamp
    uncertainty_m: Nonnegative | None
    confirmed: bool
    note: str
    marker_id: Identifier
    plane: Literal["floor"]
    points_marker_m: Annotated[list[Vector], Field(min_length=1)]
    points_enu_m: Annotated[list[Vector], Field(min_length=1)]
    derived_fields: list[str]
    independent_tape_error_m: Nonnegative | None

    @model_validator(mode="after")
    def floor_points(self) -> Self:
        require(len(self.points_marker_m) == len(self.points_enu_m), "ArUco point counts differ")
        require(
            all(abs(point[2]) <= EPS for point in [*self.points_marker_m, *self.points_enu_m]),
            "ArUco planar measurements can only export floor points",
        )
        require(
            len(set(self.derived_fields)) == len(self.derived_fields), "duplicate derived field"
        )
        return self


class BoardCameraObservation(Model):
    """General 3D tabletop observations; legacy floor observations stay unchanged."""

    observation_id: Identifier
    method: Literal["aruco_board_camera_at_point"]
    calibration_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    captured_at: Timestamp
    uncertainty_m: Nonnegative | None
    confirmed: bool
    note: str
    marker_id: Identifier
    plane: Literal["floor", "window", "vertical", "horizontal"]
    point_ids: Annotated[list[Identifier], Field(min_length=3, max_length=17)]
    points_marker_m: Annotated[list[Vector], Field(min_length=3, max_length=17)]
    points_enu_m: Annotated[list[Vector], Field(min_length=3, max_length=17)]
    derived_fields: list[str]
    independent_tape_error_m: Nonnegative | None
    tabletop_shape: Literal["rectangle", "circle", "ellipse", "polygon"]
    tabletop_outline_enu_m: Annotated[list[Vector], Field(min_length=3, max_length=16)] | None = (
        None
    )
    model_id: Annotated[str, Field(min_length=1, max_length=200)]
    geometry_source: Literal["standard_model_bounding_box"]
    height_source: Literal["surface_is_top", "capture_highest", "manual"]
    manual_height_m: Positive | None
    manual_base_height_m: Nonnegative | None
    floor_supported: bool
    floor_alignment_confirmed: Literal[True]
    surface_tolerance_m: Annotated[Positive, Field(le=0.1)]

    @model_validator(mode="after")
    def points_and_height_source(self) -> Self:
        count = {"rectangle": 4, "circle": 4, "ellipse": 6}.get(
            self.tabletop_shape, len(self.point_ids) - int(self.height_source == "capture_highest")
        )
        require(3 <= count <= 16, "invalid tabletop point count")
        expected = {f"P{i + 1}" for i in range(count)}
        if self.height_source == "capture_highest":
            expected.add("H1")
        require(
            len(self.point_ids) == len(set(self.point_ids))
            and set(self.point_ids) == expected
            and len(self.points_marker_m) == len(self.points_enu_m) == len(self.point_ids),
            "board observation needs matching tabletop point IDs and point counts",
        )
        points = dict(zip(self.point_ids, self.points_enu_m, strict=True))
        require(
            (self.tabletop_shape != "polygon" and self.tabletop_outline_enu_m is None)
            or (
                self.tabletop_shape == "polygon"
                and self.tabletop_outline_enu_m is not None
                and len(self.tabletop_outline_enu_m) == count
                and all(
                    close(a, b)
                    for a, b in zip(
                        self.tabletop_outline_enu_m,
                        [points[f"P{i + 1}"] for i in range(count)],
                        strict=True,
                    )
                )
            ),
            "polygon outline must match measured points",
        )
        require(
            len(set(self.derived_fields)) == len(self.derived_fields),
            "duplicate derived field",
        )
        require(
            (self.height_source == "manual") == (self.manual_height_m is not None),
            "manual height source requires an explicit measured height",
        )
        require(
            self.floor_supported or self.height_source == "manual",
            "floating furniture requires explicit manual height",
        )
        require(
            self.floor_supported == (self.manual_base_height_m is None),
            "floating furniture requires explicit manual base height",
        )
        prefixes = {field.partition(".")[0] for field in self.derived_fields}
        require(len(prefixes) == 1, "board observation must derive exactly one cuboid")
        identifier = next(iter(prefixes))
        require(
            set(self.derived_fields)
            == {
                identifier + ".dimensions_m[0:2]",
                identifier + ".dimensions_m[2]",
                identifier + ".yaw_deg_ccw_from_east",
                identifier + ".base_center_m",
            },
            "board observation must declare dimensions, height, yaw and base centre",
        )
        return self


Observation = Annotated[
    ManualObservation | ArucoObservation | BoardCameraObservation,
    Field(discriminator="method"),
]


class OpticalProfile(Model):
    optical_profile_id: Identifier
    status: Literal["assumed"]
    model: Literal["direct_scalar_transmittance"]
    glazing_direct_transmittance: Fraction
    film_min_direct_transmittance: Fraction
    film_max_direct_transmittance: Fraction
    actual_sgf_properties: None

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        require(
            self.film_min_direct_transmittance <= self.film_max_direct_transmittance,
            "film minimum transmittance exceeds maximum",
        )
        return self


class GradePolicy(Model):
    rank: Annotated[int, Field(ge=1, le=4)]
    constraint: Literal["hard", "soft", "none"]
    rule: Literal["block_geometric_direct_paths", "intensity_cap", "daylight_priority"]
    max_direct_irradiance_W_m2: Nonnegative | None


class Grades(Model):
    A: GradePolicy = Field(
        description="Hard geometric path blocking; applies even with zero or unknown DNI."
    )
    B: GradePolicy
    C: GradePolicy
    D: GradePolicy = Field(
        description="Daylight preference, subject to shared A/B/C film constraints."
    )

    @model_validator(mode="after")
    def ordered_policies(self) -> Self:
        for rank, grade in enumerate((self.A, self.B, self.C, self.D), 1):
            require(grade.rank == rank, "grade priorities must be A=1, B=2, C=3, D=4")
        require(
            self.A.constraint == "hard"
            and self.A.rule == "block_geometric_direct_paths"
            and self.A.max_direct_irradiance_W_m2 == 0,
            "A requires geometric direct-path blocking, independent of weather estimates",
        )
        require(
            all(
                grade.constraint == "soft" and grade.rule == "intensity_cap"
                for grade in (self.B, self.C)
            ),
            "B/C require instantaneous direct irradiance caps",
        )
        b, c = self.B.max_direct_irradiance_W_m2, self.C.max_direct_irradiance_W_m2
        require(b is not None and c is not None and 0 < b < c, "B/C caps must satisfy 0 < B < C")
        require(
            self.D.constraint == "none"
            and self.D.rule == "daylight_priority"
            and self.D.max_direct_irradiance_W_m2 is None,
            "D is a daylight preference without an irradiance cap",
        )
        return self


class ProtectionPolicy(Model):
    policy_id: Identifier
    version: Literal["0.2.0"]
    status: Literal["proposal_demo_only"]
    metric: Literal["direct_solar_irradiance_W_m2"]
    aggregation: Literal["max_over_sample_points"]
    threshold_status: Literal["synthetic_demo_not_agreed"]
    max_time_step_s: Positive
    brightening_margin_W_m2: Nonnegative
    brightening_hold_s: Nonnegative
    grades: Grades

    @model_validator(mode="after")
    def valid_margin(self) -> Self:
        cap = self.grades.B.max_direct_irradiance_W_m2
        require(
            cap is not None and self.brightening_margin_W_m2 < cap,
            "brightening margin must be below the B cap",
        )
        return self


class WeatherRecord(Model):
    valid_time: Timestamp
    interval_start: Timestamp | None = None
    interval_end: Timestamp | None = None
    dni_W_m2: Nonnegative | None = Field(
        description="Direct normal irradiance in W/m²; null is unknown, not zero."
    )


class Weather(Model):
    weather_id: Identifier
    source: Literal["synthetic", "open_meteo", "pvlib_clear_sky"]
    provider_hint: str
    variable: Literal["direct_normal_irradiance", "direct_normal_irradiance_instant"]
    unit: Literal["W/m2"]
    temporal_meaning: Literal["instant", "preceding_hour_mean"]
    fetched_at: Timestamp | None
    forecast_reference_time: Timestamp | None
    source_updated_at: Timestamp | None
    location_source: Literal["scene.site"]
    is_local_sensor: Literal[False]
    interpolation: Literal["linear_estimate", "none"]
    max_age_s: Positive
    records: Annotated[list[WeatherRecord], Field(min_length=1)]

    @field_validator("is_local_sensor", mode="before")
    @classmethod
    def strict_false(cls, value: object) -> object:
        require(value is False, "weather is_local_sensor must be false")
        return value

    @model_validator(mode="after")
    def valid_intervals(self) -> Self:
        if self.source == "open_meteo":
            require(self.fetched_at is not None, "Open-Meteo weather requires fetched_at")
        instant = self.temporal_meaning == "instant"
        expected = "direct_normal_irradiance_instant" if instant else "direct_normal_irradiance"
        require(self.variable == expected, "weather variable and temporal meaning differ")
        if not instant:
            require(
                self.interpolation == "none", "hourly mean DNI cannot use instant interpolation"
            )
        previous: datetime | None = None
        for record in self.records:
            time = _time(record.valid_time)
            require(
                previous is None or time > previous, "weather times must be strictly increasing"
            )
            previous = time
            if instant:
                require(
                    record.interval_start is None and record.interval_end is None,
                    "instant DNI must not declare an averaging interval",
                )
            else:
                require(
                    record.interval_start is not None and record.interval_end is not None,
                    "mean DNI requires both interval bounds",
                )
                assert record.interval_start is not None and record.interval_end is not None
                require(
                    _time(record.interval_end) == time
                    and (time - _time(record.interval_start)).total_seconds() == 3600,
                    "preceding-hour mean must cover the hour ending at valid_time",
                )
        return self


class Scenario(Model):
    scenario_id: Identifier
    scene_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    start_time: Timestamp
    end_time: Timestamp
    time_step_s: Positive
    weather_id: Identifier
    policy_id: Identifier

    @model_validator(mode="after")
    def valid_order(self) -> Self:
        require(_time(self.end_time) > _time(self.start_time), "scenario end must follow start")
        return self


class RegistrationCalibration(Model):
    """Explicitly registered board/ENU/floor calibration, before furniture import."""

    calibration_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    marker: Marker


class Scene(Model):
    schema_version: Literal["0.2.0"]
    scene_id: Identifier
    revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    data_kind: Literal["synthetic_fixture", "measured_draft"]
    created_at: Timestamp
    units: Literal["m"]
    coordinate_frame: Literal["ENU"]
    site: Site
    north_alignment: NorthAlignment
    room: Room
    registration_calibration: RegistrationCalibration | None = None
    windows: list[Window] = Field(default_factory=list)
    occluders: list[Occluder] = Field(default_factory=list)
    protected_regions: list[ProtectedRegion] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    observations: Annotated[list[Observation], Field(min_length=1)]
    optical_profiles: list[OpticalProfile] = Field(default_factory=list)
    policy: ProtectionPolicy | None = None
    scenario: Scenario | None = None
    weather: Weather | None = None

    @model_validator(mode="after")
    def coherent_scene(self) -> Self:
        self._validate_references_and_provenance()
        self._validate_room_geometry()
        self._validate_measurements()
        if self.scenario is not None:
            require(
                self.policy is not None and self.weather is not None,
                "scenario requires policy and weather",
            )
            assert self.policy is not None and self.weather is not None
            require(
                self.scenario.scene_revision == self.revision,
                "scenario references a stale scene revision",
            )
            require(
                self.scenario.policy_id == self.policy.policy_id,
                "scenario policy reference missing",
            )
            require(
                self.scenario.weather_id == self.weather.weather_id,
                "scenario weather reference missing",
            )
            require(
                self.scenario.time_step_s <= self.policy.max_time_step_s,
                "scenario exceeds policy time step",
            )
        return self

    def _geometry_objects(self) -> dict[str, Room | Window | Occluder | ProtectedRegion]:
        return {
            self.room.room_id: self.room,
            **{window.window_id: window for window in self.windows},
            **{occluder.occluder_id: occluder for occluder in self.occluders},
            **{region.region_id: region for region in self.protected_regions},
        }

    def _validate_references_and_provenance(self) -> None:
        ids = [
            self.scene_id,
            self.room.room_id,
            *(window.window_id for window in self.windows),
            *(occluder.occluder_id for occluder in self.occluders),
            *(region.region_id for region in self.protected_regions),
            *(marker.marker_id for marker in self.markers),
            *(observation.observation_id for observation in self.observations),
            *(profile.optical_profile_id for profile in self.optical_profiles),
        ]
        ids.extend(
            obj_id
            for obj_id in [
                self.policy.policy_id if self.policy else None,
                self.scenario.scenario_id if self.scenario else None,
                self.weather.weather_id if self.weather else None,
            ]
            if obj_id is not None
        )
        require(len(ids) == len(set(ids)), "scene identifiers must be unique")
        observations = {
            observation.observation_id: observation for observation in self.observations
        }
        for identifier, obj in self._geometry_objects().items():
            require(obj.room_id == self.room.room_id, f"{identifier}: missing room reference")
            require(
                all(ref in observations for ref in obj.quality.observation_ids),
                f"{identifier}: missing observation reference",
            )
            sources = [observations[ref] for ref in obj.quality.observation_ids]
            require(
                not obj.quality.confirmed or all(source.confirmed for source in sources),
                f"{identifier}: confirmed geometry refers to an unconfirmed observation",
            )
            needs_height = isinstance(obj, (Room, Window, Occluder)) or (
                isinstance(obj, ProtectedRegion)
                and any(abs(point[2]) > EPS for point in obj.vertices_m)
            )
            require(
                not needs_height
                or any(isinstance(source, ManualObservation) for source in sources)
                or (
                    isinstance(obj, Occluder)
                    and any(
                        isinstance(source, BoardCameraObservation)
                        and f"{identifier}.dimensions_m[2]" in source.derived_fields
                        for source in sources
                    )
                ),
                f"{identifier}: height/elevated geometry needs manual observation provenance",
            )
        for region in self.protected_regions:
            require(
                self.policy is not None and region.policy_id == self.policy.policy_id,
                f"{region.region_id}: missing protection policy reference",
            )
        optics = {profile.optical_profile_id: profile for profile in self.optical_profiles}
        for window in self.windows:
            require(
                window.optical_profile_id in optics,
                f"{window.window_id}: missing optical profile reference",
            )
            profile = optics[window.optical_profile_id]
            require(
                profile.film_min_direct_transmittance
                <= window.initial_film_direct_transmittance
                <= profile.film_max_direct_transmittance,
                "initial film transmittance outside optical profile range",
            )

    def _validate_room_geometry(self) -> None:
        floor = Polygon([(point[0], point[1]) for point in self.room.floor_vertices_m])
        height = self.room.height_m
        floor_edges = list(
            zip(
                self.room.floor_vertices_m,
                [*self.room.floor_vertices_m[1:], self.room.floor_vertices_m[0]],
                strict=True,
            )
        )
        for window in self.windows:
            in_room(window.vertices_m, floor, height, "window")
            require(abs(window.inward_normal[2]) <= EPS, "window normal must be horizontal")
            walls = [LineString([(a[0], a[1]), (b[0], b[1])]) for a, b in floor_edges]
            require(
                any(
                    all(wall.distance(Point(point[:2])) <= EPS for point in window.vertices_m)
                    for wall in walls
                ),
                "window must lie on one room wall",
            )
            center = [
                sum(point[axis] for point in window.vertices_m) / 4
                + window.inward_normal[axis] * EPS * 10
                for axis in range(3)
            ]
            require(floor.contains(Point(center[:2])), "window normal must point into the room")
        occluders = {occluder.occluder_id: occluder for occluder in self.occluders}
        for occluder in self.occluders:
            base = cuboid_footprint(
                occluder.base_center_m, occluder.dimensions_m, occluder.yaw_deg_ccw_from_east
            )
            top = [(x, y, z + occluder.dimensions_m[2]) for x, y, z in base]
            in_room(base, floor, height, "cuboid base")
            in_room(top, floor, height, "cuboid top")
        for region in self.protected_regions:
            in_room(region.vertices_m, floor, height, "protected region")
            if region.host_occluder_id is None:
                continue
            require(region.host_occluder_id in occluders, "protected region host reference missing")
            host = occluders[region.host_occluder_id]
            local = [
                cuboid_local(subtract(point, host.base_center_m), host.yaw_deg_ccw_from_east)
                for point in region.vertices_m
            ]
            width, depth, host_height = host.dimensions_m
            bounds = [(-width / 2, width / 2), (-depth / 2, depth / 2), (0, host_height)]
            require(
                all(
                    all(
                        low - EPS <= point[axis] <= high + EPS
                        for axis, (low, high) in enumerate(bounds)
                    )
                    for point in local
                ),
                "protected region outside host bounds",
            )
            faces = [
                (axis, sign)
                for axis, pair in enumerate(bounds)
                for sign, edge in zip((-1, 1), pair)
                if all(abs(point[axis] - edge) <= EPS for point in local)
            ]
            require(bool(faces), "protected region must lie on a host face")
            local_normal = cuboid_local(region.receiving_normal, host.yaw_deg_ccw_from_east)
            require(
                any(
                    close(
                        local_normal,
                        tuple(sign if component == axis else 0 for component in range(3)),
                    )
                    for axis, sign in faces
                ),
                "protected region host normal must point outward",
            )

    def _validate_measurements(self) -> None:
        windows = {window.window_id: window for window in self.windows}
        for marker in self.markers:
            if marker.plane == "window":
                require(
                    marker.window_id in windows, f"{marker.marker_id}: missing window reference"
                )
                assert marker.window_id is not None
                window = windows[marker.window_id]
                assert (
                    marker.start_row is not None
                    and marker.start_col is not None
                    and marker.marker_rows is not None
                    and marker.marker_cols is not None
                )
                require(
                    marker.start_row + marker.marker_rows <= window.film_rows
                    and marker.start_col + marker.marker_cols <= window.film_cols,
                    f"{marker.marker_id}: marker cells exceed window film grid",
                )
                (u_min, _), (_, v_max) = window.cell_to_uv_bounds(
                    marker.start_row, marker.start_col
                )
                (_, u_max), (v_min, _) = window.cell_to_uv_bounds(
                    marker.start_row + marker.marker_rows - 1,
                    marker.start_col + marker.marker_cols - 1,
                )
                expected_w = u_max - u_min
                expected_h = v_max - v_min
                require(
                    abs(marker.side_length_m - expected_w) <= EPS
                    and abs(marker.side_length_m - expected_h) <= EPS,
                    f"{marker.marker_id}: side_length_m disagrees with window cell size",
                )
                matrix = marker.T_ENU_marker
                require(
                    close(matrix[0:12:4], window.u_axis)
                    and close(matrix[1:12:4], window.v_axis)
                    and close(matrix[2:12:4], window.inward_normal),
                    f"{marker.marker_id}: window marker rotation columns must match "
                    "u_axis, v_axis, inward_normal; call marker-pairing to refresh legacy markers",
                )
                origin = (matrix[3], matrix[7], matrix[11])
                u_m, v_m = window.enu_to_uv(origin)
                require(
                    -EPS <= u_m <= window.width_m + EPS and -EPS <= v_m <= window.height_m + EPS,
                    f"{marker.marker_id}: marker transform origin outside window",
                )
        markers = {marker.marker_id: marker for marker in self.markers}
        objects = self._geometry_objects()
        for observation in self.observations:
            if not isinstance(observation, (ArucoObservation, BoardCameraObservation)):
                continue
            require(observation.marker_id in markers, "ArUco marker reference missing")
            marker = markers[observation.marker_id]
            if isinstance(observation, ArucoObservation):
                require(
                    marker.plane == "floor",
                    "ArUco planar floor observation requires a floor marker",
                )
            else:
                calibration = self.registration_calibration
                require(
                    calibration is not None
                    and calibration.calibration_id == observation.calibration_id
                    and calibration.marker == marker,
                    "board observation must match the scene's persisted calibration",
                )
                require(
                    self.north_alignment.confirmed, "board measurements require confirmed ENU north"
                )
                require(
                    marker.plane == observation.plane, "board observation marker plane mismatch"
                )
            matrix = marker.T_ENU_marker
            for local, exported in zip(
                observation.points_marker_m, observation.points_enu_m, strict=True
            ):
                transformed = tuple(
                    dot(matrix[start : start + 3], local) + matrix[start + 3] for start in (0, 4, 8)
                )
                require(
                    close(transformed, exported),
                    "ArUco exported point disagrees with marker transform",
                )
            for field in observation.derived_fields:
                identifier, separator, attribute = field.partition(".")
                require(
                    bool(separator) and identifier in objects,
                    "ArUco derived field references unknown geometry",
                )
                obj = objects[identifier]
                if isinstance(observation, BoardCameraObservation):
                    require(isinstance(obj, Occluder), "board observations derive furniture only")
                require(
                    observation.observation_id in obj.quality.observation_ids,
                    "ArUco derived field is missing from object provenance",
                )
                allowed: set[str] = set()
                if isinstance(obj, Room):
                    allowed = {"floor_vertices_m"}
                elif isinstance(obj, Occluder):
                    allowed = {
                        "dimensions_m[0:2]",
                        "dimensions_m[0]",
                        "dimensions_m[1]",
                        "yaw_deg_ccw_from_east",
                    }
                    if isinstance(observation, BoardCameraObservation):
                        allowed.update({"dimensions_m[2]", "base_center_m"})
                        self._validate_board_cuboid(observation, obj)
                    elif abs(obj.base_center_m[2]) <= EPS:
                        allowed.add("base_center_m")
                elif isinstance(obj, ProtectedRegion) and all(
                    abs(point[2]) <= EPS for point in obj.vertices_m
                ):
                    allowed = {"vertices_m"}
                require(
                    attribute in allowed,
                    "ArUco planar derived fields cannot supply height or elevated geometry",
                )

    @staticmethod
    def _validate_board_cuboid(observation: BoardCameraObservation, obj: Occluder) -> None:
        points = dict(zip(observation.point_ids, observation.points_enu_m, strict=True))
        keys = sorted((key for key in points if key != "H1"), key=lambda key: int(key[1:]))
        p = [points[key] for key in keys]
        tolerance = observation.surface_tolerance_m
        fit = fit_surface(observation.tabletop_shape, p, tolerance)
        centre = fit["centre"]
        require(
            max(abs(point[2] - centre[2]) for point in p) <= tolerance,
            "board tabletop must be horizontal",
        )
        require(
            abs(obj.dimensions_m[0] - fit["width"]) <= tolerance
            and abs(obj.dimensions_m[1] - fit["depth"]) <= tolerance
            and all(abs(obj.base_center_m[i] - centre[i]) <= tolerance for i in (0, 1)),
            "cuboid placement/dimensions disagree with board points",
        )
        axis = fit["axis"]
        yaw = degrees(atan2(axis[1], axis[0])) % 360
        # Ellipse axes have no distinguished positive end. Its box is identical after 180 degrees.
        period = 180 if observation.tabletop_shape == "ellipse" else 360
        require(
            abs((obj.yaw_deg_ccw_from_east - yaw + period / 2) % period - period / 2) <= 0.001,
            "cuboid yaw disagrees with board points",
        )
        base = obj.base_center_m[2]
        if observation.floor_supported:
            require(abs(base) <= EPS, "floor-supported board furniture must start at z=0")
        else:
            require(
                observation.manual_base_height_m is not None
                and abs(base - observation.manual_base_height_m) <= EPS,
                "cuboid disagrees with explicit manual base height",
            )
        if observation.height_source == "manual":
            require(
                observation.manual_height_m is not None
                and abs(obj.dimensions_m[2] - observation.manual_height_m) <= EPS
                and base - tolerance <= centre[2] <= base + obj.dimensions_m[2] + tolerance,
                "cuboid disagrees with explicit manual height",
            )
        else:
            top = points["H1"][2] if observation.height_source == "capture_highest" else centre[2]
            require(
                top >= centre[2] - tolerance
                and abs(obj.dimensions_m[2] - (top - base)) <= tolerance,
                "cuboid height disagrees with board height point",
            )
