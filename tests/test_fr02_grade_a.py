"""FR-02.2 grade A tests: every needed cell is shaded, whatever the intensity."""

from datetime import UTC, datetime, timedelta

import pytest
from app.schemas.shading_plan import ShadingPlan

from engine.fr02 import (
    CellCandidate,
    CellCause,
    ExposedTile,
    FilmWindow,
    build_grade_a_plan,
    map_tiles_to_cells,
)

START = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 31, 11, 0, tzinfo=UTC)
STEP_SECONDS = 300
GRADES = {"region_A": "A", "region_A2": "A", "region_B": "B", "region_C": "C"}


def window() -> FilmWindow:
    return FilmWindow("window_001", (0, 0, 0), (1, 0, 0), (0, 0, 1), 1.0, 1.0, 10, 10)


def at(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def iso(minutes: int) -> str:
    return at(minutes).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate(minutes: int, *regions: str, row: int = 3, col: int = 4) -> CellCandidate:
    causes = tuple(CellCause(region, index) for index, region in enumerate(regions))
    return CellCandidate(at(minutes), "window_001", row, col, causes)


def build(candidates) -> dict:
    plan = build_grade_a_plan(
        scene_id="scene_demo_001",
        scene_revision=2,
        windows=[window()],
        candidates=candidates,
        grades=GRADES,
        start_time=START,
        end_time=END,
        interval_seconds=STEP_SECONDS,
    )
    ShadingPlan.model_validate(plan)
    return plan


def spans(plan: dict, row: int = 3, col: int = 4) -> list[tuple[str, str]]:
    cells = plan["windows"][0]["cells"]
    cell = next((c for c in cells if (c["row"], c["col"]) == (row, col)), None)
    return [] if cell is None else [(s["start_time"], s["end_time"]) for s in cell["segments"]]


def test_no_exposure_gives_a_plan_without_cells():
    plan = build([])
    assert plan["windows"][0]["cells"] == []
    assert plan["windows"][0]["film_rows"] == 10


def test_one_lit_step_shades_the_step_before_and_after():
    assert spans(build([candidate(20, "region_A")])) == [(iso(15), iso(25))]


def test_consecutive_steps_join_into_one_interval():
    plan = build([candidate(minute, "region_A") for minute in (20, 25, 30)])
    assert spans(plan) == [(iso(15), iso(35))]


def test_separate_exposures_stay_separate():
    plan = build([candidate(15, "region_A"), candidate(45, "region_A")])
    assert spans(plan) == [(iso(10), iso(20)), (iso(40), iso(50))]


def test_shading_is_cut_at_the_plan_period():
    plan = build([candidate(0, "region_A"), candidate(60, "region_A")])
    assert spans(plan) == [(iso(0), iso(5)), (iso(55), iso(60))]


def test_every_segment_is_opaque_with_the_grade_a_region_as_reason():
    segment = build([candidate(20, "region_A")])["windows"][0]["cells"][0]["segments"][0]
    assert segment["state"] == "opaque"
    assert segment["reasons"] == [{"region_id": "region_A", "grade": "A"}]


def test_reasons_follow_the_regions_over_time():
    plan = build([candidate(20, "region_A"), candidate(25, "region_A2")])
    segments = plan["windows"][0]["cells"][0]["segments"]
    assert [(s["start_time"], s["end_time"]) for s in segments] == [
        (iso(15), iso(20)),
        (iso(20), iso(25)),
        (iso(25), iso(30)),
    ]
    assert [[r["region_id"] for r in s["reasons"]] for s in segments] == [
        ["region_A"],
        ["region_A", "region_A2"],
        ["region_A2"],
    ]


def test_a_region_seen_by_several_tiles_is_one_reason():
    plan = build([candidate(20, "region_A", "region_A")])
    assert len(plan["windows"][0]["cells"][0]["segments"][0]["reasons"]) == 1


def test_other_grades_are_left_to_the_grade_b_and_c_decision():
    assert (
        build([candidate(20, "region_B"), candidate(25, "region_C")])["windows"][0]["cells"] == []
    )


def test_a_cell_needed_by_grade_a_and_c_is_shaded_for_the_grade_a_region_only():
    plan = build([candidate(20, "region_A", "region_C")])
    segment = plan["windows"][0]["cells"][0]["segments"][0]
    assert [r["region_id"] for r in segment["reasons"]] == ["region_A"]


@pytest.mark.parametrize("irradiance", [0.0, None, 500.0])
def test_intensity_does_not_change_the_plan(irradiance):
    tile = ExposedTile(at(20), "region_A", 0, "window_001", (0.42, 0.0, 0.63), irradiance)
    plan = build(map_tiles_to_cells([tile], [window()]))
    assert spans(plan, 3, 3) == [(iso(15), iso(25))]
    assert spans(plan, 3, 4) == [(iso(15), iso(25))]


def test_unknown_region_is_rejected():
    with pytest.raises(ValueError, match="unknown protected region"):
        build([candidate(20, "region_Z")])


def test_unknown_window_is_rejected():
    stray = CellCandidate(at(20), "other_window", 0, 0, (CellCause("region_A", 0),))
    with pytest.raises(ValueError, match="unknown window"):
        build([stray])
