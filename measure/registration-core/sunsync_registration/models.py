from datetime import datetime
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def rigid(value):
    a = np.asarray(value, float)
    if a.size != 16:
        raise ValueError("Transform needs 16 row-major elements")
    a = a.reshape(4, 4)
    if not np.isfinite(a).all() or not np.allclose(a[3], [0, 0, 0, 1], atol=1e-7):
        raise ValueError("Invalid homogeneous transform")
    r = a[:3, :3]
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-6) or not np.isclose(
        np.linalg.det(r), 1, atol=1e-6
    ):
        raise ValueError("Rotation must be orthonormal and right-handed")
    return a


class Camera(Strict):
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float
    cy: float
    pixel_space: Literal["raw_opencv", "rectified"]
    # Missing distortion is not silently interpreted as a perfect lens.
    distortion: list[float]
    source: str = Field(min_length=1, max_length=200)
    lens_id: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def geometry(self):
        if not 0 <= self.cx < self.width or not 0 <= self.cy < self.height:
            raise ValueError("Principal point outside image")
        if self.pixel_space == "raw_opencv" and len(self.distortion) not in (
            4,
            5,
            8,
            12,
            14,
        ):
            raise ValueError("Raw pixels require explicit OpenCV distortion coefficients")
        if self.pixel_space == "rectified" and self.distortion:
            raise ValueError("Rectified pixels use distortion=[]")
        return self

    def matrices(self):
        return np.array(
            [[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1.0]], float
        ), np.asarray(self.distortion or [0] * 5, float)


class Board(Strict):
    board_id: str = Field(min_length=1, max_length=100)
    dictionary: Literal["DICT_4X4_50"] = "DICT_4X4_50"
    # Production film: one 6x6-cell marker on a 10x10 grid of 10cm cells.
    # Explicit 2x2 layouts remain readable for historical experiments.
    ids_by_row: tuple[tuple[int, ...], ...] = ((0,),)
    marker_side_m: float = Field(default=0.6, gt=0, le=5)
    gap_x_m: float = Field(default=0, ge=0, le=5)
    gap_y_m: float = Field(default=0, ge=0, le=5)
    border_bits: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def ids(self):
        ids = [v for row in self.ids_by_row for v in row]
        layout = tuple(len(row) for row in self.ids_by_row)
        if (
            layout not in ((1,), (2, 2))
            or len(set(ids)) != len(ids)
            or any(not 0 <= x < 50 for x in ids)
        ):
            raise ValueError(
                "Board requires a single ID or an explicit legacy 2x2 layout with distinct DICT_4X4_50 IDs"
            )
        if layout == (1,) and (self.gap_x_m or self.gap_y_m):
            raise ValueError("Single-marker board has no inter-marker gaps")
        return self

    def objects(self):
        result = {}
        s = self.marker_side_m
        for row in range(len(self.ids_by_row)):
            for col in range(len(self.ids_by_row[row])):
                x, y = col * (s + self.gap_x_m), row * (s + self.gap_y_m)
                result[self.ids_by_row[row][col]] = np.array(
                    [[x, y, 0], [x + s, y, 0], [x + s, y + s, 0], [x, y + s, 0]], float
                )
        return result


class Shape(Strict):
    kind: Literal["rectangle", "circle", "ellipse", "polygon"]
    vertices: list[Vec2] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def topology(self):
        if self.kind == "polygon":
            from .geometry import validate_polygon

            validate_polygon(self.vertices)
        elif self.vertices:
            raise ValueError("Only polygon uses custom vertices")
        return self


class PlanRequest(Strict):
    space_id: str = Field(min_length=1, max_length=100)
    furniture_id: str = Field(min_length=1, max_length=100)
    furniture_kind: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    shape: Shape
    samples_per_point: int = Field(default=3, ge=1, le=5)
    height_mode: Literal["surface_is_top", "capture_highest", "manual"] = "surface_is_top"
    manual_height_m: float | None = Field(default=None, gt=0, le=10)
    floor_supported: bool = True
    manual_base_height_m: float | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def heights(self):
        if (self.height_mode == "manual") != (self.manual_height_m is not None):
            raise ValueError("manual mode requires manual_height_m; other modes must omit it")
        if not self.floor_supported and (
            self.height_mode != "manual" or self.manual_base_height_m is None
        ):
            raise ValueError("Floating furniture requires manual height and manual base height")
        if self.floor_supported and self.manual_base_height_m is not None:
            raise ValueError("Floor-supported model base is floor, not manual")
        return self


