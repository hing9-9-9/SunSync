"""Strict save/load contract for frozen FR-01 linear quantile model artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.nowcast.kma_linear_model import LinearQuantileRegressor, enforce_monotonic_quantiles
from app.nowcast.kma_model_dataset import ALLOWED_HORIZON_MINUTES, FEATURE_NAMES

ARTIFACT_SCHEMA_VERSION = "1.0"
MODEL_VERSION = "fr01_kma_quantile_v1"
QUANTILE_LABELS = ("p10", "p50", "p90")
QUANTILES = (0.10, 0.50, 0.90)
FACTOR_BOUNDS = (0.0, 1.05)


class ModelArtifactError(ValueError):
    """Raised when an artifact is incomplete, incompatible, or corrupted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelArtifactError(f"{field} must be an object")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelArtifactError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ModelArtifactError(f"{field} must be finite")
    return number


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelArtifactError(f"{field} must be an integer")
    return value


def _validate_bounds(value: Any, field: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or len(value) != len(FEATURE_NAMES):
        raise ModelArtifactError(f"{field} must contain five bounds")
    bounds: list[tuple[float, float]] = []
    for index, pair in enumerate(value):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ModelArtifactError(f"{field}[{index}] must be [lower, upper]")
        lower = _finite_number(pair[0], f"{field}[{index}][0]")
        upper = _finite_number(pair[1], f"{field}[{index}][1]")
        if lower > upper:
            raise ModelArtifactError(f"{field}[{index}] lower bound exceeds upper bound")
        bounds.append((lower, upper))
    return tuple(bounds)


@dataclass(frozen=True)
class LoadedQuantileArtifact:
    """Validated model bundle for one forecast horizon."""

    path: Path
    horizon_minutes: int
    feature_names: tuple[str, ...]
    models: dict[str, LinearQuantileRegressor]
    document: dict[str, Any]

    def predict_raw_factors(self, features: np.ndarray) -> dict[str, np.ndarray]:
        return {label: self.models[label].predict_raw_factors(features) for label in QUANTILE_LABELS}

    def predict_factors(self, features: np.ndarray) -> dict[str, np.ndarray]:
        raw = self.predict_raw_factors(features)
        p10, p50, p90 = enforce_monotonic_quantiles(raw["p10"], raw["p50"], raw["p90"])
        return {"p10": p10, "p50": p50, "p90": p90}


def load_model_artifact(
    path: Path, *, expected_sha256: str | None = None
) -> LoadedQuantileArtifact:
    """Load one model JSON and fail closed on contract or integrity mismatches."""

    if expected_sha256 is not None and sha256_file(path) != expected_sha256:
        raise ModelArtifactError(f"SHA-256 mismatch: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelArtifactError(f"cannot read model artifact: {path}") from error
    root = _mapping(document, "model")

    if root.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ModelArtifactError("unsupported artifact_schema_version")
    if root.get("model_version") != MODEL_VERSION:
        raise ModelArtifactError("unsupported model_version")
    if root.get("status") != "frozen":
        raise ModelArtifactError("model status must be frozen")
    if root.get("model_type") != "linear_quantile_regression_p10_p50_p90":
        raise ModelArtifactError("unsupported model_type")

    horizon = _integer(root.get("horizon_minutes"), "horizon_minutes")
    if horizon not in ALLOWED_HORIZON_MINUTES:
        raise ModelArtifactError(f"unsupported horizon_minutes: {horizon}")
    feature_names = root.get("feature_names")
    if feature_names != list(FEATURE_NAMES):
        raise ModelArtifactError("feature_names or feature order does not match runtime contract")
    if root.get("feature_units") != {name: "dimensionless" for name in FEATURE_NAMES}:
        raise ModelArtifactError("feature_units do not match runtime contract")
    if root.get("quantiles") != list(QUANTILES):
        raise ModelArtifactError("quantiles must be [0.1, 0.5, 0.9]")
    if root.get("factor_bounds") != list(FACTOR_BOUNDS):
        raise ModelArtifactError("factor_bounds must be [0.0, 1.05]")

    weights_document = _mapping(root.get("weights"), "weights")
    bounds_document = _mapping(root.get("weight_bounds"), "weight_bounds")
    models: dict[str, LinearQuantileRegressor] = {}
    for label, quantile in zip(QUANTILE_LABELS, QUANTILES, strict=True):
        values = weights_document.get(label)
        if not isinstance(values, list) or len(values) != len(FEATURE_NAMES):
            raise ModelArtifactError(f"weights.{label} must contain five values")
        weights = np.asarray(
            [_finite_number(value, f"weights.{label}[{index}]") for index, value in enumerate(values)],
            dtype=np.float64,
        )
        bounds = _validate_bounds(bounds_document.get(label), f"weight_bounds.{label}")
        for index, (weight, (lower, upper)) in enumerate(zip(weights, bounds, strict=True)):
            if not lower - 1e-12 <= weight <= upper + 1e-12:
                raise ModelArtifactError(f"weights.{label}[{index}] is outside its bound")
        model = LinearQuantileRegressor(quantile, bounds=bounds)
        model.weights_ = weights
        models[label] = model

    postprocessing = _mapping(root.get("postprocessing"), "postprocessing")
    expected_rules = {
        "p10": "clip(raw_p10, 0.0, 1.05)",
        "p50": "max(p10, clip(raw_p50, 0.0, 1.05))",
        "p90": "max(p50, clip(raw_p90, 0.0, 1.05))",
    }
    if postprocessing != expected_rules:
        raise ModelArtifactError("postprocessing contract does not match runtime")
    preprocessing = _mapping(root.get("preprocessing_contract"), "preprocessing_contract")
    expected_preprocessing = {
        "version": "fr01_kma_features_v1",
        "dsr_unit": "W/m2 (project assumption recorded in source summary)",
        "dsr_to_dni": "pvlib.irradiance.erbs",
        "reference": "future KMA DSR converted to DNI with pvlib Erbs",
        "reference_type": "dsr_proxy",
        "cloud_patch_used": False,
        "open_meteo_aggregation": "preceding_hour_mean; no linear interpolation",
        "open_meteo_minimum_availability_delay_hours": 7,
        "minimum_solar_elevation_deg": 5.0,
        "direct_prior_observation_weight": "exp(-(horizon_minutes / 60) / 1.5)",
        "advection_impact": "fixed_zero",
    }
    if preprocessing != expected_preprocessing:
        raise ModelArtifactError("preprocessing contract does not match runtime")
    training = _mapping(root.get("training_metadata"), "training_metadata")
    if training.get("diagnostic_used_for_selection") is not False:
        raise ModelArtifactError("diagnostic must not be a model-selection input")
    if training.get("final_test_used") is not False:
        raise ModelArtifactError("final test must be unused at artifact freeze time")
    return LoadedQuantileArtifact(path, horizon, tuple(feature_names), models, root)


def load_model_bundle(root: Path) -> dict[int, LoadedQuantileArtifact]:
    """Load the version manifest and all three hash-verified horizon artifacts."""

    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelArtifactError(f"cannot read model manifest: {manifest_path}") from error
    document = _mapping(manifest, "manifest")
    if document.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ModelArtifactError("unsupported manifest artifact_schema_version")
    if document.get("model_version") != MODEL_VERSION:
        raise ModelArtifactError("unsupported manifest model_version")
    if document.get("status") != "frozen":
        raise ModelArtifactError("manifest status must be frozen")
    if document.get("supported_horizons_minutes") != list(ALLOWED_HORIZON_MINUTES):
        raise ModelArtifactError("manifest supported horizons must be 10, 30, and 60 minutes")
    entries = document.get("models")
    if not isinstance(entries, list) or len(entries) != len(ALLOWED_HORIZON_MINUTES):
        raise ModelArtifactError("manifest must contain exactly three model entries")

    loaded: dict[int, LoadedQuantileArtifact] = {}
    resolved_root = root.resolve()
    for index, entry_value in enumerate(entries):
        entry = _mapping(entry_value, f"models[{index}]")
        horizon = _integer(entry.get("horizon_minutes"), f"models[{index}].horizon_minutes")
        relative = entry.get("path")
        checksum = entry.get("sha256")
        if not isinstance(relative, str) or not relative:
            raise ModelArtifactError(f"models[{index}].path must be a string")
        if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ModelArtifactError(f"models[{index}].sha256 must be a SHA-256 hex digest")
        model_path = (root / relative).resolve()
        if not model_path.is_relative_to(resolved_root):
            raise ModelArtifactError("model path escapes artifact root")
        model = load_model_artifact(model_path, expected_sha256=checksum)
        if model.horizon_minutes != horizon:
            raise ModelArtifactError("manifest/model horizon mismatch")
        if horizon in loaded:
            raise ModelArtifactError(f"duplicate horizon in manifest: {horizon}")
        loaded[horizon] = model
    if tuple(sorted(loaded)) != ALLOWED_HORIZON_MINUTES:
        raise ModelArtifactError("manifest horizons must be 10, 30, and 60 minutes")
    return loaded
