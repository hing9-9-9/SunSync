"""Build a ShadingPlan from the shading needs that the grade rules produce.

A `Need` says that one film cell must be in one state for a stretch of time because of one
protected region. Several needs on one cell can overlap; this module cuts them into time-ordered
pieces and decides the state of each piece: the strictest grade present decides, and when that
grade has several needs, the most severe state wins (`infeasible`, then `opaque`, then `unknown`).
Every region active in a piece is listed as a reason.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import groupby
from typing import Any

from .cell_mapping import FilmWindow

PLAN_VERSION = "0.1.0"

_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}
_STATE_SEVERITY = {"unknown": 1, "opaque": 2, "infeasible": 3}

_Key = tuple[str, str, str]
_Piece = tuple[datetime, datetime, str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class Need:
    """One film cell must be in `state` from `start_time` to `end_time` because of one region."""

    window_id: str
    row: int
    col: int
    start_time: datetime
    end_time: datetime
    state: str
    region_id: str
    grade: str


def widened(
    moment: datetime, interval: timedelta, start_time: datetime, end_time: datetime
) -> tuple[datetime, datetime] | None:
    """Return [moment - interval, moment + interval) cut at the plan period, or None if empty."""

    low, high = max(moment - interval, start_time), min(moment + interval, end_time)
    return (low, high) if low < high else None


def grade_of(grades: Mapping[str, str], region_id: str) -> str:
    if region_id not in grades:
        raise ValueError(f"unknown protected region: {region_id}")
    return grades[region_id]


def window_of(windows: Mapping[str, FilmWindow], window_id: str) -> FilmWindow:
    if window_id not in windows:
        raise ValueError(f"candidate refers to an unknown window: {window_id}")
    return windows[window_id]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _resolve(active: Iterable[_Key]) -> tuple[str, tuple[tuple[str, str], ...]]:
    keys = list(active)
    strictest = min(_GRADE_RANK[grade] for _, _, grade in keys)
    state = max(
        (state for state, _, grade in keys if _GRADE_RANK[grade] == strictest),
        key=_STATE_SEVERITY.__getitem__,
    )
    reasons = sorted(
        {(region, grade) for _, region, grade in keys},
        key=lambda reason: (_GRADE_RANK[reason[1]], reason[0]),
    )
    return state, tuple(reasons)


def _pieces(intervals: list[tuple[datetime, datetime, _Key]]) -> list[_Piece]:
    """Cut overlapping (start, end, need) intervals into time-ordered pieces.

    Each piece has the state and the reasons that hold in it; neighbouring pieces that have the
    same state and reasons are joined.
    """

    events = sorted(
        [(start, 1, key) for start, _, key in intervals]
        + [(end, -1, key) for _, end, key in intervals]
    )
    active: Counter[_Key] = Counter()
    pieces: list[_Piece] = []
    previous: datetime | None = None
    for moment, group in groupby(events, key=lambda event: event[0]):
        if previous is not None and active:
            state, reasons = _resolve(active)
            last = pieces[-1] if pieces else None
            if last and last[1] == previous and last[2:] == (state, reasons):
                pieces[-1] = (last[0], moment, state, reasons)
            else:
                pieces.append((previous, moment, state, reasons))
        for _, change, key in group:
            active[key] += change
            if active[key] == 0:
                del active[key]
        previous = moment
    return pieces


def build_plan(
    *,
    scene_id: str,
    scene_revision: int,
    windows: Iterable[FilmWindow],
    needs: Iterable[Need],
    start_time: datetime,
    end_time: datetime,
) -> dict[str, Any]:
    """Return a ShadingPlan (as a plain dict) for the given needs."""

    window_list = list(windows)
    known_windows = {window.window_id for window in window_list}
    grouped: dict[tuple[str, int, int], list[tuple[datetime, datetime, _Key]]] = defaultdict(list)
    for need in needs:
        if need.window_id not in known_windows:
            raise ValueError(f"need refers to an unknown window: {need.window_id}")
        key = (need.window_id, need.row, need.col)
        grouped[key].append(
            (need.start_time, need.end_time, (need.state, need.region_id, need.grade))
        )

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
                        "state": state,
                        "reasons": [
                            {"region_id": region, "grade": grade} for region, grade in reasons
                        ],
                    }
                    for start, end, state, reasons in _pieces(intervals)
                ],
            }
            for (window_id, row, col), intervals in sorted(grouped.items())
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
