"""FR-02 ShadingPlan: the film-cell schedule that FR-02 hands to FR-04 and FR-05.

A cell that is not listed is transparent for the whole plan, and so is any time of a
listed cell that no segment covers. Segments are half-open intervals [start, end).
"""

from __future__ import annotations

import json
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.geometry import require
from app.schemas.scene import Identifier, Timestamp

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "fr02_shading_plan_schema.json"

CellState = Literal["opaque", "transparent", "infeasible", "unknown"]
Grade = Literal["A", "B", "C", "D"]
CellIndex = Annotated[int, Field(ge=0)]
GridSize = Annotated[int, Field(ge=1)]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Reason(PlanModel):
    region_id: Identifier
    grade: Grade


class Segment(PlanModel):
    start_time: Timestamp
    end_time: Timestamp
    state: CellState
    reasons: list[Reason] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_segment(self) -> Self:
        require(
            _instant(self.start_time) < _instant(self.end_time),
            "segment end_time must be after start_time",
        )
        require(
            self.state == "transparent" or len(self.reasons) > 0,
            f"a {self.state} segment needs at least one reason",
        )
        return self


class CellPlan(PlanModel):
    row: CellIndex
    col: CellIndex
    segments: Annotated[list[Segment], Field(min_length=1)]

    @model_validator(mode="after")
    def ordered_without_overlap(self) -> Self:
        for earlier, later in pairwise(self.segments):
            require(
                _instant(earlier.end_time) <= _instant(later.start_time),
                f"cell ({self.row}, {self.col}): segments must be in time order without overlap",
            )
        return self


class WindowPlan(PlanModel):
    window_id: Identifier
    film_rows: GridSize
    film_cols: GridSize
    cells: list[CellPlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def cells_inside_grid_once(self) -> Self:
        positions = [(cell.row, cell.col) for cell in self.cells]
        require(len(set(positions)) == len(positions), f"{self.window_id}: duplicate cell")
        require(
            all(row < self.film_rows and col < self.film_cols for row, col in positions),
            f"{self.window_id}: cell outside the {self.film_rows} x {self.film_cols} film grid",
        )
        return self


class ShadingPlan(PlanModel):
    plan_version: Literal["0.1.0"]
    scene_id: Identifier
    scene_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    start_time: Timestamp
    end_time: Timestamp
    windows: list[WindowPlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_plan(self) -> Self:
        start, end = _instant(self.start_time), _instant(self.end_time)
        require(start < end, "plan end_time must be after start_time")
        window_ids = [window.window_id for window in self.windows]
        require(len(set(window_ids)) == len(window_ids), "duplicate window_id")
        require(
            all(
                start <= _instant(segment.start_time) and _instant(segment.end_time) <= end
                for window in self.windows
                for cell in window.cells
                for segment in cell.segments
            ),
            "every segment must lie inside the plan period",
        )
        return self


class ShadingPlanRequest(PlanModel):
    """Request for the plan of one stored Scene; all of its protected areas are planned."""

    start_time: Timestamp
    duration_minutes: Annotated[int, Field(ge=1, le=1440)] = 120
    interval_seconds: Annotated[int, Field(ge=1, le=3600)] = 60
    expected_scene_revision: Annotated[int, Field(ge=1, le=2**63 - 1)] | None = None


def schema_document() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **ShadingPlan.model_json_schema(),
    }


if __name__ == "__main__":
    SCHEMA_PATH.write_text(
        json.dumps(schema_document(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(SCHEMA_PATH)
