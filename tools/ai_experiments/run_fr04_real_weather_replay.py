"""FR-04-2: replay real KMA 10-minute observations through FR-01, FR-02 and FR-04.

For each chosen day the KMA downward shortwave radiation (DSR) is converted to direct normal
irradiance (DNI) and put into the weather of an example Scene. The application's own API then
computes the FR-02 plan and the smoothed FR-04 plan, and its comparison metrics are tabulated for
the screen's 30-minute step and the API's 5-minute step.

The observation is used as if it had been forecast perfectly, so the result is an upper bound of
what the stabilization can do on that day. See the report for the other limitations.

Usage:
    python tools/ai_experiments/run_fr04_real_weather_replay.py --kma-csv PATH/kma_gk2a.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
for entry in (WORKSPACE_ROOT, WORKSPACE_ROOT / "backend"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import itertools

from app.db import init_db
from app.main import create_app
from app.nowcast.kma_ml import calculate_solar_geometry, dsr_to_dni_physics
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from engine.fr01.models import Site

KST = timezone(timedelta(hours=9))
STEP = timedelta(minutes=10)
# The frozen final-test input (KMA) must not be used to pick or judge anything.
FROZEN_FROM, FROZEN_TO = date(2026, 11, 2), date(2026, 11, 5)
DEFAULT_DAYS = (
    ("clear", "2026-10-10"),
    ("intermittent", "2026-10-16"),
    ("intermittent (restless)", "2026-10-05"),
    ("overcast", "2026-10-18"),
)
DEFAULT_SCENE = WORKSPACE_ROOT / "docs" / "examples" / "fr06_scene_example.json"
DEFAULT_INTERVALS = (300, 600, 1800)  # 600 s is the step of the KMA data itself


@dataclass(frozen=True)
class KmaRow:
    timestamp: datetime
    dsr_W_m2: float | None
    cloud_cover_pct: float | None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_day(text: str) -> date:
    day = date.fromisoformat(text)
    if FROZEN_FROM <= day <= FROZEN_TO:
        raise ValueError(f"{day} lies in the frozen final-test period and must not be used")
    return day


def parse_clock(text: str) -> tuple[int, int]:
    hour, _, minute = text.partition(":")
    value = (int(hour), int(minute or 0))
    if not (0 <= value[0] <= 23 and 0 <= value[1] <= 59):
        raise ValueError(f"not a time of day: {text}")
    return value


def day_window(
    day: date, start: tuple[int, int], end: tuple[int, int]
) -> tuple[datetime, datetime]:
    """UTC start and end of the replayed hours of a Korean calendar day."""

    def moment(clock: tuple[int, int]) -> datetime:
        return datetime(day.year, day.month, day.day, clock[0], clock[1], tzinfo=KST).astimezone(
            UTC
        )

    return moment(start), moment(end)


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def read_kma_rows(path: Path, start: datetime, end: datetime) -> list[KmaRow]:
    """Rows with start <= timestamp <= end, in time order."""
    rows: list[KmaRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            moment = datetime.strptime(record["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
            if start <= moment <= end:
                rows.append(
                    KmaRow(
                        moment,
                        _number(record["dsr_raw"]),
                        _number(record["cloud_cover_pct"]),
                    )
                )
    rows.sort(key=lambda row: row.timestamp)
    return rows


def day_statistics(rows: Sequence[KmaRow]) -> dict[str, Any]:
    """How cloudy and how restless the replayed hours are."""
    pairs = [row for row in rows if row.dsr_W_m2 is not None and row.cloud_cover_pct is not None]
    clouds = [row.cloud_cover_pct for row in pairs if row.cloud_cover_pct is not None]
    dsr = [row.dsr_W_m2 for row in pairs if row.dsr_W_m2 is not None]
    flips = sum(1 for a, b in itertools.pairwise(clouds) if (a >= 50) != (b >= 50))
    jumps = sum(1 for a, b in itertools.pairwise(dsr) if abs(b - a) >= 150)
    return {
        "rows": len(rows),
        "rows_with_values": len(pairs),
        "mean_cloud_cover_pct": round(sum(clouds) / len(clouds), 1) if clouds else None,
        "mean_dsr_W_m2": round(sum(dsr) / len(dsr), 1) if dsr else None,
        "cloud_cover_flips": flips,
        "dsr_jumps_150": jumps,
    }


def to_dni_records(
    rows: Sequence[KmaRow], site: Site
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scene weather records (direct normal irradiance) from KMA DSR rows.

    A row without a DSR value is left out; the Scene interpolates over it. The first and the last
    row of the window must exist, because the Scene cannot extrapolate.
    """
    if len(rows) < 2:
        raise ValueError("the window holds fewer than two KMA rows")
    geometry = calculate_solar_geometry(site, [row.timestamp for row in rows])
    records: list[dict[str, Any]] = []
    skipped = 0
    for row, sun in zip(rows, geometry, strict=True):
        if row.dsr_W_m2 is None:
            skipped += 1
            continue
        _kt, _kd, dni = dsr_to_dni_physics(
            row.dsr_W_m2, sun["clear_sky_dni"], sun["zenith_deg"], row.timestamp
        )
        records.append(
            {
                "valid_time": row.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "interval_start": None,
                "interval_end": None,
                "dni_W_m2": round(dni, 3),
            }
        )
    if not records or records[0]["valid_time"] != rows[0].timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"):
        raise ValueError("the first KMA row of the window has no DSR value")
    if records[-1]["valid_time"] != rows[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"):
        raise ValueError("the last KMA row of the window has no DSR value")
    return records, {"records": len(records), "rows_without_dsr": skipped}


def build_scene(
    template: dict[str, Any],
    scene_id: str,
    site: Site,
    records: list[dict[str, Any]],
    fetched_at: datetime,
) -> dict[str, Any]:
    """The example Scene with the site moved to the KMA point and the replayed weather."""
    scene = json.loads(json.dumps(template))
    scene["scene_id"] = scene_id
    scene["site"]["latitude_deg"] = site.latitude_deg
    scene["site"]["longitude_deg"] = site.longitude_deg
    weather = scene["weather"]
    weather["weather_id"] = f"weather_{scene_id}"
    # The Scene schema only accepts these sources; the label says "replay", not "synthetic".
    weather["source"] = "synthetic"
    weather["variable"] = "direct_normal_irradiance_instant"
    weather["temporal_meaning"] = "instant"
    weather["interpolation"] = "linear_estimate"
    weather["fetched_at"] = fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    weather["max_age_s"] = 3600
    weather["records"] = records
    # The scenario of the example names its weather and the period it covers.
    scene["scenario"]["weather_id"] = weather["weather_id"]
    scene["scenario"]["start_time"] = records[0]["valid_time"]
    scene["scenario"]["end_time"] = records[-1]["valid_time"]
    return scene


def _covered_steps(
    plan: dict[str, Any], grid: Sequence[datetime]
) -> set[tuple[Any, int, int, datetime]]:
    """The (cell, step) pairs of the grid at which the plan shades the cell.

    A plan segment may begin between two steps (FR-02 uses the exact moment the sunlight enters
    a cell), so segments are compared on the step grid, not by their own start times.
    """
    covered: set[tuple[Any, int, int, datetime]] = set()
    for window in plan["windows"]:
        for cell in window["cells"]:
            spans = [
                (
                    datetime.fromisoformat(item["start_time"]),
                    datetime.fromisoformat(item["end_time"]),
                )
                for item in cell["segments"]
            ]
            for moment in grid:
                if any(begin <= moment < finish for begin, finish in spans):
                    covered.add((window["window_id"], cell["row"], cell["col"], moment))
    return covered


def step_grid(start: datetime, minutes: int, step_seconds: int) -> list[datetime]:
    count = (minutes * 60) // step_seconds
    return [start + timedelta(seconds=step_seconds * index) for index in range(count)]


def never_brighter(response: dict[str, Any], grid: Sequence[datetime]) -> bool:
    """Every cell-step the FR-02 plan shades is also shaded in the smoothed plan."""
    return _covered_steps(response["baseline_plan"], grid) <= _covered_steps(
        response["stabilized_plan"], grid
    )


def _change_pct(before: float, after: float) -> float | None:
    """Signed: positive means `after` is lower (better) than `before`."""
    if before <= 0:
        return None
    return round((before - after) / before * 100.0, 1)


def case_result(
    response: dict[str, Any], step_seconds: int, grid: Sequence[datetime]
) -> dict[str, Any]:
    metrics = response["metrics"]
    return {
        "interval_seconds": step_seconds,
        "fr02_switches": metrics["baseline_switch_count"],
        "fr04_switches": metrics["adaptive_switch_count"],
        "switch_change_pct": _change_pct(
            metrics["baseline_switch_count"], metrics["adaptive_switch_count"]
        ),
        "fr02_total_change": round(metrics["baseline_total_change"], 3),
        "fr04_total_change": round(metrics["adaptive_total_change"], 3),
        "total_change_reduction_pct": round(metrics["total_change_reduction_pct"], 1),
        "fr02_mean_opacity": round(metrics["baseline_mean_opacity"], 4),
        "fr04_mean_opacity": round(metrics["adaptive_mean_opacity"], 4),
        "daylight_gain_pct": round(metrics["daylight_gain_pct"], 1),
        "fr02_above_cap_q50": metrics["baseline_violation_count"],
        "fr04_above_cap_q50": metrics["protection_violation_count"],
        "fr02_above_cap_q90": metrics["baseline_violation_count_q90"],
        "fr04_above_cap_q90": metrics["protection_violation_count_q90"],
        "never_brighter_than_fr02": never_brighter(response, grid),
    }


@dataclass(frozen=True)
class ReplayDay:
    label: str
    day: date


def replay(
    csv_path: Path,
    days: Sequence[ReplayDay],
    intervals: Sequence[int],
    scene_template: dict[str, Any],
    start_clock: tuple[int, int],
    end_clock: tuple[int, int],
) -> list[dict[str, Any]]:
    """One result per day, each with one case per interval."""
    results: list[dict[str, Any]] = []
    with _client() as client:
        for entry in days:
            start, end = day_window(entry.day, start_clock, end_clock)
            rows = read_kma_rows(csv_path, start, end)
            first = _first_site_row(csv_path)
            site = Site(first["latitude"], first["longitude"], "Asia/Seoul")
            records, counts = to_dni_records(rows, site)
            minutes = int((end - start).total_seconds() // 60)
            cases = []
            for interval in intervals:
                scene_id = f"replay_{entry.day:%Y%m%d}_{interval}"
                scene = build_scene(
                    scene_template,
                    scene_id,
                    site,
                    records,
                    datetime.now(UTC) - timedelta(seconds=1),
                )
                created = client.post("/api/scenes", json=scene)
                if created.status_code != 201:
                    raise RuntimeError(
                        f"scene rejected: {created.status_code} {created.text[:300]}"
                    )
                request = {
                    "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duration_minutes": minutes,
                    "interval_seconds": interval,
                    "mode": "smooth_fr02",
                }
                response = client.post(
                    f"/api/scenes/{scene_id}/adaptive-shading-plan", json=request
                )
                if response.status_code != 200:
                    raise RuntimeError(f"plan failed: {response.status_code} {response.text[:300]}")
                grid = step_grid(start, minutes, interval)
                cases.append(case_result(response.json(), interval, grid))
            results.append(
                {
                    "label": entry.label,
                    "date_kst": entry.day.isoformat(),
                    "hours_kst": [
                        f"{start_clock[0]:02d}:{start_clock[1]:02d}",
                        f"{end_clock[0]:02d}:{end_clock[1]:02d}",
                    ],
                    "statistics": day_statistics(rows),
                    "weather_records": counts,
                    "cases": cases,
                }
            )
    return results


CONTROL_DNI_W_M2 = (0.0, 20.0, 50.0, 100.0, 300.0, 700.0)


def constant_sky_controls(
    csv_path: Path,
    day: date,
    scene_template: dict[str, Any],
    start_clock: tuple[int, int],
    end_clock: tuple[int, int],
) -> list[dict[str, Any]]:
    """How strongly the FR-02 plan of this Scene reacts to the sky.

    The same day and geometry are run with a constant direct irradiance. If the plan hardly
    changes between a thin and a strong sky, clouds cannot create much flicker in this Scene.
    """
    start, end = day_window(day, start_clock, end_clock)
    rows = read_kma_rows(csv_path, start, end)
    first = _first_site_row(csv_path)
    site = Site(first["latitude"], first["longitude"], "Asia/Seoul")
    records, _ = to_dni_records(rows, site)
    minutes = int((end - start).total_seconds() // 60)
    grid = step_grid(start, minutes, 300)
    results: list[dict[str, Any]] = []
    with _client() as client:
        for value in CONTROL_DNI_W_M2:
            constant = [dict(record, dni_W_m2=value) for record in records]
            scene_id = f"control_{int(value)}"
            scene = build_scene(
                scene_template, scene_id, site, constant, datetime.now(UTC) - timedelta(seconds=1)
            )
            created = client.post("/api/scenes", json=scene)
            if created.status_code != 201:
                raise RuntimeError(f"scene rejected: {created.status_code} {created.text[:300]}")
            request = {
                "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_minutes": minutes,
                "interval_seconds": 300,
                "mode": "smooth_fr02",
            }
            response = client.post(f"/api/scenes/{scene_id}/adaptive-shading-plan", json=request)
            if response.status_code != 200:
                raise RuntimeError(f"plan failed: {response.status_code} {response.text[:300]}")
            body = response.json()
            results.append(
                {
                    "dni_W_m2": value,
                    "fr02_switches": body["metrics"]["baseline_switch_count"],
                    "fr02_shaded_cell_steps": len(_covered_steps(body["baseline_plan"], grid)),
                }
            )
    return results


def _first_site_row(csv_path: Path) -> dict[str, float]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        record = next(csv.DictReader(handle))
    return {
        "latitude": float(record["returned_latitude"]),
        "longitude": float(record["returned_longitude"]),
    }


class _ClientContext:
    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        engine = create_engine(
            f"sqlite:///{Path(self._directory.name) / 'replay.db'}",
            connect_args={"check_same_thread": False},
        )
        init_db(engine)
        self._client = TestClient(create_app(engine))

    def __enter__(self) -> TestClient:
        self._client.__enter__()
        return self._client

    def __exit__(self, *args: object) -> None:
        self._client.__exit__(*args)
        self._directory.cleanup()


def _client() -> _ClientContext:
    return _ClientContext()


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


LIMITATIONS = (
    (
        "- **One Scene, one point.** The result belongs to the example Scene of `docs/examples` "
        "(one window, protected regions A to D, caps of the app policy) at the KMA point in Seoul. "
        "It does not describe every room."
    ),
    (
        "- **The observation is treated as a perfect forecast.** NFR1.2 speaks about forecasts, which "
        "are less steady than observations; read these numbers as the most the stabilization can do "
        "on that day."
    ),
    (
        "- **DSR is not DNI.** The observed downward shortwave radiation is converted with the Erbs "
        "decomposition (`dsr_to_dni_physics`); the conversion error is part of every number."
    ),
    (
        "- **The 5-minute step is interpolated.** KMA gives one value per 10 minutes; the Scene "
        "interpolates linearly between them, so the 5-minute case is not an independent measurement. "
        "It also turns a dip of one 10-minute observation into a dip of one 5-minute step. The "
        "10-minute rows use the observations as they are: a difference between the 5-minute and the "
        "10-minute rows is an effect of the interpolation, not of the weather."
    ),
    (
        "- **Label.** The Scene only accepts the weather sources `synthetic`, `open_meteo` and "
        "`pvlib_clear_sky`; the replayed values carry `synthetic` although they are observations."
    ),
    (
        "- **Hours.** Only 08:00 to 16:50 Korean time of each day is replayed (KMA has DSR from 07:40 "
        "to 16:50), not the whole day."
    ),
    (
        "- **No forecast band.** The replay passes no uncertainty band, so the hysteresis margin of "
        "FR-04.3 uses the median value only."
    ),
)


def build_report(
    results: Sequence[dict[str, Any]],
    data_name: str,
    data_sha: str,
    controls: Sequence[dict[str, Any]] = (),
) -> str:
    lines = [
        "# FR-04 real-weather replay (FR-04-2)",
        "",
        (
            "Generated by `tools/ai_experiments/run_fr04_real_weather_replay.py`. Every number below is "
            "computed by that run; re-run it to refresh this file."
        ),
        "",
        (
            f"**Input**: `{data_name}` (SHA-256 `{data_sha}`), KMA GK2A point data every 10 minutes at "
            "37.5714 N, 126.9658 E. The frozen final-test period is not used."
        ),
        (
            "**Compared**: the FR-02 plan and the default FR-04 plan (`smooth_fr02`: gap filling, "
            "minimum holding time, hysteresis), both computed by the application's API on the replayed "
            "weather. Positive percentages are better than FR-02, negative ones are worse."
        ),
        "",
        "## The days",
        "",
        "| Day (KST) | Kind | Mean cloud cover | Mean DSR | Cloud-cover flips | DSR jumps of 150 or more |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        stat = result["statistics"]
        lines.append(
            f"| {result['date_kst']} | {result['label']} | {stat['mean_cloud_cover_pct']}% | "
            f"{stat['mean_dsr_W_m2']} W/m2 | {stat['cloud_cover_flips']} | {stat['dsr_jumps_150']} |"
        )
    lines += [
        "",
        "## Result (08:00 to 16:50 Korean time)",
        "",
        (
            "`Total change` is the sum of |opacity change| between steps. `Above cap` counts cell-steps "
            "above the protection cap at the median (q50) and at the sunny end of the band (q90)."
        ),
        "",
        (
            "| Day | Step | Switches FR-02 / FR-04 | Switch change | Total change FR-02 / FR-04 | "
            "Total change reduction | Daylight gain | Above cap q50 FR-02 / FR-04 | "
            "Above cap q90 FR-02 / FR-04 | Never brighter than FR-02 |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        for case in result["cases"]:
            lines.append(
                f"| {result['label']} {result['date_kst']} | {case['interval_seconds'] // 60} min | "
                f"{case['fr02_switches']} / {case['fr04_switches']} | "
                f"{_pct(case['switch_change_pct'])} | "
                f"{case['fr02_total_change']} / {case['fr04_total_change']} | "
                f"{_pct(case['total_change_reduction_pct'])} | {_pct(case['daylight_gain_pct'])} | "
                f"{case['fr02_above_cap_q50']} / {case['fr04_above_cap_q50']} | "
                f"{case['fr02_above_cap_q90']} / {case['fr04_above_cap_q90']} | "
                f"{'yes' if case['never_brighter_than_fr02'] else 'NO'} |"
            )
    if controls:
        lines += [
            "",
            "## How strongly the FR-02 plan reacts to the sky (control)",
            "",
            (
                "The same Scene and the geometry of the first day, with a constant direct irradiance "
                "instead of the observed one (5-minute step). If the plan hardly changes between a "
                "thin and a strong sky, clouds cannot create much flicker in this Scene."
            ),
            "",
            "| Constant DNI | FR-02 switches | FR-02 shaded cell-steps |",
            "|---|---|---|",
            *[
                f"| {c['dni_W_m2']:.0f} W/m2 | {c['fr02_switches']} | {c['fr02_shaded_cell_steps']} |"
                for c in controls
            ],
        ]
    counts = Counter()
    for result in results:
        for key, value in result["weather_records"].items():
            counts[key] += value
    lines += [
        "",
        (
            f"Weather records used: {counts['records']}; rows without a DSR value (interpolated "
            f"over): {counts['rows_without_dsr']}."
        ),
        "",
        "## Limitations",
        "",
        *LIMITATIONS,
        "",
    ]
    return "\n".join(lines)


def _day_argument(text: str) -> ReplayDay:
    label, _, value = text.partition("=")
    if not value:
        raise argparse.ArgumentTypeError("use LABEL=YYYY-MM-DD")
    try:
        return ReplayDay(label, parse_day(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kma-csv", type=Path, required=True)
    parser.add_argument(
        "--day",
        type=_day_argument,
        action="append",
        help="LABEL=YYYY-MM-DD (Korean date); repeat. Default: clear, intermittent, overcast.",
    )
    parser.add_argument("--intervals", type=int, nargs="+", default=list(DEFAULT_INTERVALS))
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--start", default="08:00", help="Korean time of day, HH:MM")
    parser.add_argument(
        "--end", default="16:50", help="Korean time of day, HH:MM (KMA has DSR until 16:50)"
    )
    parser.add_argument("--output-dir", type=Path, default=WORKSPACE_ROOT / "docs" / "reports")
    args = parser.parse_args(argv)

    days = args.day or [ReplayDay(label, parse_day(value)) for label, value in DEFAULT_DAYS]
    template = json.loads(args.scene.read_text(encoding="utf-8"))
    results = replay(
        args.kma_csv,
        days,
        args.intervals,
        template,
        parse_clock(args.start),
        parse_clock(args.end),
    )
    sha = sha256_of(args.kma_csv)
    controls = constant_sky_controls(
        args.kma_csv, days[0].day, template, parse_clock(args.start), parse_clock(args.end)
    )
    report = build_report(results, args.kma_csv.name, sha, controls)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "fr04_real_weather_replay.md").write_text(report, encoding="utf-8")
    (args.output_dir / "fr04_real_weather_replay.json").write_text(
        json.dumps({"input_sha256": sha, "results": results, "sky_controls": controls}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
