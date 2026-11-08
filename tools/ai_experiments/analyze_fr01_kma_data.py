"""Print reproducible terminal EDA for a normalized FR-01 KMA GK2A CSV."""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.nowcast.kma_ml import calculate_solar_geometry

from engine.fr01.models import Site

EXPECTED_INTERVAL_MINUTES = 10
REQUIRED_COLUMNS = {
    "timestamp_utc",
    "dsr_raw",
    "ca_raw_provider",
    "ca_fraction",
    "cloud_cover_pct",
    "quality_flag",
}


def _combine_csvs(input_paths: list[Path], output_path: Path) -> Path:
    """Combine normalized CSVs without changing provider value representations."""

    if len(input_paths) < 2:
        raise ValueError("at least two input CSVs are required for --combined-output")
    resolved_output = output_path.resolve()
    if any(path.resolve() == resolved_output for path in input_paths):
        raise ValueError("combined output must differ from every input CSV")

    fieldnames: list[str] | None = None
    rows: list[tuple[datetime, dict[str, str]]] = []
    seen: set[datetime] = set()
    for path in input_paths:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            current_fields = reader.fieldnames
            if not current_fields:
                raise ValueError(f"CSV has no header: {path}")
            if fieldnames is None:
                fieldnames = list(current_fields)
            elif current_fields != fieldnames:
                raise ValueError(f"CSV headers do not match: {path}")
            for line_number, row in enumerate(reader, start=2):
                raw_timestamp = row.get("timestamp_utc", "")
                try:
                    timestamp = datetime.fromisoformat(raw_timestamp).astimezone(UTC)
                except ValueError as error:
                    raise ValueError(
                        f"invalid timestamp in {path}:{line_number}"
                    ) from error
                if timestamp in seen:
                    raise ValueError(f"duplicate timestamp across inputs: {raw_timestamp}")
                seen.add(timestamp)
                rows.append((timestamp, row))

    if fieldnames is None or not rows:
        raise ValueError("input CSVs contain no data rows")
    rows.sort(key=lambda item: item[0])
    expected_step_seconds = EXPECTED_INTERVAL_MINUTES * 60
    for (previous, _), (current, _) in pairwise(rows):
        if (current - previous).total_seconds() != expected_step_seconds:
            raise ValueError(
                f"combined timestamp gap: {previous.isoformat()} -> {current.isoformat()}"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(row for _timestamp, row in rows)
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path


def _number(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def _percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "-"
    return f"{numerator / denominator * 100:.1f}%"


def _print_heading(title: str) -> None:
    print(f"\n=== {title} ===")


def _print_pairs(items: Iterable[tuple[str, Any]]) -> None:
    pairs = [(str(label), str(value)) for label, value in items]
    width = max(len(label) for label, _value in pairs)
    for label, value in pairs:
        print(f"{label:<{width}} : {value}")


def _print_frame(frame: pd.DataFrame, *, index: bool = False) -> None:
    if frame.empty:
        print("(none)")
        return
    with pd.option_context(
        "display.max_rows",
        None,
        "display.max_columns",
        None,
        "display.width",
        220,
        "display.max_colwidth",
        40,
    ):
        print(frame.to_string(index=index))


def _load_csv(path: Path, timezone: ZoneInfo, site: Site) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing_columns = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing_columns:
        raise ValueError(f"missing required columns: {', '.join(missing_columns)}")

    timestamps = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    invalid_timestamp_count = int(timestamps.isna().sum())
    if invalid_timestamp_count:
        raise ValueError(f"invalid timestamp rows: {invalid_timestamp_count}")
    frame["timestamp"] = timestamps
    frame["local_timestamp"] = timestamps.dt.tz_convert(timezone)
    frame["local_date"] = frame["local_timestamp"].dt.strftime("%Y-%m-%d")
    frame["local_hour"] = frame["local_timestamp"].dt.hour

    for column in ("dsr_raw", "ca_raw_provider", "ca_fraction", "cloud_cover_pct"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    geometries = calculate_solar_geometry(site, list(timestamps.dt.to_pydatetime()))
    frame["solar_elevation_deg"] = [item["solar_elevation_deg"] for item in geometries]
    frame["is_daylight"] = frame["solar_elevation_deg"] > 0.0
    return frame


def _timestamp_summary(frame: pd.DataFrame) -> tuple[dict[str, Any], pd.DatetimeIndex]:
    ordered = frame["timestamp"].sort_values()
    duplicate_count = int(frame["timestamp"].duplicated().sum())
    unique = pd.DatetimeIndex(ordered.drop_duplicates())
    expected = pd.date_range(
        start=unique.min(),
        end=unique.max(),
        freq=f"{EXPECTED_INTERVAL_MINUTES}min",
    )
    missing = expected.difference(unique)
    deltas = unique.to_series().diff().dropna().dt.total_seconds().div(60)
    unexpected_steps = int((deltas != EXPECTED_INTERVAL_MINUTES).sum())
    return (
        {
            "rows": len(frame),
            "unique_timestamps": len(unique),
            "duplicates": duplicate_count,
            "missing_timestamps": len(missing),
            "unexpected_steps": unexpected_steps,
            "first_utc": unique.min().isoformat(),
            "last_utc": unique.max().isoformat(),
        },
        missing,
    )


def _daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for local_date, group in frame.groupby("local_date", sort=True):
        daylight = group["is_daylight"]
        dsr_valid = group["dsr_raw"].notna()
        ca_valid = group["ca_fraction"].notna()
        rows.append(
            {
                "local_date": local_date,
                "rows": len(group),
                "day": int(daylight.sum()),
                "DSR valid": int(dsr_valid.sum()),
                "DSR miss": int((~dsr_valid).sum()),
                "day DSR miss": int((daylight & ~dsr_valid).sum()),
                "CA valid": int(ca_valid.sum()),
                "CA miss": int((~ca_valid).sum()),
                "CA outlier": int((group["quality_flag"] == "ca_out_of_range").sum()),
            }
        )
    return pd.DataFrame(rows)


def _hourly_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for hour, group in frame.groupby("local_hour", sort=True):
        dsr_valid = int(group["dsr_raw"].notna().sum())
        ca_valid = int(group["ca_fraction"].notna().sum())
        rows.append(
            {
                "hour": f"{int(hour):02d}",
                "rows": len(group),
                "day rows": int(group["is_daylight"].sum()),
                "DSR valid%": _percent(dsr_valid, len(group)),
                "DSR mean": _number(group["dsr_raw"].mean(), 1),
                "CA valid%": _percent(ca_valid, len(group)),
                "CA mean": _number(group["ca_fraction"].mean(), 3),
            }
        )
    return pd.DataFrame(rows)


def _missing_runs(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    missing = ordered[column].isna()
    run_id = missing.ne(missing.shift(fill_value=False)).cumsum()
    runs: list[dict[str, Any]] = []
    for _identifier, group in ordered[missing].groupby(run_id[missing]):
        count = len(group)
        daylight_count = int(group["is_daylight"].sum())
        runs.append(
            {
                "column": column,
                "start_utc": group["timestamp"].iloc[0].strftime("%Y-%m-%d %H:%M"),
                "end_utc": group["timestamp"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
                "points": count,
                "duration_min": count * EXPECTED_INTERVAL_MINUTES,
                "day_points": daylight_count,
                "pattern": "night_only" if daylight_count == 0 else "contains_daylight",
            }
        )
    return pd.DataFrame(runs)


def _statistics(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in ("dsr_raw", "ca_raw_provider", "ca_fraction"):
        series = frame[column]
        rows.append(
            {
                "column": column,
                "valid": int(series.notna().sum()),
                "missing": int(series.isna().sum()),
                "missing%": _percent(int(series.isna().sum()), len(series)),
                "min": _number(series.min(), 3),
                "median": _number(series.median(), 3),
                "mean": _number(series.mean(), 3),
                "max": _number(series.max(), 3),
            }
        )
    return pd.DataFrame(rows)


def _logical_checks(frame: pd.DataFrame) -> list[tuple[str, str]]:
    daylight = frame["is_daylight"]
    dsr = frame["dsr_raw"]
    ca_raw = frame["ca_raw_provider"]
    ca_fraction = frame["ca_fraction"]
    out_of_range = ca_raw.notna() & ~ca_raw.between(0.0, 1.0, inclusive="both")
    invalid_normalized = out_of_range & ca_fraction.notna()
    valid_pair = daylight & dsr.notna() & ca_fraction.notna()
    correlation = frame.loc[valid_pair, ["dsr_raw", "ca_fraction"]].corr().iloc[0, 1]
    correlation_text = _number(correlation, 3) if math.isfinite(correlation) else "-"
    return [
        ("negative DSR", str(int((dsr < 0).sum()))),
        ("night DSR > 0", str(int((~daylight & dsr.gt(0)).sum()))),
        ("daylight DSR missing", str(int((daylight & dsr.isna()).sum()))),
        ("CA raw outside 0~1", str(int(out_of_range.sum()))),
        ("out-of-range CA used as fraction", str(int(invalid_normalized.sum()))),
        ("daylight valid DSR·CA pairs", str(int(valid_pair.sum()))),
        ("daylight Pearson corr(DSR, CA)", correlation_text),
    ]


def analyze(args: argparse.Namespace) -> int:
    try:
        timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {args.timezone}") from error
    site = Site(args.site_lat, args.site_lon, args.timezone)
    input_path = (
        _combine_csvs(args.input_csv, args.combined_output)
        if args.combined_output is not None
        else args.input_csv[0]
    )
    frame = _load_csv(input_path, timezone, site)
    time_summary, missing_timestamps = _timestamp_summary(frame)

    _print_heading("INPUT")
    _print_pairs(
        [
            ("file", input_path.resolve()),
            ("source files", len(args.input_csv)),
            ("site", f"{args.site_lat:.4f}, {args.site_lon:.4f}"),
            ("display timezone", args.timezone),
        ]
    )

    _print_heading("TIMESTAMP INTEGRITY")
    _print_pairs(time_summary.items())
    if len(missing_timestamps):
        print("missing sample:", ", ".join(str(value) for value in missing_timestamps[:10]))

    _print_heading("OVERALL VALUE SUMMARY")
    _print_frame(_statistics(frame))

    _print_heading("QUALITY FLAGS")
    quality = (
        frame["quality_flag"]
        .fillna("<null>")
        .value_counts(dropna=False)
        .rename_axis("quality_flag")
        .reset_index(name="count")
    )
    quality["percent"] = quality["count"].map(lambda value: _percent(int(value), len(frame)))
    _print_frame(quality)

    _print_heading(f"DAILY SUMMARY ({args.timezone})")
    _print_frame(_daily_summary(frame))

    _print_heading(f"HOURLY PROFILE ({args.timezone})")
    _print_frame(_hourly_summary(frame))

    _print_heading("MISSING RUNS")
    all_runs = pd.concat(
        [_missing_runs(frame, "dsr_raw"), _missing_runs(frame, "ca_fraction")],
        ignore_index=True,
    )
    if all_runs.empty:
        _print_frame(all_runs)
    else:
        ranked = all_runs.sort_values(
            ["day_points", "points"], ascending=[False, False]
        ).head(args.max_missing_runs)
        _print_frame(ranked)
        print(f"shown={len(ranked)} total_runs={len(all_runs)}")

    _print_heading("LOGICAL CHECKS")
    _print_pairs(_logical_checks(frame))

    has_time_error = any(
        int(time_summary[key]) > 0
        for key in ("duplicates", "missing_timestamps", "unexpected_steps")
    )
    _print_heading("EDA VERDICT")
    _print_pairs(
        [
            ("timestamp contract", "FAIL" if has_time_error else "PASS"),
            ("review required", "daylight DSR gaps and CA outliers"),
            ("next", "record findings in docs/fr01_4_ai_kma_analysis.md"),
        ]
    )
    return 1 if has_time_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, nargs="+", required=True)
    parser.add_argument(
        "--combined-output",
        type=Path,
        help="atomically combine multiple input CSVs before analysis",
    )
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--site-lat", type=float, default=37.5714)
    parser.add_argument("--site-lon", type=float, default=126.9658)
    parser.add_argument(
        "--max-missing-runs",
        type=int,
        default=30,
        help="maximum missing runs printed after prioritizing daylight gaps",
    )
    args = parser.parse_args()
    if len(args.input_csv) > 1 and args.combined_output is None:
        parser.error("multiple --input-csv paths require --combined-output")
    if len(args.input_csv) == 1 and args.combined_output is not None:
        parser.error("--combined-output requires at least two input CSVs")
    if args.max_missing_runs < 1:
        parser.error("--max-missing-runs must be positive")
    try:
        return analyze(args)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
