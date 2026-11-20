"""FR-05 Pydantic schemas for the Virtual Film Device, Commands, and Multi-user Pairing."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.schemas.scene import Dimensions, Identifier, Number, Timestamp, Vector
from app.schemas.shading_plan import Reason, ShadingPlan, ShadingPlanRequest

FilmType = Literal["PDLC", "SPD"]
AppliedCellState = Literal["transparent", "opaque", "dimmed"]
LogStatus = Literal["applied", "target_unmet", "unknown_intensity"]


def _validate_cell_opacity(v: Any) -> float:
    if isinstance(v, bool):
        raise TypeError("opacity cannot be a boolean")
    if not isinstance(v, (int, float)):
        raise TypeError("opacity must be a numeric value between 0.0 and 1.0")
    val = float(v)
    if math.isnan(val) or math.isinf(val):
        raise ValueError("opacity cannot be NaN or Inf")
    if not (0.0 <= val <= 1.0):
        raise ValueError("opacity must be between 0.0 and 1.0")
    return val


CellOpacity = Annotated[float, BeforeValidator(_validate_cell_opacity)]


class FilmBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CellCommand(FilmBaseModel):
    row: Annotated[int, Field(ge=0)]
    col: Annotated[int, Field(ge=0)]
    opacity: CellOpacity | None = None
    state: Literal["transparent", "opaque", "dimmed"] | None = None

    @model_validator(mode="after")
    def resolve_and_validate(self) -> Self:
        if self.opacity is None and self.state is None:
            raise ValueError("at least one of opacity or state must be provided")
        if self.state == "transparent":
            if self.opacity is None:
                self.opacity = 0.0
            elif abs(self.opacity - 0.0) > 1e-6:
                raise ValueError("state 'transparent' requires opacity 0.0")
        elif self.state == "opaque":
            if self.opacity is None:
                self.opacity = 1.0
            elif abs(self.opacity - 1.0) > 1e-6:
                raise ValueError("state 'opaque' requires opacity 1.0")
        elif self.state == "dimmed":
            if self.opacity is None:
                raise ValueError("state 'dimmed' requires explicit opacity between 0.0 and 1.0")
            if not (1e-6 < self.opacity < 1.0 - 1e-6):
                raise ValueError("state 'dimmed' requires opacity strictly between 0.0 and 1.0")
        return self


class WindowFilmCommandRequest(FilmBaseModel):
    window_id: Identifier | None = None
    film_type: FilmType | None = None
    commands: Annotated[list[CellCommand], Field(min_length=1)]
    timestamp: Timestamp | None = None
    plan_id: str | None = None
    note: str = ""


class MarkerModeRequest(FilmBaseModel):
    window_id: Identifier | None = None
    enabled: bool
    code_id: Annotated[int, Field(ge=0, lt=50)] = 0
    start_row: Annotated[int, Field(ge=0)] = 2
    start_col: Annotated[int, Field(ge=0)] = 2
    module_row: Annotated[int, Field(ge=0)] | None = None
    module_col: Annotated[int, Field(ge=0)] | None = None
    timestamp: Timestamp | None = None

    @model_validator(mode="after")
    def module_target(self) -> Self:
        if (self.module_row is None) != (self.module_col is None):
            raise ValueError("module_row and module_col must be supplied together")
        if self.module_row is not None and (
            "start_row" in self.model_fields_set or "start_col" in self.model_fields_set
        ):
            raise ValueError("choose a film module or raw cell coordinates, not both")
        return self


class ScheduleApplyRequest(FilmBaseModel):
    plan: ShadingPlan | None = None
    plan_request: ShadingPlanRequest | None = None
    plan_id: str | None = None
    at: Timestamp | None = None

    @model_validator(mode="after")
    def require_plan_source(self) -> Self:
        if self.plan is None and self.plan_request is None:
            raise ValueError("either plan or plan_request must be provided")
        return self


class MeasuredFurnitureInput(FilmBaseModel):
    occluder_id: Identifier
    name: str
    type: Literal["bookshelf", "desk", "chair", "other"] = "other"
    base_center_m: Vector
    dimensions_m: Dimensions
    yaw_deg_ccw_from_east: Number = 0.0


class MarkerPairingRequest(FilmBaseModel):
    code_id: Annotated[int, Field(ge=0, lt=50)] = 0
    window_id: Identifier | None = None
    module_row: Annotated[int, Field(ge=0)] = 0
    module_col: Annotated[int, Field(ge=0)] = 0
    auto_provision_window: bool = True
    enable_marker_display: bool = True
    clamp_furniture_to_room: bool = True
    furniture: list[MeasuredFurnitureInput] = Field(default_factory=list)


class WindowFilmStateResponse(FilmBaseModel):
    scene_id: str
    window_id: str
    film_type: FilmType
    film_rows: int
    film_cols: int
    cells: list[list[float]]
    states: list[list[AppliedCellState]]
    marker_mode_active: bool
    marker_code_id: int | None = None
    active_plan_id: str | None = None
    evaluated_at: str
    updated_at: str


class SceneFilmStateResponse(FilmBaseModel):
    scene_id: str
    evaluated_at: str
    windows: list[WindowFilmStateResponse]


class FilmApplicationLogEntry(FilmBaseModel):
    log_id: int | None = None
    scene_id: str
    window_id: str
    timestamp: str
    row: int
    col: int
    previous_opacity: float
    new_opacity: float
    previous_state: AppliedCellState
    new_state: AppliedCellState
    plan_id: str | None = None
    status: LogStatus
    note: str
    reasons: list[Reason] = Field(default_factory=list)
    created_at: str


class FilmApplicationLogListResponse(FilmBaseModel):
    scene_id: str
    total: int
    logs: list[FilmApplicationLogEntry]


class ScheduleApplyResponse(FilmBaseModel):
    scene_id: str
    plan_id: str
    applied_records_count: int
    target_unmet_count: int
    evaluated_at: str
    windows: list[WindowFilmStateResponse]


class MarkerPairingResponse(FilmBaseModel):
    scene_id: str
    scene_revision: int
    window_id: str
    marker_id: str
    code_id: int
    clamped_furniture_ids: list[str]
    film_state: WindowFilmStateResponse
