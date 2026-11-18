"""FR-04-2 with finer data: replay measured 1-minute direct irradiance through FR-02 and FR-04.

KMA gives one value per 10 minutes, too coarse to contain the flicker that FR-04.1 and FR-04.2
(10-minute rules) are meant for. NOAA SURFRAD measures direct normal irradiance (DNI) every minute
at seven US stations (public data). A day of one station is mapped by solar time onto the same
month and day of 2026 for the example Scene in Seoul, and the application's own API computes the
FR-02 plan and the smoothed FR-04 plan at several steps.

The sky is borrowed from a US station and the geometry is Seoul's; only the cloud-induced
variation of the direct irradiance is real. See the report for the limits.

Usage:
    python tools/ai_experiments/run_fr04_minute_replay.py --surfrad-dir DIR --download
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import itertools

from engine.fr01.models import Site
from tools.ai_experiments.run_fr04_real_weather_replay import (
    DEFAULT_SCENE,
    _change_pct,
    _client,
    _pct,
    parse_clock,
    plan_cases,
)

SURFRAD_ROOT = "https://gml.noaa.gov/aftp/data/radiation/surfrad"
SEOUL = Site(37.5714, 126.9658, "Asia/Seoul")
DEFAULT_FOLDER, DEFAULT_PREFIX = "Penn_State_PA", "psu"
# Days of 2023 picked from the flicker statistics below (see the report): the kind is a label
# chosen by the numbers, not a judgement of the weather.
DEFAULT_DAYS = (
    ("clear", "2023-10-11"),
    ("intermittent, sunny", "2023-10-02"),
    ("intermittent, restless", "2023-10-22"),
    ("overcast", "2023-10-06"),
)
DEFAULT_INTERVALS = (60, 300, 1800)


@dataclass(frozen=True)
class MinuteDay:
    label: str
    day: date


@dataclass(frozen=True)
class Station:
    latitude: float
    longitude: float


def doy_name(prefix: str, day: date) -> str:
    return f"{prefix}{day:%y}{day.timetuple().tm_yday:03d}.dat"


def surfrad_url(folder: str, prefix: str, day: date) -> str:
    return f"{SURFRAD_ROOT}/{folder}/{day.year}/{doy_name(prefix, day)}"


def fetch_day(folder: str, prefix: str, day: date, directory: Path) -> Path:
    """The day's file in `directory`; downloaded from NOAA when it is not there yet."""
    path = directory / doy_name(prefix, day)
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        url = surfrad_url(folder, prefix, day)
        with urllib.request.urlopen(url, timeout=60) as response:
            path.write_bytes(response.read())
    return path


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_surfrad(path: Path) -> tuple[Station, list[tuple[int, float | None]]]:
    """Station position and (UTC minute of the day, DNI) for every row of one daily file.

    A DNI value counts only when its quality flag is 0 and it is not the -9999.9 marker; the
    small negative values of the night are read as 0.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    latitude, longitude = (float(part) for part in lines[1].split()[:2])
    rows: list[tuple[int, float | None]] = []
    for line in lines[2:]:
        token = line.split()
        if len(token) < 14:
            continue
        minute = int(token[4]) * 60 + int(token[5])
        value, quality = float(token[12]), int(token[13])
        rows.append((minute, max(0.0, value) if quality == 0 and value > -9000 else None))
    return Station(latitude, longitude), rows


def solar_minutes(station: Station) -> int:
    """Minutes the station's solar time is ahead of UTC (4 minutes per degree of longitude)."""
    return round(station.longitude * 4)


