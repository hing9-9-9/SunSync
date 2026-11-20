"""Plan assembly tests: overlapping needs on one cell become one ordered, consistent plan."""

from datetime import UTC, datetime, timedelta

import pytest
from app.schemas.shading_plan import ShadingPlan

from engine.fr02 import FilmWindow, Need, build_plan

START = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 31, 11, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def iso(minutes: int) -> str:
    return at(minutes).strftime("%Y-%m-%dT%H:%M:%SZ")


def window() -> FilmWindow:
    return FilmWindow("window_001", (0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 1.0, 10, 10, 0.0)


def need(start: int, end: int, state: str, region: str, grade: str) -> Need:
    return Need("window_001", 3, 4, at(start), at(end), state, region, grade)


def build(needs) -> dict:
    plan = build_plan(
        scene_id="scene_demo_001",
        scene_revision=2,
        windows=[window()],
        needs=needs,
        start_time=START,
        end_time=END,
    )
    ShadingPlan.model_validate(plan)
    return plan


def segments(plan: dict) -> list[dict]:
    cells = plan["windows"][0]["cells"]
    return [] if not cells else cells[0]["segments"]


def test_no_needs_give_a_plan_without_cells():
    assert segments(build([])) == []


def test_overlapping_needs_of_one_state_and_region_join():
    plan = build([need(15, 25, "opaque", "region_B", "B"), need(20, 30, "opaque", "region_B", "B")])
    assert [(s["start_time"], s["end_time"]) for s in segments(plan)] == [(iso(15), iso(30))]


def test_pieces_split_where_the_reasons_change():
    plan = build([need(10, 20, "opaque", "region_B", "B"), need(15, 25, "opaque", "region_C", "C")])
    assert [(s["start_time"], s["end_time"]) for s in segments(plan)] == [
        (iso(10), iso(15)),
        (iso(15), iso(20)),
        (iso(20), iso(25)),
    ]
    assert [[r["region_id"] for r in s["reasons"]] for s in segments(plan)] == [
        ["region_B"],
        ["region_B", "region_C"],
        ["region_C"],
    ]


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (("opaque", "infeasible"), "infeasible"),
        (("unknown", "opaque"), "opaque"),
        (("unknown", "infeasible"), "infeasible"),
    ],
    ids=["infeasible_over_opaque", "opaque_over_unknown", "infeasible_over_unknown"],
)
@pytest.mark.parametrize("stricter_first", [True, False], ids=["stricter_first", "looser_first"])
def test_the_most_severe_state_wins_whichever_grade_has_it(states, expected, stricter_first):
    first, second = states if stricter_first else states[::-1]
    plan = build([need(10, 20, first, "region_B", "B"), need(10, 20, second, "region_C", "C")])
    assert [s["state"] for s in segments(plan)] == [expected]


def test_a_stricter_unknown_does_not_hide_a_looser_known_need():
    plan = build(
        [need(10, 20, "unknown", "region_B", "B"), need(10, 20, "opaque", "region_C", "C")]
    )
    only = segments(plan)
    assert [s["state"] for s in only] == ["opaque"]
    assert [r["region_id"] for r in only[0]["reasons"]] == ["region_B", "region_C"]


def test_a_stricter_opaque_does_not_hide_a_looser_unmet_need():
    plan = build(
        [need(10, 20, "opaque", "region_B", "B"), need(10, 20, "infeasible", "region_C", "C")]
    )
    assert [s["state"] for s in segments(plan)] == ["infeasible"]


def test_reasons_are_listed_strictest_grade_first():
    plan = build([need(10, 20, "opaque", "region_C", "C"), need(10, 20, "opaque", "region_A", "A")])
    assert [(r["grade"], r["region_id"]) for r in segments(plan)[0]["reasons"]] == [
        ("A", "region_A"),
        ("C", "region_C"),
    ]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"state": "transparent"}, "state must be one of"),
        ({"grade": "E"}, "grade must be one of"),
        ({"end_time": at(10)}, "must end after it starts"),
    ],
    ids=["unknown_state", "unknown_grade", "empty_period"],
)
def test_a_need_must_be_valid(changes, message):
    values = {
        "window_id": "window_001",
        "row": 3,
        "col": 4,
        "start_time": at(10),
        "end_time": at(20),
        "state": "opaque",
        "region_id": "region_A",
        "grade": "A",
    } | changes
    with pytest.raises(ValueError, match=message):
        Need(**values)


def test_a_need_for_an_unknown_window_is_rejected():
    stray = Need("other_window", 0, 0, at(10), at(20), "opaque", "region_A", "A")
    with pytest.raises(ValueError, match="unknown window"):
        build([stray])
