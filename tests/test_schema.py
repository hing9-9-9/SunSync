"""The published structural schema must follow the API's actual model."""

import json
from pathlib import Path

import pytest
from app.scene_cli import SCHEMA_PATH, schema_document
from app.schemas.scene import Scene
from jsonschema import Draft202012Validator

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


def test_checked_in_schema_matches_model():
    published = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert published == schema_document()
    Draft202012Validator.check_schema(published)


@pytest.mark.parametrize("file", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_examples_match_schema_and_runtime(file):
    data = json.loads(file.read_text(encoding="utf-8"))
    Draft202012Validator(schema_document()).validate(data)
    scene = Scene.model_validate(data)
    Draft202012Validator(schema_document()).validate(scene.model_dump(mode="json"))
    assert Scene.model_validate_json(scene.model_dump_json()) == scene


def test_structural_schema_rejects_unknown_fields_and_invalid_scalar():
    data = json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema_document())
    data["room"]["height_m"] = -1
    data["units"] = "cm"
    data["exposure_budget"] = 20
    errors = list(validator.iter_errors(data))
    assert len(errors) >= 3
