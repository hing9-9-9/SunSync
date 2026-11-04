"""Regression coverage for scene placement, fragment contracts and delayed captures."""

import copy
import importlib.util
import json
import re
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from sunsync_registration.api import app
from sunsync_registration.core import compose_scene, measure
from sunsync_registration.fragment_schema import BoardCameraFragmentV1
from sunsync_registration.fr06 import export_fragment, schema_id
from sunsync_registration.models import MeasureRequest
from sunsync_registration.pose import estimate
from sunsync_registration.session import CaptureSession

spec = importlib.util.spec_from_file_location(
    "review_fixture", Path(__file__).parents[1] / "examples/build_synthetic_example.py"
)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


@pytest.mark.parametrize("enu", [True, False])
def test_scene_recomputes_derived_fields_and_discards_caller_extensions(enu):
    request = example.fixture().model_dump()
    if not enu:
        request["alignment"] = None
        request["floor"] = {"board_origin_height_m": 1.2, "up_board": [0, -1, 0]}
    result = measure(MeasureRequest.model_validate(request))
    original = copy.deepcopy(result["model"])
    result["model"].update(
        base_center_board_m=[99, 99, 99],
        base_center_enu_m=[99, 99, 99],
        yaw_deg_ccw_from_east=180,
        furniture_id="spoofed",
        reference_id="spoofed",
        confirmed=True,
        shape_approximation=False,
        origin="fake_origin",
    )
    scene = compose_scene([result])
    placement = scene["furniture"][0]
    assert placement["furniture_id"] == result["furniture_id"]
    assert placement["reference_id"] == result["reference_id"]
    assert placement["base_center_board_m"] == pytest.approx(
        original["base_center_board_m"]
    )
    assert placement["shape_approximation"] is True
    assert "confirmed" not in placement
    if enu:
        assert placement["base_center_enu_m"] == pytest.approx(
            original["base_center_enu_m"]
        )
        assert placement["yaw_deg_ccw_from_east"] == pytest.approx(
            original["yaw_deg_ccw_from_east"]
        )
    else:
        assert (
            "base_center_enu_m" not in placement
            and "yaw_deg_ccw_from_east" not in placement
        )
    client = TestClient(app)
    assert client.post("/v1/scenes", json={"measurements": [result]}).json() == scene


def test_fragment_rebuilds_placement_and_sanitizes_identifiers():
    request = example.fixture().model_dump()
    request["board"]["board_id"] = "board-한글/1"
    request["plan"]["furniture_id"] = "1desk-한글"
    request["plan"]["space_id"] = "room-한글"
    result = measure(MeasureRequest.model_validate(request))
    expected = export_fragment(result)
    result["model"].update(base_center_enu_m=[10, 20, 30], yaw_deg_ccw_from_east=270)
    fragment = export_fragment(result)
    assert fragment == expected
    identifier = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    assert identifier.fullmatch(fragment["markers"][0]["marker_id"])
    assert identifier.fullmatch(fragment["observations"][0]["observation_id"])
    assert identifier.fullmatch(fragment["occluders"][0]["occluder_id"])
    assert identifier.fullmatch(fragment["occluders"][0]["room_id"])
    assert schema_id("a-b") != schema_id("a_b")
    assert fragment["extensions"]["id_mapping"]["space_id"]["source"] == "room-한글"


def test_fragment_version_and_schema_match_export_and_http_validation():
    fragment = export_fragment(measure(example.fixture()))
    assert fragment["schema_version"] == "sunsync.fr06-fragment/1"
    assert fragment["extensions"]["legacy_fr06_0_2_0_compatible"] is False
    Draft202012Validator(BoardCameraFragmentV1.model_json_schema()).validate(fragment)
    BoardCameraFragmentV1.model_validate(fragment)
    assert fragment["observations"][0]["captured_at"] is not None
    client = TestClient(app)
    assert client.post("/v1/fr06/validate", json=fragment).status_code == 200


def test_published_schemas_are_current():
    root = Path(__file__).parents[1]
    assert (
        json.loads((root / "fr06-fragment.schema.json").read_text(encoding="utf-8"))
        == BoardCameraFragmentV1.model_json_schema()
    )
    assert (
        json.loads((root / "openapi.json").read_text(encoding="utf-8")) == app.openapi()
    )


