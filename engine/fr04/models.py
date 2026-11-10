"""Immutable data models and optical configurations for FR-04 Adaptive Control.

This module is pure Python with zero dependencies on FastAPI, SQLModel, or the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

HardwareFilmMode = Literal["PDLC", "LG_SGF", "SPD"]
ProtectionGrade = Literal["A", "B", "C", "D"]
CellState = Literal["opaque", "transparent", "infeasible", "unknown"]


@dataclass(frozen=True, slots=True)
class OpticalProfileData:
    """Optical parameters of window glazing and smart film."""

    glazing_direct_transmittance: float = 1.0
    film_min_direct_transmittance: float = 0.05
    film_max_direct_transmittance: float = 0.70

    def __post_init__(self) -> None:
        if not 0.0 <= self.glazing_direct_transmittance <= 1.0:
            raise ValueError("glazing_direct_transmittance must be in [0.0, 1.0]")
        if not 0.0 <= self.film_min_direct_transmittance <= 1.0:
            raise ValueError("film_min_direct_transmittance must be in [0.0, 1.0]")
        if not 0.0 <= self.film_max_direct_transmittance <= 1.0:
            raise ValueError("film_max_direct_transmittance must be in [0.0, 1.0]")
        if self.film_min_direct_transmittance > self.film_max_direct_transmittance:
            raise ValueError(
                "film_min_direct_transmittance cannot exceed film_max_direct_transmittance"
            )

    def film_transmittance(self, opacity: float) -> float:
        """Map film opacity u in [0, 1] to film direct solar transmittance.

        tau(u) = tau_max - u * (tau_max - tau_min)
        u = 0.0 (transparent) -> tau_max
        u = 1.0 (opaque)      -> tau_min
        """
        clamped_u = max(0.0, min(1.0, opacity))
        return self.film_max_direct_transmittance - clamped_u * (
            self.film_max_direct_transmittance - self.film_min_direct_transmittance
        )

    def total_transmittance(self, opacity: float) -> float:
        """Combined transmittance through glazing and smart film."""
        return self.glazing_direct_transmittance * self.film_transmittance(opacity)

    def transmitted_irradiance(self, incident_irradiance: float, opacity: float) -> float:
        """Transmitted direct irradiance (W/m2) at given film opacity."""
        return incident_irradiance * self.total_transmittance(opacity)


@dataclass(frozen=True, slots=True)
class AdaptiveWeights:
    """Hyperparameters and stabilization settings for the FR-04 adaptive controller."""

    w_damage: float = 1000.0
    w_daylight: float = 1.0
    w_flicker: float = 15.0
    kappa_B: float = 1.2
    kappa_C: float = 0.8
    brightening_margin_W_m2: float = 15.0
    min_gap_seconds: int = 600
    brightening_hold_seconds: int = 600
    enable_edge_margin: bool = False
    edge_margin_threshold_m: float = 0.03

    def __post_init__(self) -> None:
        if self.w_damage < 0 or self.w_daylight < 0 or self.w_flicker < 0:
            raise ValueError("Cost weights must be non-negative")
        if self.kappa_B < self.kappa_C:
            raise ValueError("kappa_B must be >= kappa_C for risk-averse ordering")
        if self.brightening_margin_W_m2 < 0:
            raise ValueError("brightening_margin_W_m2 must be non-negative")
        if self.min_gap_seconds < 0 or self.brightening_hold_seconds < 0:
            raise ValueError("Time intervals must be non-negative")


@dataclass(frozen=True, slots=True)
class UncertaintyQuantiles:
    """Quantile distribution of solar irradiance at a given time step."""

    q10: float
    q50: float
    q90: float

    def __post_init__(self) -> None:
        if self.q10 < 0 or self.q50 < 0 or self.q90 < 0:
            raise ValueError("Quantiles must be non-negative")
        if not (self.q10 <= self.q50 <= self.q90):
            raise ValueError(
                f"Quantiles must be non-decreasing: q10={self.q10}, q50={self.q50}, q90={self.q90}"
            )

    @property
    def spread(self) -> float:
        """Uncertainty spread sigma = q90 - q10."""
        return self.q90 - self.q10


@dataclass(frozen=True, slots=True)
class CellObservation:
    """Solar exposure data for one film cell at one time step."""

    timestamp: datetime
    window_id: str
    row: int
    col: int
    strictest_grade: ProtectionGrade | None = None
    cap_W_m2: float | None = None
    irradiance_q10: float = 0.0
    irradiance_q50: float = 0.0
    irradiance_q90: float = 0.0
    reasons: tuple[tuple[str, str], ...] = ()
    is_edge_adjacent: bool = False


@dataclass(frozen=True, slots=True)
class OptimizedCellStep:
    """Optimized opacity and cost breakdown for one cell at one time step."""

    timestamp: datetime
    opacity: float
    transmittance: float
    state: CellState
    risk_cost: float = 0.0
    daylight_cost: float = 0.0
    flicker_cost: float = 0.0
    reasons: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CellTrajectory:
    """Time-series trajectory of optimized control steps for one cell."""

    window_id: str
    row: int
    col: int
    steps: tuple[OptimizedCellStep, ...]


@dataclass(frozen=True, slots=True)
class AdaptiveComparisonMetrics:
    """Quantitative comparison between baseline and adaptive shading plans."""

    baseline_switch_count: int
    adaptive_switch_count: int
    flicker_reduction_pct: float
    baseline_mean_opacity: float
    adaptive_mean_opacity: float
    daylight_gain_pct: float
    protection_violation_count: int
    # Percent values are signed: positive is better than the baseline, negative is worse.
    # `protection_violation_count` counts cell-steps above the cap if the median forecast (q50)
    # is true; the `_q90` fields do the same for the sunny end of the band. The baseline's own
    # counts are given so that an unreachable cap is not blamed on the adaptive plan.
    baseline_violation_count: int = 0
    protection_violation_count_q90: int = 0
    baseline_violation_count_q90: int = 0
    # Sum of |opacity change| between steps; grows with the size of each change, not only
    # with their number, so it is fair to multi-level film.
    baseline_total_change: float = 0.0
    adaptive_total_change: float = 0.0
    total_change_reduction_pct: float = 0.0
    tuned_weights: dict[str, float] = field(default_factory=dict)
