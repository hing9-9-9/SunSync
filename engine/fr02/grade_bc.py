"""FR-02.2 grades B and C: shade a cell only when the sunlight is stronger than the grade allows.

For one cell, one region and one time step, the strongest tile intensity (W/m2, as FR-01 gives it,
without glass or film) is compared with the cap of the region's grade:

- at or below the cap: nothing is needed;
- above the cap and the film at its darkest brings it to the cap or below: `opaque`;
- above the cap and even the darkest film cannot: `infeasible`;
- no intensity is known (and none of the known ones is above the cap): `unknown`, never zero.

The time rule is the same as for grade A: a step shades from t - interval to t + interval.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

from .cell_mapping import CellCandidate, FilmWindow
from .plan import Need, grade_of, widened, window_of

CAPPED_GRADES = ("B", "C")


def _state(intensities: Sequence[float | None], cap: float, film_min: float) -> str | None:
    known = [value for value in intensities if value is not None]
    if known and max(known) > cap:
        return "opaque" if max(known) * film_min <= cap else "infeasible"
    return "unknown" if len(known) < len(intensities) else None


def grade_bc_needs(
    *,
    windows: Iterable[FilmWindow],
    candidates: Iterable[CellCandidate],
    grades: Mapping[str, str],
    caps: Mapping[str, float],
    start_time: datetime,
    end_time: datetime,
    interval_seconds: int,
) -> list[Need]:
    """Return the needs of the cells that grade B and C areas require.

    `grades` maps every region_id to its grade and `caps` maps "B" and "C" to their intensity
    caps in W/m2. Regions of other grades are left to their own rules.
    """

    for grade in CAPPED_GRADES:
        if grade not in caps:
            raise ValueError(f"no intensity cap for grade {grade}")
    by_id = {window.window_id: window for window in windows}
    interval = timedelta(seconds=interval_seconds)
    needs: list[Need] = []
    for candidate in candidates:
        window = window_of(by_id, candidate.window_id)
        intensities: dict[str, list[float | None]] = defaultdict(list)
        for cause in candidate.causes:
            if grade_of(grades, cause.region_id) in CAPPED_GRADES:
                intensities[cause.region_id].append(cause.direct_irradiance_W_m2)
        span = widened(candidate.timestamp, interval, start_time, end_time)
        if span is None:
            continue
        for region, values in sorted(intensities.items()):
            grade = grades[region]
            state = _state(values, caps[grade], window.film_min_transmittance)
            if state is not None:
                needs.append(
                    Need(
                        window.window_id, candidate.row, candidate.col, *span, state, region, grade
                    )
                )
    return needs