class Floor(Strict):
    board_origin_height_m: float = Field(ge=0, le=20)
    up_board: Vec3

    @model_validator(mode="after")
    def unit(self):
        if not np.isclose(np.linalg.norm(self.up_board), 1, atol=1e-6):
            raise ValueError("up_board must be a measured/confirmed unit vector")
        return self


class Alignment(Strict):
    T_enu_board: list[float] = Field(min_length=16, max_length=16)
    north_confirmed: Literal[True]
    floor_confirmed: Literal[True]

    @model_validator(mode="after")
    def transform(self):
        rigid(self.T_enu_board)
        return self


class Detection(Strict):
    marker_id: int = Field(ge=0, le=49)
    corners_px: tuple[Vec2, Vec2, Vec2, Vec2]


class Capture(Strict):
    capture_id: str = Field(min_length=1, max_length=100)
    point_id: str
    captured_at: str | None = None
    camera: Camera
    detections: list[Detection] = Field(max_length=100)
    image_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    # OpenCV camera axes: right, down, forward. Optical centre -> contact point.
    camera_to_point_m: Vec3 = (0, 0, 0)

    @model_validator(mode="after")
    def capture_time(self):
        if self.captured_at is not None:
            value = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("captured_at requires a timezone")
        return self


class MeasureRequest(Strict):
    plan: PlanRequest
    board: Board
    captures: list[Capture] = Field(min_length=1, max_length=340)
    floor: Floor | None = None
    alignment: Alignment | None = None
    estimation_mode: Literal["legacy", "weighted", "robust_joint"] = "robust_joint"
    max_point_uncertainty_m: float = Field(default=0.15, gt=0, le=0.5)
    placement_sigma_m: float = Field(default=0.01, ge=0.003, le=0.03)
    max_selected_per_point: int = Field(default=8, ge=3, le=12)
    max_repeat_spread_m: float = Field(default=0.03, gt=0, le=0.1)
    max_surface_residual_m: float = Field(default=0.03, gt=0, le=0.1)
    max_reprojection_rmse_px: float = Field(default=2, gt=0, le=5)

    @model_validator(mode="after")
    def bounded_points(self):
        counts = {}
        for capture in self.captures:
            counts[capture.point_id] = counts.get(capture.point_id, 0) + 1
        if any(count > 20 for count in counts.values()):
            raise ValueError("At most 20 raw captures per point")
        return self


class SelectRequest(Strict):
    board: Board
    captures: list[Capture] = Field(min_length=1, max_length=20)
    required_samples: int = Field(default=3, ge=1, le=5)
    max_selected: int = Field(default=8, ge=3, le=12)
    max_repeat_spread_m: float = Field(default=0.03, gt=0, le=0.1)
    max_reprojection_rmse_px: float = Field(default=2, gt=0, le=5)
    max_point_uncertainty_m: float = Field(default=0.15, gt=0, le=0.5)
    placement_sigma_m: float = Field(default=0.01, ge=0.003, le=0.03)


class DetectRequest(Strict):
    camera: Camera
    board: Board
    image_base64: str = Field(min_length=1, max_length=12_000_000)


Positive = Annotated[float, Field(gt=0)]


class ModelPlacement(Strict):
    model_id: str = Field(min_length=1, max_length=200)
    dimensions_m: tuple[Positive, Positive, Positive]
    T_board_model: list[float] = Field(min_length=16, max_length=16)
    base_center_board_m: Vec3
    height_source: Literal["surface_is_top", "capture_highest", "manual"]
    floor_supported: StrictBool
    origin: Literal["base_bounding_box_center"] = "base_bounding_box_center"
    local_axes: Literal["x width, y depth, z up"] = "x width, y depth, z up"
    shape_approximation: Literal[True] = True
    orientation_requires_user_review: Literal[True] = True
    T_enu_model: list[float] | None = Field(default=None, min_length=16, max_length=16)
    base_center_enu_m: Vec3 | None = None
    yaw_deg_ccw_from_east: float | None = Field(default=None, ge=0, lt=360)
