"""FR-02.2 grade A: shade every cell that a direct-sun path reaches, whatever the intensity.

A film that cannot block fully (minimum transmittance above zero) cannot meet grade A, so the
need is `infeasible` instead of `opaque`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from .models import CellCandidate, FilmWindow, Need
from .plan import grade_of, widened, window_of

GRADE = "A"


def grade_a_needs(
    *,
    windows: Iterable[FilmWindow],
    candidates: Iterable[CellCandidate],
    grades: Mapping[str, str],
    start_time: datetime,
    end_time: datetime,
    interval_seconds: int,
) -> tuple[Need, ...]:
    """Return the needs of the cells that grade A areas require (`grades`: region_id to grade)."""

    by_id = {window.window_id: window for window in windows}
    interval = timedelta(seconds=interval_seconds)
    needs: list[Need] = []
    for candidate in candidates:
        window = window_of(by_id, candidate.window_id)
        regions = {
            cause.region_id
            for cause in candidate.causes
            if grade_of(grades, cause.region_id) == GRADE
        }
        span = widened(candidate.timestamp, interval, start_time, end_time)
        if regions and span:
            state = "infeasible" if window.film_min_transmittance > 0 else "opaque"
            needs.extend(
                Need(window.window_id, candidate.row, candidate.col, *span, state, region, GRADE)
                for region in sorted(regions)
            )
    return tuple(needs)
