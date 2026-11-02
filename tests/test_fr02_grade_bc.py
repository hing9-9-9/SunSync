"""FR-02.2 grade B and C tests: shade only when the sunlight is stronger than the grade allows."""

from datetime import UTC, datetime, timedelta

import pytest
from app.schemas.shading_plan import ShadingPlan

from engine.fr02 import (
    CellCandidate,
    CellCause,
    FilmWindow,
    build_plan,
    grade_bc_needs,
)

START = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 31, 11, 0, tzinfo=UTC)
STEP_SECONDS = 300
GRADES = {"region_A": "A", "region_B": "B", "region_B2": "B", "region_C": "C", "region_D": "D"}
CAPS = {"B": 20.0, "C": 80.0}


def window(film_min: float = 0.0) -> FilmWindow:
    return FilmWindow("window_001", (0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 1.0, 10, 10, film_min)


def at(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def iso(minutes: int) -> str:
    return at(minutes).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate(minutes: int, *causes: tuple[str, float | None]) -> CellCandidate:
    found = tuple(CellCause(region, index, value) for index, (region, value) in enumerate(causes))
    return CellCandidate(at(minutes), "window_001", 3, 4, found)


def build(candidates, film_min: float = 0.0) -> dict:
    windows = [window(film_min)]
    common = {
        "windows": windows,
        "candidates": candidates,
        "grades": GRADES,
        "start_time": START,
        "end_time": END,
        "interval_seconds": STEP_SECONDS,
    }
    needs = grade_bc_needs(caps=CAPS, **common)
    plan = build_plan(
        scene_id="scene_demo_001",
        scene_revision=2,
        windows=windows,
        needs=needs,
        start_time=START,
        end_time=END,
    )
    ShadingPlan.model_validate(plan)
    return plan


def segments(plan: dict) -> list[dict]:
    cells = plan["windows"][0]["cells"]
    return [] if not cells else cells[0]["segments"]


def summary(plan: dict) -> list[tuple[str, str, str]]:
    return [(s["start_time"], s["end_time"], s["state"]) for s in segments(plan)]


def test_intensity_at_or_below_the_cap_needs_nothing():
    assert segments(build([candidate(20, ("region_B", 15.0))])) == []
    assert segments(build([candidate(20, ("region_B", 20.0))])) == []


def test_intensity_above_the_cap_shades_the_step_before_and_after():
    plan = build([candidate(20, ("region_B", 25.0))])
    assert summary(plan) == [(iso(15), iso(25), "opaque")]
    assert segments(plan)[0]["reasons"] == [{"region_id": "region_B", "grade": "B"}]


def test_grade_c_has_a_higher_cap_than_grade_b():
    plan = build([candidate(20, ("region_B", 50.0), ("region_C", 50.0))])
    assert [r["region_id"] for r in segments(plan)[0]["reasons"]] == ["region_B"]


def test_the_strongest_tile_of_a_region_decides():
    plan = build([candidate(20, ("region_B", 15.0), ("region_B", 25.0))])
    assert summary(plan) == [(iso(15), iso(25), "opaque")]


def test_the_film_at_its_darkest_decides_between_opaque_and_infeasible():
    # cap 20, film lets 10 % through: 100 -> 10 meets the cap, 480 -> 48 does not
    assert summary(build([candidate(20, ("region_B", 100.0))], film_min=0.1))[0][2] == "opaque"
    assert summary(build([candidate(20, ("region_B", 480.0))], film_min=0.1))[0][2] == "infeasible"


def test_a_film_that_blocks_fully_is_never_infeasible():
    assert summary(build([candidate(20, ("region_B", 5000.0))], film_min=0.0))[0][2] == "opaque"


def test_an_unknown_intensity_is_unknown_not_zero():
    assert summary(build([candidate(20, ("region_B", None))])) == [(iso(15), iso(25), "unknown")]


def test_a_known_excess_wins_over_an_unknown_tile():
    plan = build([candidate(20, ("region_B", 25.0), ("region_B", None))])
    assert summary(plan) == [(iso(15), iso(25), "opaque")]


def test_a_known_value_below_the_cap_does_not_hide_an_unknown_tile():
    plan = build([candidate(20, ("region_B", 15.0), ("region_B", None))])
    assert summary(plan) == [(iso(15), iso(25), "unknown")]


def test_the_plan_follows_the_intensity_over_time():
    plan = build(
        [
            candidate(20, ("region_B", 15.0)),
            candidate(25, ("region_B", 100.0)),
            candidate(30, ("region_B", 15.0)),
        ]
    )
    assert summary(plan) == [(iso(20), iso(30), "opaque")]


def test_regions_of_grade_a_and_d_are_not_decided_here():
    plan = build([candidate(20, ("region_A", 900.0), ("region_D", 900.0))])
    assert segments(plan) == []


def test_two_regions_in_one_cell_give_the_most_severe_state_and_both_reasons():
    plan = build([candidate(20, ("region_B", 480.0), ("region_C", 100.0))], film_min=0.1)
    only = segments(plan)
    assert [s["state"] for s in only] == ["infeasible"]
    assert [r["region_id"] for r in only[0]["reasons"]] == ["region_B", "region_C"]


def test_unknown_region_is_rejected():
    with pytest.raises(ValueError, match="unknown protected region"):
        build([candidate(20, ("region_Z", 50.0))])


def test_a_missing_cap_is_rejected():
    with pytest.raises(ValueError, match="no intensity cap for grade C"):
        grade_bc_needs(
            windows=[window()],
            candidates=[],
            grades=GRADES,
            caps={"B": 20.0},
            start_time=START,
            end_time=END,
            interval_seconds=STEP_SECONDS,
        )
