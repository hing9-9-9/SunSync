"""FR-02.2 grade A: shade every film cell that a direct-sun path reaches, whatever the intensity.

FR-01 looks at fixed time steps. A step at time t where a cell is needed shades the cell from
t - interval to t + interval, because the exposure may have started or ended anywhere between
the neighbouring steps. The shading is cut at the plan period.

Grade A allows no light at all, so a film that cannot block fully (minimum transmittance above
zero) cannot meet it and the need is `infeasible` instead of `opaque`.

Tiles that FR-01 could not decide (`boundary_ambiguous`) carry no crossing point and never reach
this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from .cell_mapping import CellCandidate, FilmWindow
from .plan import Need, grade_of, widened, window_of

GRADE = "A"


def grade_a_needs(
    *,
    windows: Iterable[FilmWindow],
    candidates: Iterable[CellCandidate],
    grades: Mapping[str, str],
    start_time: datetime,
    end_time: datetime,
    interval_seconds: int,
) -> list[Need]:
    """Return the needs of the cells that grade A areas require.

    `grades` maps every region_id to its grade; candidates caused only by other grades are left
    to the grade B/C decision.
    """

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
            state = "opaque" if window.film_min_transmittance == 0 else "infeasible"
            needs.extend(
                Need(window.window_id, candidate.row, candidate.col, *span, state, region, GRADE)
                for region in sorted(regions)
            )
    return needs