@pytest.mark.parametrize(
    "field",
    [
        "legacy_version",
        "legacy_method",
        "plane",
        "naive_time",
        "derived_path",
        "points",
    ],
)
def test_fragment_validator_rejects_invalid_contract_fields(field):
    fragment = export_fragment(measure(example.fixture()))
    if field == "legacy_version":
        fragment["schema_version"] = "0.2.0"
    elif field == "legacy_method":
        fragment["observations"][0]["method"] = "aruco_planar"
    elif field == "plane":
        fragment["markers"][0]["plane"] = "window"
    elif field == "naive_time":
        fragment["observations"][0]["captured_at"] = "2026-11-04T12:00:00"
    elif field == "derived_path":
        fragment["observations"][0]["derived_fields"] = [
            fragment["occluders"][0]["occluder_id"] + ".dimensions_m"
        ]
    else:
        fragment["observations"][0]["points_enu_m"][0][0] += 0.1
    with pytest.raises(ValidationError):
        BoardCameraFragmentV1.model_validate(fragment)
    assert TestClient(app).post("/v1/fr06/validate", json=fragment).status_code == 422


def test_generated_paths_reference_only_supported_components():
    fragment = export_fragment(measure(example.fixture()))
    object_id = fragment["occluders"][0]["occluder_id"]
    assert set(fragment["observations"][0]["derived_fields"]) == {
        object_id + ".dimensions_m[0:2]",
        object_id + ".yaw_deg_ccw_from_east",
        object_id + ".base_center_m",
        object_id + ".dimensions_m[2]",
    }
    request = example.fixture().model_dump()
    request["plan"].update(
        floor_supported=False,
        height_mode="manual",
        manual_height_m=0.5,
        manual_base_height_m=0.4,
    )
    floating = export_fragment(measure(MeasureRequest.model_validate(request)))
    assert any(
        p.endswith(".base_center_m")
        for p in floating["observations"][0]["derived_fields"]
    )


def test_fragment_components_match_updated_backend():
    path = Path(__file__).parents[3] / "docs/fr06_scene_schema.json"
    if not path.exists():
        pytest.skip(
            "Legacy backend contract is available only in the parent repository"
        )
    legacy = json.loads(path.read_text(encoding="utf-8"))
    fragment = export_fragment(measure(example.fixture()))
    for kind, value in [
        ("Marker", fragment["markers"][0]),
        ("BoardCameraObservation", fragment["observations"][0]),
    ]:
        schema = {"$defs": legacy["$defs"], "$ref": "#/$defs/" + kind}
        assert not list(Draft202012Validator(schema).iter_errors(value))
    assert fragment["schema_version"] != legacy.get("properties", {}).get(
        "schema_version", {}
    ).get("const")


def detected(request, capture):
    return {
        "detections": [d.model_dump() for d in capture.detections],
        "pose": estimate(request.board, capture.camera, capture.detections),
    }


def test_delayed_point_one_response_never_becomes_point_two():
    request = example.fixture()
    session = CaptureSession(request.plan, request.board)
    captures = request.captures[:3]
    tickets = [session.begin_capture(c.capture_id) for c in captures]
    delayed = session.begin_capture("late-P1")
    for capture, ticket in zip(captures, tickets):
        session.accept(
            capture.capture_id,
            ticket["point_id"],
            capture.camera,
            detected(request, capture),
        )
    assert session.current_step["point_id"] == "P2"
    with pytest.raises(ValueError, match="Stale"):
        session.accept(
            "late-P1",
            delayed["point_id"],
            captures[0].camera,
            detected(request, captures[0]),
        )
    assert len(session.captures) == 3
    assert not any(c.point_id == "P2" for c in session.captures)


def test_capture_ticket_mismatch_and_same_point_retake_are_rejected():
    request = example.fixture()
    session = CaptureSession(request.plan, request.board)
    capture = request.captures[0]
    ticket = session.begin_capture("old")
    with pytest.raises(ValueError, match="ticket"):
        session.accept("old", "P2", capture.camera, detected(request, capture))
    session.retake("P1")
    assert session.current_step["point_id"] == "P1"
    with pytest.raises(ValueError, match="invalidated"):
        session.accept(
            "old", ticket["point_id"], capture.camera, detected(request, capture)
        )
    assert not session.captures
    with pytest.raises(ValueError, match="Duplicate"):
        session.begin_capture("old")
