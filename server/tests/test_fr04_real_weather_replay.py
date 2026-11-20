"""FR-04-2 replay tool: day windows, the DSR to DNI records, the step-grid comparison and a short run."""

import argparse
import csv
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tools.ai_experiments import run_fr04_real_weather_replay as replay

TEMPLATE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "fr06_scene_example.json"
HEADER = [
    "timestamp_utc",
    "requested_latitude",
    "requested_longitude",
    "returned_latitude",
    "returned_longitude",
    "dsr_raw",
    "ca_raw_provider",
    "ca_fraction",
    "cloud_cover_pct",
    "dsr_unit",
    "unit_confirmed",
    "quality_flag",
]


def write_csv(path: Path, start: datetime, count: int, dsr: dict[int, str] | None = None) -> Path:
    """A KMA-shaped file: one row every 10 minutes; `dsr` overrides single rows by index."""
    dsr = dsr or {}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for index in range(count):
            moment = start + timedelta(minutes=10 * index)
            writer.writerow(
                [
                    moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    37.5714,
                    126.9658,
                    37.5714,
                    126.9658,
                    dsr.get(index, "400.0"),
                    0.1,
                    0.1,
                    10.0,
                    "W/m2",
                    "true",
                    "ok",
                ]
            )
    return path


MORNING = datetime(2026, 10, 9, 23, 0, tzinfo=UTC)  # 08:00 Korean time on 2026-10-10
SITE = replay.Site(37.5714, 126.9658, "Asia/Seoul")


@pytest.mark.parametrize("text", ["2026-11-02", "2026-11-04", "2026-11-05"])
def test_the_frozen_final_test_days_are_refused(text):
    with pytest.raises(ValueError, match="frozen"):
        replay.parse_day(text)
    with pytest.raises(argparse.ArgumentTypeError):
        replay._day_argument(f"clear={text}")


def test_days_next_to_the_frozen_period_are_accepted():
    assert replay.parse_day("2026-11-01") == date(2026, 11, 1)
    assert replay.parse_day("2026-11-06") == date(2026, 11, 6)


def test_the_day_window_is_korean_time_in_utc():
    start, end = replay.day_window(date(2026, 10, 10), (8, 0), (16, 50))
    assert start == datetime(2026, 10, 9, 23, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 10, 7, 50, tzinfo=UTC)
    assert replay.parse_clock("16:50") == (16, 50)
    with pytest.raises(ValueError, match="time of day"):
        replay.parse_clock("25:00")


def test_rows_are_read_inside_the_window_only_and_a_blank_dsr_is_missing(tmp_path):
    path = write_csv(tmp_path / "kma.csv", MORNING - timedelta(minutes=20), 12, {4: ""})
    rows = replay.read_kma_rows(path, MORNING, MORNING + timedelta(minutes=50))
    assert [row.timestamp for row in rows] == [
        MORNING + timedelta(minutes=10 * i) for i in range(6)
    ]
    assert [row.dsr_W_m2 is None for row in rows] == [False, False, True, False, False, False]
    assert rows[0].cloud_cover_pct == 10.0


def test_the_statistics_count_cloud_flips_and_dsr_jumps():
    start = MORNING
    values = [(500, 0), (100, 100), (500, 0), (480, 10)]
    rows = [
        replay.KmaRow(start + timedelta(minutes=10 * i), dsr, cloud)
        for i, (dsr, cloud) in enumerate(values)
    ]
    stat = replay.day_statistics(rows)
    assert stat["cloud_cover_flips"] == 2
    assert stat["dsr_jumps_150"] == 2
    assert stat["mean_cloud_cover_pct"] == 27.5


def test_a_row_without_dsr_is_left_out_and_counted(tmp_path):
    path = write_csv(tmp_path / "kma.csv", MORNING, 7, {3: ""})
    rows = replay.read_kma_rows(path, MORNING, MORNING + timedelta(minutes=60))
    records, counts = replay.to_dni_records(rows, SITE)
    assert counts == {"records": 6, "rows_without_dsr": 1}
    assert all(record["dni_W_m2"] >= 0 for record in records)
    assert records[0]["valid_time"] == "2026-10-09T23:00:00Z"


@pytest.mark.parametrize("missing_index", [0, 6])
def test_a_missing_first_or_last_row_is_refused(tmp_path, missing_index):
    path = write_csv(tmp_path / "kma.csv", MORNING, 7, {missing_index: ""})
    rows = replay.read_kma_rows(path, MORNING, MORNING + timedelta(minutes=60))
    with pytest.raises(ValueError, match="no DSR value"):
        replay.to_dni_records(rows, SITE)


def test_the_scene_gets_the_weather_and_a_consistent_scenario():
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    records = [
        {
            "valid_time": "2026-10-09T23:00:00Z",
            "interval_start": None,
            "interval_end": None,
            "dni_W_m2": 1.0,
        },
        {
            "valid_time": "2026-10-09T23:10:00Z",
            "interval_start": None,
            "interval_end": None,
            "dni_W_m2": 2.0,
        },
    ]
    scene = replay.build_scene(
        template, "scene_x", SITE, records, datetime(2026, 11, 18, tzinfo=UTC)
    )
    assert scene["scene_id"] == "scene_x"
    assert scene["site"]["latitude_deg"] == 37.5714
    assert scene["weather"]["records"] == records
    assert scene["weather"]["temporal_meaning"] == "instant"
    assert scene["scenario"]["weather_id"] == scene["weather"]["weather_id"]
    assert scene["scenario"]["start_time"] == "2026-10-09T23:00:00Z"
    assert scene["scenario"]["end_time"] == "2026-10-09T23:10:00Z"
    assert template["weather"]["records"] != records  # the template itself is not changed