def to_window(
    rows: Sequence[tuple[int, float | None]],
    station: Station,
    replay_day: date,
    start_solar: tuple[int, int],
    end_solar: tuple[int, int],
) -> tuple[list[dict[str, Any]], list[float], datetime, datetime, dict[str, int]]:
    """Scene weather records, the DNI values, and the UTC window in Seoul for one day.

    A row at solar time T of the station is placed at solar time T in Seoul on `replay_day`.
    """
    low = start_solar[0] * 60 + start_solar[1]
    high = end_solar[0] * 60 + end_solar[1]
    shift = round(SEOUL.longitude_deg * 4)  # Seoul's solar time ahead of UTC, in minutes
    midnight = datetime(replay_day.year, replay_day.month, replay_day.day, tzinfo=UTC)
    records: list[dict[str, Any]] = []
    values: list[float] = []
    for minute, value in rows:
        solar = minute + solar_minutes(station)
        if not low <= solar <= high or value is None:
            continue
        moment = midnight + timedelta(minutes=solar - shift)
        records.append(
            {
                "valid_time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "interval_start": None,
                "interval_end": None,
                "dni_W_m2": round(value, 3),
            }
        )
        values.append(value)
    if len(records) < 2:
        raise ValueError("the window holds fewer than two valid DNI values")
    # The Scene cannot extrapolate: the window starts at the first and ends at the last valid minute.
    start = datetime.strptime(records[0]["valid_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end = datetime.strptime(records[-1]["valid_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    minutes = int((end - start).total_seconds() // 60) + 1
    return (
        records,
        values,
        start,
        end,
        {"records": len(records), "minutes_without_dni": minutes - len(records)},
    )


def window_statistics(values: Sequence[float]) -> dict[str, Any]:
    """How strongly the direct irradiance varies minute by minute."""
    crossings = sum(1 for a, b in itertools.pairwise(values) if (a >= 100) != (b >= 100))
    jumps = sum(1 for a, b in itertools.pairwise(values) if abs(b - a) >= 300)
    dips, run = 0, 0
    for index, value in enumerate(values):
        if value < 100:
            run += 1
            continue
        if 0 < run < 10 and index - run - 1 >= 0 and values[index - run - 1] >= 100:
            dips += 1
        run = 0
    return {
        "minutes": len(values),
        "mean_dni_W_m2": round(sum(values) / len(values)) if values else None,
        "crossings_of_100": crossings,
        "jumps_300": jumps,
        "short_dips_under_10_min": dips,
    }


# The application refuses a plan whose intersection checks exceed its limit (about 200 steps for
# the example Scene), so a fine step is computed in chunks and the counts are summed.
MAX_STEPS = 200


def chunk_windows(start: datetime, end: datetime, interval: int) -> list[tuple[datetime, datetime]]:
    """Consecutive windows of at most MAX_STEPS steps that together cover [start, end]."""
    span = timedelta(seconds=MAX_STEPS * interval)
    windows = []
    cursor = start
    while cursor < end:
        following = min(cursor + span, end)
        windows.append((cursor, following))
        cursor = following
    return windows


def merge_cases(parts: Sequence[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """One case from the cases of consecutive chunks; each part is (minutes, case)."""
    total = sum(minutes for minutes, _ in parts)

    def added(key: str) -> Any:
        return sum(case[key] for _, case in parts)

    def weighted(key: str) -> float:
        return sum(minutes * case[key] for minutes, case in parts) / total

    switches = (added("fr02_switches"), added("fr04_switches"))
    change = (added("fr02_total_change"), added("fr04_total_change"))
    opacity = (weighted("fr02_mean_opacity"), weighted("fr04_mean_opacity"))
    return {
        "interval_seconds": parts[0][1]["interval_seconds"],
        "chunks": len(parts),
        "fr02_switches": switches[0],
        "fr04_switches": switches[1],
        "switch_change_pct": _change_pct(*switches),
        "fr02_total_change": round(change[0], 3),
        "fr04_total_change": round(change[1], 3),
        "total_change_reduction_pct": _change_pct(*change),
        "fr02_mean_opacity": round(opacity[0], 4),
        "fr04_mean_opacity": round(opacity[1], 4),
        "daylight_gain_pct": _change_pct(*opacity),
        "fr02_above_cap_q50": added("fr02_above_cap_q50"),
        "fr04_above_cap_q50": added("fr04_above_cap_q50"),
        "fr02_above_cap_q90": added("fr02_above_cap_q90"),
        "fr04_above_cap_q90": added("fr04_above_cap_q90"),
        "never_brighter_than_fr02": all(case["never_brighter_than_fr02"] for _, case in parts),
    }


def replay_minutes(
    files: dict[date, Path],
    days: Sequence[MinuteDay],
    intervals: Sequence[int],
    template: dict[str, Any],
    start_solar: tuple[int, int],
    end_solar: tuple[int, int],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with _client() as client:
        for entry in days:
            station, rows = read_surfrad(files[entry.day])
            replay_day = date(2026, entry.day.month, entry.day.day)
            records, values, start, end, counts = to_window(
                rows, station, replay_day, start_solar, end_solar
            )
            cases = []
            for interval in intervals:
                parts = []
                for index, (low, high) in enumerate(chunk_windows(start, end, interval)):
                    minutes = int((high - low).total_seconds() // 60)
                    case = plan_cases(
                        client,
                        template,
                        f"minute_{entry.day:%Y%m%d}_{interval}_{index}",
                        SEOUL,
                        records,
                        low,
                        high,
                        [interval],
                    )[0]
                    parts.append((minutes, case))
                cases.append(merge_cases(parts))
            results.append(
                {
                    "label": entry.label,
                    "station_date": entry.day.isoformat(),
                    "replayed_as": replay_day.isoformat(),
                    "statistics": window_statistics(values),
                    "weather_records": counts,
                    "cases": cases,
                }
            )
    return results


LIMITATIONS = (
    (
        "- **Borrowed sky.** The direct irradiance was measured at a US station and is placed in "
        "the example Scene in Seoul by solar time (same month and day of 2026). The cloud "
        "pattern is real, but it is not Seoul's, and the Scene's geometry is Seoul's."
    ),
    (
        "- **One Scene, one station, a few days.** The days are picked from the flicker "
        "statistics so that each kind is present; the result does not say how often such days "
        "occur."
    ),
    (
        "- **Perfect forecast.** The measurement is used as if it had been forecast exactly. A "
        "real forecast is smoother and is revised over time; the revisions are not modelled."
    ),
    (
        "- **One plan per day.** FR-04 smooths a single plan; the replay does not re-plan every "
        "few minutes as a nowcast would."
    ),
    (
        "- **Caps are demo values.** The Scene's protection policy is marked "
        "`synthetic_demo_not_agreed`; the caps decide where the plan reacts to the sky."
    ),
    (
        "- **Chunks.** The application refuses a plan with more than about 200 steps for this "
        "Scene, so the 1-minute step is computed in chunks of up to 200 minutes and the counts "
        "are summed. A cell exposed across a chunk boundary counts as two exposures in both "
        "plans; the comparison between the plans is not affected by this."
    ),
    (
        "- **Label.** The Scene only accepts the weather sources `synthetic`, `open_meteo` and "
        "`pvlib_clear_sky`; the measured values carry `synthetic`."
    ),
)


def finest_versus_coarser(result: dict[str, Any]) -> list[dict[str, Any]]:
    """FR-04 at the finest step against FR-02 planned at each coarser step.

    Planning at a coarser step is the plain way to get fewer switches; this shows whether FR-04
    at the fine step does better or only the same.
    """
    cases = sorted(result["cases"], key=lambda case: case["interval_seconds"])
    fine, rows = cases[0], []
    for coarse in cases[1:]:
        rows.append(
            {
                "fine_seconds": fine["interval_seconds"],
                "coarse_seconds": coarse["interval_seconds"],
                "fr04_fine_switches": fine["fr04_switches"],
                "fr02_coarse_switches": coarse["fr02_switches"],
                "fr04_fine_mean_opacity": fine["fr04_mean_opacity"],
                "fr02_coarse_mean_opacity": coarse["fr02_mean_opacity"],
                "daylight_difference_pct": _change_pct(
                    coarse["fr02_mean_opacity"], fine["fr04_mean_opacity"]
                ),
            }
        )
    return rows


def _step(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} s"


def build_report(
    results: Sequence[dict[str, Any]],
    provenance: Sequence[dict[str, str]],
    station: str,
) -> str:
    lines = [
        "# FR-04 replay of measured 1-minute irradiance (FR-04-2, finer data)",
        "",
        (
            "Generated by `tools/ai_experiments/run_fr04_minute_replay.py`. Every number below is "
            "computed by that run; re-run it to refresh this file."
        ),
        "",
        (
            f"**Input**: NOAA SURFRAD, {station}, direct normal irradiance measured every minute "
            "(quality flag 0 only). The days are placed by solar time into the same month and day of "
            "2026 for the example Scene in Seoul, 08:00 to 16:50 solar time."
        ),
        (
            "**Compared**: the FR-02 plan and the default FR-04 plan (`smooth_fr02`), both computed "
            "by the application's API. Positive percentages are better than FR-02."
        ),
        "",
        "## The days",
        "",
        (
            "| Station day | Kind | Mean DNI | Crossings of 100 W/m2 | Jumps of 300 or more | "
            "Dips under 10 min |"
        ),
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        stat = result["statistics"]
        lines.append(
            f"| {result['station_date']} | {result['label']} | {stat['mean_dni_W_m2']} W/m2 | "
            f"{stat['crossings_of_100']} | {stat['jumps_300']} | {stat['short_dips_under_10_min']} |"
        )
    lines += [
        "",
        "## Result",
        "",
        (
            "`Total change` is the sum of |opacity change| between steps. `Above cap` counts cell-steps "
            "above the protection cap at the median (q50) and at the sunny end of the band (q90)."
        ),
        "",
        (
            "| Day | Step | Switches FR-02 / FR-04 | Switch change | Total change reduction | "
            "Daylight gain | Above cap q50 FR-02 / FR-04 | Above cap q90 FR-02 / FR-04 | "
            "Never brighter than FR-02 |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        for case in result["cases"]:
            lines.append(
                f"| {result['label']} {result['station_date']} | "
                f"{_step(case['interval_seconds'])} | "
                f"{case['fr02_switches']} / {case['fr04_switches']} | "
                f"{_pct(case['switch_change_pct'])} | {_pct(case['total_change_reduction_pct'])} | "
                f"{_pct(case['daylight_gain_pct'])} | "
                f"{case['fr02_above_cap_q50']} / {case['fr04_above_cap_q50']} | "
                f"{case['fr02_above_cap_q90']} / {case['fr04_above_cap_q90']} | "
                f"{'yes' if case['never_brighter_than_fr02'] else 'NO'} |"
            )
    lines += [
        "",
        "## FR-04 at the finest step against FR-02 at a coarser step",
        "",
        (
            "Planning at a coarser step is the plain way to get fewer switches. A positive daylight "
            "difference means FR-04 at the finest step shades less than FR-02 at the coarser step "
            "(more daylight). Mean opacity is the share of shaded time over all cells."
        ),
        "",
        (
            "| Day | FR-04 step | FR-02 step | Switches FR-04 / FR-02 | Mean opacity FR-04 / FR-02 | "
            "Daylight difference |"
        ),
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        for row in finest_versus_coarser(result):
            lines.append(
                f"| {result['label']} {result['station_date']} | {_step(row['fine_seconds'])} | "
                f"{_step(row['coarse_seconds'])} | "
                f"{row['fr04_fine_switches']} / {row['fr02_coarse_switches']} | "
                f"{row['fr04_fine_mean_opacity']} / {row['fr02_coarse_mean_opacity']} | "
                f"{_pct(row['daylight_difference_pct'])} |"
            )
    lines += [
        "",
        "## Data files",
        "",
        "| File | SHA-256 | Source |",
        "|---|---|---|",
        *[f"| `{p['name']}` | `{p['sha256']}` | {p['url']} |" for p in provenance],
        "",
        "## Limitations",
        "",
        *LIMITATIONS,
        "",
    ]
    return "\n".join(lines)


def _day_argument(text: str) -> MinuteDay:
    label, _, value = text.partition("=")
    if not value:
        raise argparse.ArgumentTypeError("use LABEL=YYYY-MM-DD")
    try:
        return MinuteDay(label, date.fromisoformat(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surfrad-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true", help="fetch missing days from NOAA")
    parser.add_argument("--folder", default=DEFAULT_FOLDER)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--day", type=_day_argument, action="append")
    parser.add_argument("--intervals", type=int, nargs="+", default=list(DEFAULT_INTERVALS))
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--start", default="08:00", help="solar time of day, HH:MM")
    parser.add_argument("--end", default="16:50", help="solar time of day, HH:MM")
    parser.add_argument("--output-dir", type=Path, default=WORKSPACE_ROOT / "docs" / "reports")
    args = parser.parse_args(argv)

    days = args.day or [
        MinuteDay(label, date.fromisoformat(value)) for label, value in DEFAULT_DAYS
    ]
    files: dict[date, Path] = {}
    provenance = []
    for entry in days:
        path = args.surfrad_dir / doy_name(args.prefix, entry.day)
        if not path.exists():
            if not args.download:
                parser.error(f"{path} is missing; pass --download to fetch it from NOAA")
            path = fetch_day(args.folder, args.prefix, entry.day, args.surfrad_dir)
        files[entry.day] = path
        provenance.append(
            {
                "name": path.name,
                "sha256": sha256_of(path),
                "url": surfrad_url(args.folder, args.prefix, entry.day),
            }
        )
    template = json.loads(args.scene.read_text(encoding="utf-8"))
    results = replay_minutes(
        files, days, args.intervals, template, parse_clock(args.start), parse_clock(args.end)
    )
    report = build_report(results, provenance, f"{args.folder} ({args.prefix})")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "fr04_minute_replay.md").write_text(report, encoding="utf-8")
    (args.output_dir / "fr04_minute_replay.json").write_text(
        json.dumps({"files": provenance, "results": results}, indent=2) + "\n", encoding="utf-8"
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
