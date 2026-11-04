"""Versioned transport for the backend FR-06 registration endpoint."""

from typing import Annotated, Literal
import numpy as np
from pydantic import AwareDatetime, Field, model_validator
from .models import Strict, Vec3, Positive, rigid

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")]
Plane = Literal["vertical", "horizontal"]


class FragmentMarker(Strict):
    marker_id: Identifier
    family: Literal["aruco"]
    dictionary: Literal["DICT_4X4_50"]
    code_id: int = Field(ge=0, lt=50)
    side_length_m: Positive
    plane: Plane
    T_ENU_marker: list[float] = Field(min_length=16, max_length=16)

    @model_validator(mode="after")
    def geometry(self):
        t = rigid(self.T_ENU_marker)
        normal_up = abs(t[2, 2])
        if (self.plane == "vertical" and normal_up >= 0.05) or (
            self.plane == "horizontal" and normal_up <= 0.999
        ):
            raise ValueError("Marker plane disagrees with ENU transform")
        return self


class FragmentObservation(Strict):
    observation_id: Identifier
    method: Literal["aruco_board_camera_at_point"]
    captured_at: AwareDatetime
    uncertainty_m: float | None = Field(ge=0)
    confirmed: Literal[False]
    note: str
    marker_id: Identifier
    plane: Plane
    point_ids: list[Identifier] = Field(min_length=4, max_length=5)
    points_marker_m: list[Vec3] = Field(min_length=1)
    points_enu_m: list[Vec3] = Field(min_length=1)
    derived_fields: list[str]
    independent_tape_error_m: float | None = Field(ge=0)
    tabletop_shape: Literal["rectangle"]
    model_id: str = Field(min_length=1, max_length=200)
    geometry_source: Literal["standard_model_bounding_box"]
    height_source: Literal["surface_is_top", "capture_highest", "manual"]
    manual_height_m: Positive | None
    manual_base_height_m: float | None = Field(ge=0)
    floor_supported: bool
    floor_alignment_confirmed: Literal[True]
    surface_tolerance_m: Positive = Field(le=0.1)

    @model_validator(mode="after")
    def points_and_height(self):
        expected = {"P1", "P2", "P3", "P4"}
        if self.height_source == "capture_highest":
            expected.add("H1")
        if (
            len(set(self.point_ids)) != len(self.point_ids)
            or set(self.point_ids) != expected
            or len(self.point_ids) != len(self.points_marker_m)
        ):
            raise ValueError("Expected rectangle point IDs and matching point counts")
        if (self.height_source == "manual") != (self.manual_height_m is not None):
            raise ValueError("Manual height requires an explicit value")
        if not self.floor_supported and self.height_source != "manual":
            raise ValueError("Floating furniture requires manual height")
        if self.floor_supported != (self.manual_base_height_m is None):
            raise ValueError("Floating furniture requires manual base height")
        return self


class FragmentQuality(Strict):
    observation_ids: list[Identifier] = Field(min_length=1)
    confirmed: Literal[False]


class FragmentOccluder(Strict):
    occluder_id: Identifier
    room_id: Identifier
    name: str
    kind: Literal["cuboid"]
    type: Literal["desk", "chair", "bookshelf", "other"]
    base_center_m: Vec3
    dimensions_m: tuple[Positive, Positive, Positive]
    yaw_deg_ccw_from_east: float = Field(ge=0, lt=360)
    material: Literal["opaque"]
    quality: FragmentQuality


class BoardCameraFragmentV1(Strict):
    schema_version: Literal["sunsync.fr06-fragment/1"]
    units: Literal["m"]
    coordinate_frame: Literal["ENU"]
    markers: list[FragmentMarker] = Field(min_length=1)
    observations: list[FragmentObservation] = Field(min_length=1)
    occluders: list[FragmentOccluder] = Field(min_length=1)
    windows: list = Field(max_length=0)
    protected_regions: list = Field(max_length=0)
    extensions: dict

    @model_validator(mode="after")
    def references_and_geometry(self):
        for values, key in (
            (self.markers, "marker_id"),
            (self.observations, "observation_id"),
            (self.occluders, "occluder_id"),
        ):
            ids = [getattr(v, key) for v in values]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate fragment object ID")
        markers = {m.marker_id: m for m in self.markers}
        observations = {o.observation_id: o for o in self.observations}
        occluders = {o.occluder_id: o for o in self.occluders}
        for occluder in self.occluders:
            if not set(occluder.quality.observation_ids).issubset(observations):
                raise ValueError("Unknown provenance observation")
        for observation in self.observations:
            marker = markers.get(observation.marker_id)
            if marker is None or observation.plane != marker.plane:
                raise ValueError("Unknown marker or inconsistent marker plane")
            if len(observation.points_marker_m) != len(observation.points_enu_m):
                raise ValueError("Observation point counts differ")
            t = rigid(marker.T_ENU_marker)
            expected = np.array(observation.points_marker_m) @ t[:3, :3].T + t[:3, 3]
            if not np.allclose(expected, observation.points_enu_m, atol=1e-6):
                raise ValueError("Observation points disagree with marker transform")
            if len(set(observation.derived_fields)) != len(observation.derived_fields):
                raise ValueError("Duplicate derived field")
            for field in observation.derived_fields:
                object_id, _, component = field.partition(".")
                occluder = occluders.get(object_id)
                if (
                    occluder is None
                    or observation.observation_id
                    not in occluder.quality.observation_ids
                ):
                    raise ValueError("Unknown derived field object or provenance")
                allowed = {
                    "dimensions_m[0:2]",
                    "dimensions_m[0]",
                    "dimensions_m[1]",
                    "dimensions_m[2]",
                    "base_center_m",
                    "yaw_deg_ccw_from_east",
                }
                if abs(occluder.base_center_m[2]) <= 1e-8:
                    allowed.add("base_center_m")
                if component not in allowed:
                    raise ValueError("Unsupported derived field path")
        return self