def plan(*cells):
    """A plan with one window; each cell is (row, col, [(start, end), ...]) as ISO strings."""
    return {
        "windows": [
            {
                "window_id": "w",
                "cells": [
                    {
                        "row": row,
                        "col": col,
                        "segments": [{"start_time": a, "end_time": b} for a, b in spans],
                    }
                    for row, col, spans in cells
                ],
            }
        ]
    }


def test_plans_are_compared_on_the_step_grid_not_by_their_own_start_times():
    start = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)
    grid = replay.step_grid(start, 60, 1800)  # 07:00 and 07:30
    # FR-02 starts the segment at the exact entry time 07:20, FR-04 on the step at 07:30.
    baseline = plan((4, 9, [("2026-10-10T07:20:00+00:00", "2026-10-10T07:50:00+00:00")]))
    smoothed = plan((4, 9, [("2026-10-10T07:30:00+00:00", "2026-10-10T07:50:00+00:00")]))
    assert replay.never_brighter({"baseline_plan": baseline, "stabilized_plan": smoothed}, grid)


def test_a_cell_step_shaded_by_fr02_but_not_by_fr04_is_found():
    start = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)
    grid = replay.step_grid(start, 60, 1800)
    baseline = plan((0, 0, [("2026-10-10T07:00:00+00:00", "2026-10-10T08:00:00+00:00")]))
    smoothed = plan((0, 0, [("2026-10-10T07:00:00+00:00", "2026-10-10T07:30:00+00:00")]))
    assert not replay.never_brighter({"baseline_plan": baseline, "stabilized_plan": smoothed}, grid)


def test_the_change_is_signed_and_undefined_without_a_baseline():
    assert replay._change_pct(10, 6) == 40.0
    assert replay._change_pct(10, 12) == -20.0
    assert replay._change_pct(0, 0) is None


def test_the_metrics_of_a_response_are_copied_and_signed():
    response = {
        "baseline_plan": plan(),
        "stabilized_plan": plan(),
        "metrics": {
            "baseline_switch_count": 20,
            "adaptive_switch_count": 15,
            "baseline_total_change": 20.0,
            "adaptive_total_change": 15.0,
            "total_change_reduction_pct": 25.0,
            "baseline_mean_opacity": 0.2,
            "adaptive_mean_opacity": 0.21,
            "daylight_gain_pct": -5.0,
            "baseline_violation_count": 3,
            "protection_violation_count": 0,
            "baseline_violation_count_q90": 4,
            "protection_violation_count_q90": 1,
        },
    }
    result = replay.case_result(response, 300, [])
    assert result["switch_change_pct"] == 25.0
    assert result["daylight_gain_pct"] == -5.0
    assert (result["fr02_above_cap_q50"], result["fr04_above_cap_q50"]) == (3, 0)
    assert (result["fr02_above_cap_q90"], result["fr04_above_cap_q90"]) == (4, 1)
    assert result["never_brighter_than_fr02"] is True


def test_the_report_shows_signs_the_failed_check_and_the_limits():
    case = {
        "interval_seconds": 1800,
        "fr02_switches": 10,
        "fr04_switches": 12,
        "switch_change_pct": -20.0,
        "fr02_total_change": 10.0,
        "fr04_total_change": 12.0,
        "total_change_reduction_pct": -20.0,
        "daylight_gain_pct": 0.0,
        "fr02_above_cap_q50": 0,
        "fr04_above_cap_q50": 0,
        "fr02_above_cap_q90": 0,
        "fr04_above_cap_q90": 0,
        "never_brighter_than_fr02": False,
    }
    result = {
        "label": "clear",
        "date_kst": "2026-10-10",
        "statistics": {
            "mean_cloud_cover_pct": 1.5,
            "mean_dsr_W_m2": 500.0,
            "cloud_cover_flips": 0,
            "dsr_jumps_150": 0,
        },
        "weather_records": {"records": 5, "rows_without_dsr": 1},
        "cases": [case],
    }
    controls = [{"dni_W_m2": 0.0, "fr02_switches": 0, "fr02_shaded_cell_steps": 0}]
    report = replay.build_report([result], "kma.csv", "abc123", controls)
    assert "-20.0%" in report
    assert "| NO |" in report
    assert "abc123" in report
    assert "perfect forecast" in report
    assert "| 0 W/m2 | 0 | 0 |" in report


def test_a_short_replay_runs_through_the_application(tmp_path):
    path = write_csv(tmp_path / "kma.csv", MORNING, 13)  # 08:00 to 10:00 Korean time
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    results = replay.replay(
        path,
        [replay.ReplayDay("clear", date(2026, 10, 10))],
        (1800,),
        template,
        (8, 0),
        (10, 0),
    )
    assert [r["label"] for r in results] == ["clear"]
    case = results[0]["cases"][0]
    assert case["interval_seconds"] == 1800
    assert case["never_brighter_than_fr02"] is True
    assert results[0]["weather_records"] == {"records": 13, "rows_without_dsr": 0}
    controls = replay.constant_sky_controls(path, date(2026, 10, 10), template, (8, 0), (10, 0))
    assert [c["dni_W_m2"] for c in controls] == list(replay.CONTROL_DNI_W_M2)
    assert controls[0]["fr02_shaded_cell_steps"] == 0  # no sun, no shading
    shaded = [c["fr02_shaded_cell_steps"] for c in controls]
    assert shaded == sorted(shaded)  # a stronger sky never shades less
