"""The FR-04 experiment reports signed metrics and checks the cap directly."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from engine.fr04 import CellObservation, OpticalProfileData

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "ai_experiments"
    / "run_fr04_adaptive_control_experiment.py"
)
spec = importlib.util.spec_from_file_location("fr04_experiment", SCRIPT)
assert spec is not None and spec.loader is not None
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)

PROFILE = OpticalProfileData(
    glazing_direct_transmittance=0.85,
    film_min_direct_transmittance=0.03,
    film_max_direct_transmittance=0.72,
)
KEY = ("w", 0, 0)


def observations(q50: float, q90: float, steps: int = 4) -> dict:
    now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    return {
        KEY: [
            CellObservation(
                timestamp=now,
                window_id="w",
                row=0,
                col=0,
                strictest_grade="B",
                cap_W_m2=50.0,
                irradiance_q10=q50,
                irradiance_q50=q50,
                irradiance_q90=q90,
            )
            for _ in range(steps)
        ]
    }


def test_a_worse_result_is_reported_negative_not_clipped():
    result = experiment.evaluate_mode(
        {KEY: [0.2, 0.2, 0.2, 0.2]}, {KEY: [1.0, 1.0, 1.0, 1.0]}, observations(10, 10), PROFILE
    )
    assert result["all_cells"]["daylight_gain_pct"] < 0
    assert result["all_cells"]["adaptive_mean_opacity"] == 1.0


def test_total_change_counts_the_size_of_each_change():
    result = experiment.evaluate_mode(
        {KEY: [0.0, 1.0, 0.0, 1.0]},
        {KEY: [0.5, 0.75, 0.5, 0.75]},
        observations(10, 10),
        PROFILE,
    )
    scope = result["all_cells"]
    assert scope["baseline_switches"] == 3 and scope["adaptive_switches"] == 3
    assert scope["flicker_reduction_pct"] == 75.0  # same switches, a quarter of the change


def test_violations_are_counted_against_the_cap_not_the_baseline():
    # 200 W/m2 on a clear film transmits about 122 W/m2 (cap 50), so every step is above it
    clear = {KEY: [0.0] * 4}
    result = experiment.evaluate_mode(clear, clear, observations(200, 200), PROFILE)
    assert result["baseline_violations_q50"] == 4
    assert result["adaptive_violations_q50"] == 4


def test_the_q90_check_catches_what_the_median_check_misses():
    # median 60 W/m2 on a clear film gives 36.7 (fine); q90 of 100 gives 61.2 (above 50)
    clear = {KEY: [0.0] * 4}
    result = experiment.evaluate_mode(clear, clear, observations(60, 100), PROFILE)
    assert result["adaptive_violations_q50"] == 0
    assert result["adaptive_violations_q90"] == 4


def test_the_overcast_scenario_stays_in_the_low_irradiance_band():
    start = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    timeline = experiment.generate_solar_profile("Overcast Low Irradiance", start)
    assert all(q50 > 0 for _, _, q50, _ in timeline)
    assert max(q90 for *_, q90 in timeline) < 500
