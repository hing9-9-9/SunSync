"""FR-02.2 grade A: shade every film cell that a direct-sun path reaches, whatever the intensity.

FR-01 looks at fixed time steps. A step at time t where a cell is needed shades the cell from
t - interval to t + interval, because the exposure may have started or ended anywhere between
the neighbouring steps. The shading is cut at the plan period.

Tiles that FR-01 could not decide (`boundary_ambiguous`) carry no crossing point and never reach
this module.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from itertools import groupby
from typing import Any

from .cell_mapping import CellCandidate, FilmWindow

PLAN_VERSION = "0.1.0"
GRADE = "A"

_Piece = tuple[datetime, datetime, tuple[str, ...]]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pieces(intervals: list[tuple[datetime, datetime, str]]) -> list[_Piece]:
    """Cut overlapping (start, end, region) intervals into time-ordered pieces.

    Each piece names the regions that are active in it; neighbouring pieces with the same
    regions are joined.
    """

    events = sorted(
        [(start, 1, region) for start, _, region in intervals]
        + [(end, -1, region) for _, end, region in intervals]
    )
    active: Counter[str] = Counter()
    pieces: list[_Piece] = []
    previous: datetime | None = None
    for moment, group in groupby(events, key=lambda event: event[0]):
        if previous is not None and active:
            regions = tuple(sorted(active))
            if pieces and pieces[-1][1] == previous and pieces[-1][2] == regions:
                pieces[-1] = (pieces[-1][0], moment, regions)
            else:
                pieces.append((previous, moment, regions))
        for _, change, region in group:
            active[region] += change
            if active[region] == 0:
                del active[region]
        previous = moment
    return pieces


def build_grade_a_plan(
    *,
    scene_id: str,
    scene_revision: int,
    windows: Iterable[FilmWindow],
    candidates: Iterable[CellCandidate],
    grades: Mapping[str, str],
    start_time: datetime,
    end_time: datetime,
    interval_seconds: int,
) -> dict[str, Any]:
    """Return a ShadingPlan (as a plain dict) that shades the cells needed by grade A areas.

    `grades` maps every region_id to its grade; candidates caused only by other grades are left
    to the grade B/C decision.
    """

    window_list = list(windows)
    known_windows = {window.window_id for window in window_list}
    interval = timedelta(seconds=interval_seconds)
    needed: dict[tuple[str, int, int], list[tuple[datetime, datetime, str]]] = defaultdict(list)
    for candidate in candidates:
        if candidate.window_id not in known_windows:
            raise ValueError(f"candidate refers to an unknown window: {candidate.window_id}")
        regions = set()
        for cause in candidate.causes:
            if cause.region_id not in grades:
                raise ValueError(f"unknown protected region: {cause.region_id}")
            if grades[cause.region_id] == GRADE:
                regions.add(cause.region_id)
        low = max(candidate.timestamp - interval, start_time)
        high = min(candidate.timestamp + interval, end_time)
        if regions and low < high:
            key = (candidate.window_id, candidate.row, candidate.col)
            needed[key].extend((low, high, region) for region in regions)

    window_plans = []
    for window in window_list:
        cells = [
            {
                "row": row,
                "col": col,
                "segments": [
                    {
                        "start_time": _iso(start),
                        "end_time": _iso(end),
                        "state": "opaque",
                        "reasons": [{"region_id": region, "grade": GRADE} for region in regions],
                    }
                    for start, end, regions in _pieces(intervals)
                ],
            }
            for (window_id, row, col), intervals in sorted(needed.items())
            if window_id == window.window_id
        ]
        window_plans.append(
            {
                "window_id": window.window_id,
                "film_rows": window.film_rows,
                "film_cols": window.film_cols,
                "cells": cells,
            }
        )
    return {
        "plan_version": PLAN_VERSION,
        "scene_id": scene_id,
        "scene_revision": scene_revision,
        "start_time": _iso(start_time),
        "end_time": _iso(end_time),
        "windows": window_plans,
    }
