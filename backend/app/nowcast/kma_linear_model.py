"""Small deterministic linear quantile model used by the FR-01 first implementation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

DEFAULT_WEIGHT_BOUNDS = (
    (-0.5, 0.5),
    (0.0, 1.5),
    (0.0, 1.5),
    (-0.5, 0.5),
    (0.0, 0.0),
)


@dataclass(frozen=True)
class FitResult:
    weights: np.ndarray
    loss: float
    iterations: int


class LinearQuantileRegressor:
    """Fit ``prediction = X @ weights`` with pinball loss and bounded weights."""

    def __init__(
        self,
        quantile: float = 0.5,
        *,
        bounds: tuple[tuple[float, float], ...] = DEFAULT_WEIGHT_BOUNDS,
    ) -> None:
        if not 0.0 < quantile < 1.0:
            raise ValueError("quantile must be between 0 and 1")
        if len(bounds) != 5 or any(lower > upper for lower, upper in bounds):
            raise ValueError("bounds must contain five valid (lower, upper) pairs")
        self.quantile = quantile
        self.bounds = bounds
        self.weights_: np.ndarray | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> FitResult:
        if x.ndim != 2 or x.shape[1] != 5:
            raise ValueError("X must have shape (n, 5)")
        if y.ndim != 1 or len(y) != len(x):
            raise ValueError("y must have shape (n,)")
        if len(x) < 20:
            raise ValueError("at least 20 training rows are required")
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError("training data contains non-finite values")

        def objective(weights: np.ndarray) -> float:
            error = y - x @ weights
            return float(np.mean(np.maximum(self.quantile * error, (self.quantile - 1.0) * error)))

        initial = np.array([0.0, 0.75, 0.20, 0.05, 0.0], dtype=np.float64)
        result = minimize(
            objective,
            initial,
            method="L-BFGS-B",
            bounds=self.bounds,
        )
        if not result.success:
            raise RuntimeError(
                f"q={self.quantile:g} quantile optimization failed: {result.message}"
            )
        self.weights_ = np.asarray(result.x, dtype=np.float64)
        return FitResult(self.weights_.copy(), float(result.fun), int(result.nit))

    def predict_factors(self, x: np.ndarray) -> np.ndarray:
        return np.clip(self.predict_raw_factors(x), 0.0, 1.05)

    def predict_raw_factors(self, x: np.ndarray) -> np.ndarray:
        """Return the unbounded linear output for diagnostics before physical correction."""

        if self.weights_ is None:
            raise RuntimeError("model is not fitted")
        if x.ndim != 2 or x.shape[1] != len(self.weights_):
            raise ValueError("X feature count does not match fitted model")
        return x @ self.weights_


def enforce_monotonic_quantiles(
    raw_p10: np.ndarray, raw_p50: np.ndarray, raw_p90: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Clip physical bounds and enforce P10 <= P50 <= P90 without hiding raw outputs."""

    if raw_p10.shape != raw_p50.shape or raw_p50.shape != raw_p90.shape:
        raise ValueError("quantile predictions must have identical shapes")
    p10 = np.clip(raw_p10, 0.0, 1.05)
    p50 = np.maximum(p10, np.clip(raw_p50, 0.0, 1.05))
    p90 = np.maximum(p50, np.clip(raw_p90, 0.0, 1.05))
    return p10, p50, p90
