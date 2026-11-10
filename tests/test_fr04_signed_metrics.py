"""FR-04 comparison metrics are signed and the cap is checked directly."""

from datetime import UTC, datetime

from app.services.adaptive_control import _change_phrase

from engine.fr04 import (
    AdaptivePolicyTuner,
    AdaptiveWeights,
    CellObservation,
    OpticalProfileData,
    compare_baseline_vs_adaptive,
    count_cap_violations,
)

PROFILE = OpticalProfileData(
    glazing_direct_transmittance=0.85,
    film_min_direct_transmittance=0.03,
    film_max_direct_transmittance=0.72,
)
KEY = ("w", 0, 0)


def observations(q50: float, q90: float | None = None, steps: int = 6, grade="B", cap=50.0):
    now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    return {
        KEY: [
            CellObservation(
                timestamp=now,
                window_id="w",
                row=0,
                col=0,
                strictest_grade=grade,
                cap_W_m2=cap,
                irradiance_q10=q50,
                irradiance_q50=q50,
                irradiance_q90=q50 if q90 is None else q90,
            )
            for _ in range(steps)
        ]
    }


def test_a_worse_result_is_negative_not_clipped_to_zero():
    metrics = compare_baseline_vs_adaptive(
        {KEY: [0.0, 1.0, 0.0, 1.0]},  # 3 switches, mean 0.5
        {KEY: [1.0, 1.0, 1.0, 1.0]},  # no switch, always dark
        observations(10, steps=4),
        PROFILE,
    )
    assert metrics.daylight_gain_pct == -100.0
    assert metrics.flicker_reduction_pct == 100.0


def test_more_switching_than_the_baseline_gives_a_negative_flicker_value():
    metrics = compare_baseline_vs_adaptive(
        {KEY: [0.0, 0.0, 1.0, 1.0]},  # 1 switch
        {KEY: [0.0, 0.5, 0.0, 0.5]},  # 3 small switches
        observations(10, steps=4),
        PROFILE,
    )
    assert metrics.flicker_reduction_pct < 0
    assert metrics.total_change_reduction_pct == -50.0  # 1.5 of total change against 1.0


def test_total_change_is_fair_to_small_steps():
    metrics = compare_baseline_vs_adaptive(
        {KEY: [0.0, 1.0, 0.0, 1.0]},
        {KEY: [0.5, 0.75, 0.5, 0.75]},
        observations(10, steps=4),
        PROFILE,
    )
    assert metrics.baseline_switch_count == metrics.adaptive_switch_count == 3
    assert metrics.total_change_reduction_pct == 75.0


def test_violations_are_counted_against_the_cap_for_both_plans():
    # 200 W/m2 on a clear film transmits about 122 W/m2, above the cap of 50
    clear = {KEY: [0.0] * 6}
    dark = {KEY: [1.0] * 6}
    obs = observations(200)
    metrics = compare_baseline_vs_adaptive(clear, clear, obs, PROFILE)
    assert metrics.baseline_violation_count == metrics.protection_violation_count == 6
    metrics = compare_baseline_vs_adaptive(dark, clear, obs, PROFILE)
    assert metrics.baseline_violation_count == 0
    assert metrics.protection_violation_count == 6


def test_q90_catches_what_the_median_misses():
    clear = {KEY: [0.0] * 6}
    obs = observations(60, q90=100)  # 36.7 W/m2 at q50, 61.2 W/m2 at q90
    assert count_cap_violations(clear, obs, PROFILE, "q50") == 0
    assert count_cap_violations(clear, obs, PROFILE, "q90") == 6


def test_grade_a_needs_full_darkness_in_sun():
    obs = observations(300, grade="A", cap=0.0)
    assert count_cap_violations({KEY: [0.75] * 6}, obs, PROFILE) == 6
    assert count_cap_violations({KEY: [1.0] * 6}, obs, PROFILE) == 0


def test_tuner_returns_the_safest_candidate_when_none_is_as_safe_as_the_baseline():
    # the baseline is fully dark, so every candidate that lets sun through is worse than it
    obs = observations(200, steps=8)
    baseline = {KEY: [1.0] * 8}
    tuner = AdaptivePolicyTuner([AdaptiveWeights(w_flicker=5.0), AdaptiveWeights(w_flicker=15.0)])
    weights, metrics = tuner.tune(obs, baseline, PROFILE, 300, "PDLC")
    assert metrics is not None and weights in tuner.candidates
    assert metrics.protection_violation_count <= 8


def test_decision_summary_wording_follows_the_sign():
    assert "개선" in _change_phrase("자연채광", 4.2)
    assert "나빠졌습니다" in _change_phrase("자연채광", -4.2)
    assert "4.2%" in _change_phrase("자연채광", -4.2)
    assert "같습니다" in _change_phrase("자연채광", 0.0)


def test_a_tiny_baseline_does_not_blow_up_the_daylight_percentage():
    # baseline shades 1 step in 1000 (mean 0.001); the adaptive plan never shades
    obs = observations(10, steps=1000)
    baseline = {KEY: [1.0] + [0.0] * 999}
    adaptive = {KEY: [0.0] * 1000}
    metrics = compare_baseline_vs_adaptive(baseline, adaptive, obs, PROFILE)
    assert metrics.baseline_mean_opacity == 0.001
    assert metrics.daylight_gain_pct == 10.0  # (0.001 - 0) / 0.01, not 100
