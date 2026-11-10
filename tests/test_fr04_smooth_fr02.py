"""FR-04 as a smoothing step on the FR-02 plan: it keeps the plan steady and never brightens it."""

import random
from datetime import UTC, datetime, timedelta

from engine.fr04 import AdaptiveWeights, CellObservation, OpticalProfileData, smooth_fr02_plan

PROFILE = OpticalProfileData(
    glazing_direct_transmittance=0.85,
    film_min_direct_transmittance=0.03,
    film_max_direct_transmittance=0.72,
)
START = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
WEIGHTS = AdaptiveWeights()  # hold and gap of 600 s: two steps of 300 s


def observations(
    q50s, *, grade="B", cap=30.0, band=0.0, reasons=(("desk", "B"),)
) -> list[CellObservation]:
    return [
        CellObservation(
            timestamp=START + timedelta(seconds=300 * t),
            window_id="w",
            row=0,
            col=0,
            strictest_grade=grade,
            cap_W_m2=cap,
            irradiance_q10=q50 * (1 - band),
            irradiance_q50=q50,
            irradiance_q90=q50 * (1 + band),
            reasons=reasons,
        )
        for t, q50 in enumerate(q50s)
    ]


def smooth(opacities, obs, states=None):
    states = states or ["opaque" if o > 0 else "transparent" for o in opacities]
    return smooth_fr02_plan(opacities, states, obs, PROFILE, WEIGHTS, 300)


def test_a_short_gap_between_shaded_steps_is_filled():
    obs = observations([5, 5, 5, 5, 5])  # low sun: hysteresis does not apply
    steps = smooth([1, 1, 0, 1, 1], obs)
    assert [s.opacity for s in steps] == [1, 1, 1, 1, 1]


def test_a_long_gap_is_left_open():
    obs = observations([5] * 7)
    steps = smooth([1, 0, 0, 0, 0, 0, 1], obs)
    assert steps[3].opacity == 0.0 and steps[4].opacity == 0.0


def test_the_cell_stays_shaded_for_the_minimum_holding_time():
    obs = observations([5] * 6)
    steps = smooth([1, 0, 0, 0, 0, 0], obs)
    assert [s.opacity for s in steps] == [1, 1, 0, 0, 0, 0]


def test_hysteresis_keeps_a_cell_dark_until_the_sun_is_clearly_below_the_cap():
    # FR-02 brightens as soon as the intensity is at the cap (30). With a band of 20 percent the
    # clear film still lets too much through at 25 W/m2, so the cell stays dark until the sun has
    # really dropped.
    obs = observations([40, 25, 25, 25, 25, 2, 2, 2], band=0.2)
    steps = smooth([1, 0, 0, 0, 0, 0, 0, 0], obs)
    opacities = [s.opacity for s in steps]
    assert opacities[1:5] == [1, 1, 1, 1]
    assert opacities[5:] == [0, 0, 0]


def test_hysteresis_does_not_apply_to_grade_a_or_cells_without_a_cap():
    for grade, cap in (("A", 0.0), ("D", None)):
        obs = observations([25] * 6, grade=grade, cap=cap, band=0.2)
        steps = smooth([1, 0, 0, 0, 0, 0], obs)
        assert [s.opacity for s in steps][2:] == [0, 0, 0, 0]


def test_the_smoothed_plan_is_never_brighter_than_the_fr02_plan():
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(1, 40)
        baseline = [float(rng.random() < 0.4) for _ in range(n)]
        obs = observations(
            [rng.choice([0, 3, 20, 28, 35, 80, 400]) for _ in range(n)],
            grade=rng.choice(["A", "B", "C", "D"]),
            cap=rng.choice([0.0, 30.0, 70.0]),
            band=rng.choice([0.0, 0.2, 0.6]),
        )
        steps = smooth(baseline, obs)
        assert len(steps) == n
        assert all(step.opacity >= base for step, base in zip(steps, baseline, strict=True))


def test_fr02_states_such_as_unknown_and_infeasible_are_kept():
    obs = observations([5] * 5)
    steps = smooth(
        [0, 1, 1, 1, 0], obs, ["transparent", "unknown", "unknown", "infeasible", "transparent"]
    )
    assert [s.state for s in steps][1:4] == ["unknown", "unknown", "infeasible"]


def test_an_empty_plan_gives_no_steps():
    assert smooth_fr02_plan([], [], [], PROFILE, WEIGHTS, 300) == ()
