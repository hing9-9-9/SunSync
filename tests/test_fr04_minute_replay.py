"""FR-04-2 replay of measured 1-minute irradiance: file reading, solar-time mapping, chunks, merging."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tools.ai_experiments import run_fr04_minute_replay as minute

TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "fr06_scene_example.json"
LONGITUDE = -77.93  # Penn State; solar time is 312 minutes behind UTC


def write_surfrad(path: Path, rows: list[tuple[int, str, int]]) -> Path:
    """A SURFRAD-shaped daily file; each row is (UTC minute of the day, DNI text, quality flag)."""
    lines = [" Penn State", f"   40.72  {LONGITUDE}  376 m version 1"]
    for utc_minute, dni, quality in rows:
        hour, mins = divmod(utc_minute, 60)
        lines.append(
            f" 2023 284 10 11 {hour:2d} {mins:2d} 0.000 50.00 "
            f"500.0 0 50.0 0 {dni} {quality} 100.0 0 280.0 0"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_file_names_and_addresses_follow_the_noaa_layout():
    assert minute.doy_name("psu", date(2023, 10, 11)) == "psu23284.dat"
    assert minute.surfrad_url("Penn_State_PA", "psu", date(2023, 10, 11)).endswith(
        "/surfrad/Penn_State_PA/2023/psu23284.dat"
    )


def test_a_day_file_gives_the_station_and_only_good_dni_values(tmp_path):
    path = write_surfrad(
        tmp_path / "psu23284.dat",
        [(0, "-0.3", 0), (1, "412.5", 0), (2, "-9999.9", 1), (3, "300.0", 1), (4, "650.0", 0)],
    )
    station, rows = minute.read_surfrad(path)
    assert (station.latitude, station.longitude) == (40.72, LONGITUDE)
    # night offsets become 0; the missing marker and a flagged value are missing
    assert rows == [(0, 0.0), (1, 412.5), (2, None), (3, None), (4, 650.0)]
    assert minute.solar_minutes(station) == -312


def test_a_station_minute_is_placed_at_the_same_solar_time_in_seoul(tmp_path):
    # 13:12 UTC is 08:00 solar time at Penn State; 08:00 solar time in Seoul on 2026-10-11 is
    # 23:32 UTC the day before (Seoul's solar time is 508 minutes ahead of UTC).
    rows = [(792 + i, 500.0 + i) for i in range(11)]
    path = write_surfrad(tmp_path / "f.dat", [(m, f"{v}", 0) for m, v in rows])
    station, parsed = minute.read_surfrad(path)
    records, values, start, end, counts = minute.to_window(
        parsed, station, date(2026, 10, 11), (8, 0), (8, 10)
    )
    assert records[0]["valid_time"] == "2026-10-10T23:32:00Z"
    assert records[-1]["valid_time"] == "2026-10-10T23:42:00Z"
    assert (start, end) == (
        datetime(2026, 10, 10, 23, 32, tzinfo=UTC),
        datetime(2026, 10, 10, 23, 42, tzinfo=UTC),
    )
    assert values[:2] == [500.0, 501.0]
    assert counts == {"records": 11, "minutes_without_dni": 0}


def test_the_window_shrinks_to_the_first_and_last_valid_minute(tmp_path):
    station = minute.Station(40.72, LONGITUDE)
    rows: list[tuple[int, float | None]] = [(792 + i, 400.0) for i in range(10)]
    rows[0], rows[1], rows[4], rows[9] = (792, None), (793, None), (796, None), (801, None)
    records, _values, start, end, counts = minute.to_window(
        rows, station, date(2026, 10, 11), (8, 0), (8, 9)
    )
    assert records[0]["valid_time"] == "2026-10-10T23:34:00Z"  # the first two minutes are gone
    assert end == datetime(2026, 10, 10, 23, 40, tzinfo=UTC)  # the last minute is gone
    assert start == datetime(2026, 10, 10, 23, 34, tzinfo=UTC)
    assert counts == {"records": 6, "minutes_without_dni": 1}  # only the gap inside is counted


def test_a_window_without_two_valid_values_is_refused():
    station = minute.Station(40.72, LONGITUDE)
    with pytest.raises(ValueError, match="fewer than two"):
        minute.to_window([(792, 5.0)], station, date(2026, 10, 11), (8, 0), (8, 5))


def test_the_flicker_statistics():
    values = [800.0] * 5 + [0.0] * 3 + [800.0] * 5 + [0.0] * 12 + [800.0] * 3
    stat = minute.window_statistics(values)
    assert stat["crossings_of_100"] == 4
    assert stat["jumps_300"] == 4
    assert stat["short_dips_under_10_min"] == 1  # only the 3-minute dip, not the 12-minute one
    assert stat["mean_dni_W_m2"] == round(sum(values) / len(values))


def test_a_fine_step_is_cut_into_chunks_the_application_accepts():
    start = datetime(2026, 10, 10, 23, 0, tzinfo=UTC)
    end = start + timedelta(minutes=530)
    chunks = minute.chunk_windows(start, end, 60)
    assert [(b - a).total_seconds() / 60 for a, b in chunks] == [200, 200, 130]
    assert chunks[0][0] == start and chunks[-1][1] == end
    assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))
    assert len(minute.chunk_windows(start, end, 300)) == 1  # 106 steps fit in one plan


def case(**over):
    base = {
        "interval_seconds": 60,
        "fr02_switches": 100,
        "fr04_switches": 40,
        "switch_change_pct": 60.0,
        "total_change_reduction_pct": 60.0,
        "daylight_gain_pct": -20.0,
        "fr02_total_change": 100.0,
        "fr04_total_change": 40.0,
        "fr02_mean_opacity": 0.1,
        "fr04_mean_opacity": 0.12,
        "fr02_above_cap_q50": 1,
        "fr04_above_cap_q50": 0,
        "fr02_above_cap_q90": 2,
        "fr04_above_cap_q90": 1,
        "never_brighter_than_fr02": True,
    }
    return {**base, **over}


def test_chunks_are_summed_and_the_means_are_weighted_by_their_length():
    parts = [
        (200, case()),
        (
            100,
            case(fr02_switches=50, fr04_switches=20, fr04_mean_opacity=0.18, fr02_mean_opacity=0.1),
        ),
    ]
    merged = minute.merge_cases(parts)
    assert merged["chunks"] == 2
    assert (merged["fr02_switches"], merged["fr04_switches"]) == (150, 60)
    assert merged["switch_change_pct"] == 60.0
    assert merged["fr04_mean_opacity"] == round((200 * 0.12 + 100 * 0.18) / 300, 4)
    assert merged["daylight_gain_pct"] < 0  # FR-04 shades more: signed, negative
    assert merged["fr02_above_cap_q90"] == 4
    assert merged["never_brighter_than_fr02"] is True


def test_one_chunk_that_is_brighter_than_fr02_makes_the_merged_case_not_safe():
    merged = minute.merge_cases([(100, case()), (100, case(never_brighter_than_fr02=False))])
    assert merged["never_brighter_than_fr02"] is False


def test_the_finest_fr04_step_is_compared_with_the_coarser_fr02_steps():
    result = {
        "cases": [
            case(interval_seconds=1800, fr02_switches=30, fr02_mean_opacity=0.2),
            case(interval_seconds=60),
            case(interval_seconds=300, fr02_switches=40, fr02_mean_opacity=0.15),
        ]
    }
    rows = minute.finest_versus_coarser(result)
    assert [(r["fine_seconds"], r["coarse_seconds"]) for r in rows] == [(60, 300), (60, 1800)]
    assert rows[0]["fr04_fine_switches"] == 40 and rows[0]["fr02_coarse_switches"] == 40
    assert rows[0]["daylight_difference_pct"] == 20.0  # 0.12 against 0.15 shades 20% less


def test_the_report_names_the_files_the_kinds_and_the_limits():
    result = {
        "label": "intermittent",
        "station_date": "2023-10-22",
        "replayed_as": "2026-10-22",
        "statistics": {
            "mean_dni_W_m2": 334,
            "crossings_of_100": 83,
            "jumps_300": 90,
            "short_dips_under_10_min": 39,
        },
        "weather_records": {"records": 500, "minutes_without_dni": 3},
        "cases": [case(), case(interval_seconds=300, fr02_switches=40, fr04_switches=40)],
    }
    provenance = [{"name": "psu23295.dat", "sha256": "abc", "url": "https://example.test/f"}]
    report = minute.build_report([result], provenance, "Penn_State_PA (psu)")
    assert "psu23295.dat" in report and "abc" in report
    assert "+60.0%" in report
    assert "Borrowed sky" in report and "synthetic_demo_not_agreed" in report
    assert "| 1 min |" in report and "| 5 min |" in report


def test_a_short_replay_runs_through_the_application(tmp_path):
    # 08:00 to 09:00 solar time at Penn State is 13:12 to 14:12 UTC.
    rows = [(792 + i, "600.0", 0) for i in range(61)]
    path = write_surfrad(tmp_path / "psu23284.dat", rows)
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    results = minute.replay_minutes(
        {date(2023, 10, 11): path},
        [minute.MinuteDay("clear", date(2023, 10, 11))],
        (60, 1800),
        template,
        (8, 0),
        (9, 0),
    )
    assert results[0]["replayed_as"] == "2026-10-11"
    assert results[0]["weather_records"] == {"records": 61, "minutes_without_dni": 0}
    fine, coarse = results[0]["cases"]
    assert (fine["interval_seconds"], fine["chunks"]) == (60, 1)
    assert coarse["interval_seconds"] == 1800
    assert fine["never_brighter_than_fr02"] and coarse["never_brighter_than_fr02"]


def test_a_file_without_data_rows_is_refused(tmp_path):
    path = tmp_path / "psu23284.dat"
    path.write_text(" Penn State\n   40.72  -77.93  376 m version 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no data rows"):
        minute.read_surfrad(path)


def test_a_missing_day_file_stops_the_tool_and_names_where_to_get_it(tmp_path, capsys):
    with pytest.raises(SystemExit):
        minute.main(["--surfrad-dir", str(tmp_path), "--day", "clear=2023-10-11"])
    error = capsys.readouterr().err
    assert "psu23284.dat is missing" in error
    assert "gml.noaa.gov/aftp/data/radiation/surfrad/Penn_State_PA/2023/psu23284.dat" in error


def test_the_tool_makes_no_network_request():
    source = Path(minute.__file__).read_text(encoding="utf-8")
    assert "urllib" not in source and "requests" not in source and "socket" not in source


def test_the_report_states_data_handling_next_steps_and_no_secrets():
    report = minute.build_report([], [], "Penn_State_PA (psu)")
    assert "## Data handling and security" in report and "## Next steps" in report
    assert "No network request" in report
    assert "No secrets, no personal data" in report
