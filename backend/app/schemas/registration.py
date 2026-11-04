"""Transport envelope for adding measured furniture to an existing FR-06 scene."""

from typing import Annotated, Any, Literal

from pydantic import Field

from app.schemas.scene import BoardCameraObservation, Marker, Model, Occluder


class RegistrationFragment(Model):
    schema_version: Literal["sunsync.fr06-fragment/1"]
    units: Literal["m"]
    coordinate_frame: Literal["ENU"]
    markers: Annotated[list[Marker], Field(min_length=1, max_length=100)]
    observations: Annotated[list[BoardCameraObservation], Field(min_length=1, max_length=100)]
    occluders: Annotated[list[Occluder], Field(min_length=1, max_length=100)]
    windows: Annotated[list[Any], Field(max_length=0)]
    protected_regions: Annotated[list[Any], Field(max_length=0)]
    extensions: dict[str, Any]


class RegisterFurnitureRequest(Model):
    expected_revision: Annotated[int, Field(gt=0)]
    fragment: RegistrationFragment
