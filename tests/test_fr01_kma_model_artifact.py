from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from app.nowcast.kma_model_artifact import (
    FACTOR_BOUNDS,
    ModelArtifactError,
    load_model_artifact,
    load_model_bundle,
    sha256_file,
)
from app.nowcast.kma_model_dataset import FEATURE_NAMES

ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "app"
    / "nowcast"
    / "model_artifacts"
    / "fr01_kma_quantile_v1"
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_frozen_bundle_loads_all_horizons_and_round_trip_passed() -> None:
    models = load_model_bundle(ARTIFACT_ROOT)
    assert tuple(models) == (10, 30, 60)
    manifest = _read_json(ARTIFACT_ROOT / "manifest.json")
    assert manifest["round_trip_validation"]["status"] == "PASS"
    assert manifest["round_trip_validation"]["maximum_factor_absolute_error"] <= 1e-10
    assert manifest["round_trip_validation"]["maximum_dni_absolute_error_W_m2"] <= 1e-6


@pytest.mark.parametrize("horizon", [10, 30, 60])
def test_loaded_model_uses_frozen_feature_and_physical_contract(horizon: int) -> None:
    model = load_model_bundle(ARTIFACT_ROOT)[horizon]
    assert model.feature_names == FEATURE_NAMES
    features = np.asarray(
        [[1.0, 0.2, 0.4, 0.8, 0.0], [1.0, 0.9, 0.3, 0.1, 0.0]],
        dtype=np.float64,
    )
    predictions = model.predict_factors(features)
    assert np.all(FACTOR_BOUNDS[0] <= predictions["p10"])
    assert np.all(predictions["p10"] <= predictions["p50"])
    assert np.all(predictions["p50"] <= predictions["p90"])
    assert np.all(predictions["p90"] <= FACTOR_BOUNDS[1])


def test_manifest_rejects_model_content_changed_after_packaging(tmp_path: Path) -> None:
    copied = tmp_path / "artifacts"
    shutil.copytree(ARTIFACT_ROOT, copied)
    model_path = copied / "horizon_10" / "model.json"
    document = _read_json(model_path)
    document["weights"]["p50"][0] += 0.01
    _write_json(model_path, document)
    with pytest.raises(ModelArtifactError, match="SHA-256 mismatch"):
        load_model_bundle(copied)


def test_loader_rejects_feature_order_even_with_matching_hash(tmp_path: Path) -> None:
    copied = tmp_path / "artifacts"
    shutil.copytree(ARTIFACT_ROOT, copied)
    model_path = copied / "horizon_10" / "model.json"
    document = _read_json(model_path)
    document["feature_names"][1:3] = reversed(document["feature_names"][1:3])
    _write_json(model_path, document)
    manifest_path = copied / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["models"][0]["sha256"] = sha256_file(model_path)
    _write_json(manifest_path, manifest)
    with pytest.raises(ModelArtifactError, match="feature_names or feature order"):
        load_model_bundle(copied)


def test_loader_rejects_unknown_horizon(tmp_path: Path) -> None:
    model_path = tmp_path / "model.json"
    document = _read_json(ARTIFACT_ROOT / "horizon_10" / "model.json")
    document["horizon_minutes"] = 120
    _write_json(model_path, document)
    with pytest.raises(ModelArtifactError, match="unsupported horizon_minutes"):
        load_model_artifact(model_path)

def test_loader_rejects_nonfinite_weight(tmp_path: Path) -> None:
    model_path = tmp_path / "model.json"
    document = _read_json(ARTIFACT_ROOT / "horizon_10" / "model.json")
    document["weights"]["p10"][0] = float("nan")
    _write_json(model_path, document)
    with pytest.raises(ModelArtifactError, match="must be finite"):
        load_model_artifact(model_path)
