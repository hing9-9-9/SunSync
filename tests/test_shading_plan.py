"""ShadingPlan contract tests: examples, rejection rules, round trip and published schema."""

import copy
import json
from pathlib import Path

import pytest
from app.schemas.shading_plan import SCHEMA_PATH, ShadingPlan, schema_document
from jsonschema import Draft202012Validator
from pydantic import ValidationError

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr02"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.json"))


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def first_cell(plan: dict) -> dict:
    return plan["windows"][0]["cells"][0]


def end_not_after_start(plan: dict) -> None:
    segment = first_cell(plan)["segments"][0]
    segment["end_time"] = segment["start_time"]


def overlapping_segments(plan: dict) -> None:
    first_cell(plan)["segments"][1]["start_time"] = "2026-10-31T01:10:00Z"


def segments_out_of_order(plan: dict) -> None:
    first_cell(plan)["segments"].reverse()


def cell_outside_grid(plan: dict) -> None:
    first_cell(plan)["row"] = plan["windows"][0]["film_rows"]


def duplicate_cell(plan: dict) -> None:
    cells = plan["windows"][0]["cells"]
    cells.append(copy.deepcopy(cells[0]))


def duplicate_window(plan: dict) -> None:
    plan["windows"].append(copy.deepcopy(plan["windows"][0]))


def segment_outside_period(plan: dict) -> None:
    first_cell(plan)["segments"][0]["start_time"] = "2026-10-31T00:30:00Z"


def opaque_without_reason(plan: dict) -> None:
    first_cell(plan)["segments"][0]["reasons"] = []


def unknown_field(plan: dict) -> None:
    plan["sample_index"] = 17


def naive_timestamp(plan: dict) -> None:
    plan["start_time"] = "2026-10-31T01:00:00"


def number_as_text(plan: dict) -> None:
    plan["scene_revision"] = "2"


@pytest.mark.parametrize("file", EXAMPLE_FILES, ids=lambda path: path.name)
def test_examples_match_model_and_published_schema(file):
    data = json.loads(file.read_text(encoding="utf-8"))
    Draft202012Validator(schema_document()).validate(data)
    ShadingPlan.model_validate(data)


REJECTED = [
    pytest.param(function, message, id=function.__name__)
    for function, message in [
        (end_not_after_start, "end_time must be after start_time"),
        (overlapping_segments, "time order without overlap"),
        (segments_out_of_order, "time order without overlap"),
        (cell_outside_grid, "outside the 15 x 30 film grid"),
        (duplicate_cell, "duplicate cell"),
        (duplicate_window, "duplicate window_id"),
        (segment_outside_period, "inside the plan period"),
        (opaque_without_reason, "needs at least one reason"),
        (unknown_field, "Extra inputs are not permitted"),
        (naive_timestamp, "should match pattern"),
        (number_as_text, "valid integer"),
    ]
]


@pytest.mark.parametrize(("mutate", "message"), REJECTED)
def test_invalid_plans_are_rejected_for_the_right_reason(mutate, message):
    data = load("shading_plan_grade_a.json")
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        ShadingPlan.model_validate(data)


@pytest.mark.parametrize("file", EXAMPLE_FILES, ids=lambda path: path.name)
def test_round_trip_keeps_every_value(file):
    data = json.loads(file.read_text(encoding="utf-8"))
    plan = ShadingPlan.model_validate(data)
    assert plan.model_dump(mode="json") == data
    assert ShadingPlan.model_validate_json(plan.model_dump_json()) == plan


def test_checked_in_schema_matches_model():
    published = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert published == schema_document()
    Draft202012Validator.check_schema(published)
