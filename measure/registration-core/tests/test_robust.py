"""Fixed-seed held-out synthetic observations; truth is never sent to the solver."""

import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from sunsync_registration.core import measure
from sunsync_registration.fr06 import export_fragment
from sunsync_registration.models import MeasureRequest, SelectRequest
from sunsync_registration.pose import estimate, point_covariance
from sunsync_registration.selection import select_observations

spec = importlib.util.spec_from_file_location(
    "robust_fixture", Path(__file__).parents[1] / "examples/build_synthetic_example.py"
)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


noisy_spec = importlib.util.spec_from_file_location(
    "noisy_fixture", Path(__file__).parents[1] / "examples/build_noisy_fixture.py"
)
noisy_module = importlib.util.module_from_spec(noisy_spec)
noisy_spec.loader.exec_module(noisy_module)
noisy_fixture = noisy_module.noisy_fixture


@pytest.mark.parametrize("kind", ["rectangle", "circle", "ellipse", "polygon"])
def test_joint_shapes_roundtrip_with_noisy_independent_frames(kind):
    data, truth = noisy_fixture(kind=kind)
    result = measure(MeasureRequest.model_validate(data))
    assert result["status"] == "model_ready_unreviewed", result["required_actions"]
    assert result["optimization"]["status"] == "converged"
    assert result["optimization"]["final_cost"] <= result["optimization"]["initial_cost"] + 1e-6
    estimated = np.array(
        [result["points"][f"P{i + 1}"]["point_board_m"] for i in range(len(truth))]
    )
    assert np.max(np.linalg.norm(estimated - truth, axis=1)) < 0.025
    assert export_fragment(result)["observations"][0]["tabletop_shape"] == kind


def test_burst_rejects_majority_incompatible_or_duplicate_frames():
    data, _ = noisy_fixture(outlier=True)
    burst = SelectRequest(board=data["board"], captures=data["captures"][:8])
    chosen = select_observations(burst)
    assert chosen["status"] == "ok"
    assert "P1-7" not in chosen["selected_capture_ids"]
    assert chosen["raw_samples"] == 8 and 3 <= chosen["selected_samples"] < 8
    repeated = burst.model_dump()
    for c in repeated["captures"]:
        c["image_sha256"] = "a" * 64
    rejected = select_observations(SelectRequest.model_validate(repeated))
    assert rejected["status"] == "retake"
    assert rejected["selected_samples"] == 0
    assert sum(d["status"] == "duplicate_image" for d in rejected["diagnostics"]) == 7


def test_repeated_identical_views_do_not_shrink_uncertainty():
    base = example.fixture(single=True).model_dump()

    def quality(count):
        frames = [
            {**copy.deepcopy(base["captures"][0]), "capture_id": str(i)} for i in range(count)
        ]
        return select_observations(SelectRequest(board=base["board"], captures=frames))["quality"]

    a, b = quality(3), quality(12)
    assert a["independent_view_groups"] == b["independent_view_groups"] == 1
    assert b["uncertainty_m"] == pytest.approx(a["uncertainty_m"], rel=1e-6)


def test_uncertainty_finite_symmetric_and_applies_contact_offset():
    base = example.fixture(single=True)
    c = base.captures[0]
    pose = estimate(base.board, c.camera, c.detections)
    covariance = point_covariance(pose, [0, 0.1, 0])
    assert np.allclose(covariance, covariance.T)
    assert np.linalg.eigvalsh(covariance).min() > 0
    assert not np.allclose(covariance, point_covariance(pose, [0, 0, 0]), rtol=1e-4, atol=1e-10)


def test_uncertain_pose_is_not_forced_into_a_model():
    data, _ = noisy_fixture()
    data["max_point_uncertainty_m"] = 1e-8
    result = measure(MeasureRequest.model_validate(data))
    assert result["model"] is None
    assert any(d["status"] == "pose_uncertainty_too_large" for d in result["capture_diagnostics"])


def test_existing_median_and_weighted_modes_remain_available_for_comparison():
    data, _ = noisy_fixture()
    for mode in ["legacy", "weighted"]:
        result = measure(MeasureRequest.model_validate({**data, "estimation_mode": mode}))
        assert result["status"] == "model_ready_unreviewed", result["required_actions"]
        assert result["optimization"] is None


def test_selection_never_joins_different_points():
    data, _ = noisy_fixture()
    with pytest.raises(ValueError, match="one point"):
        select_observations(SelectRequest(board=data["board"], captures=data["captures"][:9]))


def test_burst_tickets_keep_shutter_identity_and_invalidate_on_retake():
    from sunsync_registration.session import CaptureSession

    data, _ = noisy_fixture()
    request = MeasureRequest.model_validate(data)
    session = CaptureSession(request.plan, request.board)
    captures = request.captures[:8]
    for c in captures:
        session.begin_capture(c.capture_id, c.captured_at)
    changed = captures[0].model_copy(update={"captured_at": "2026-11-10T01:00:01Z"})
    with pytest.raises(ValueError, match="ticket"):
        session.accept_burst([changed, *captures[1:]])
    result = session.accept_burst(captures)
    assert result["status"] == "ok" and session.current_step["point_id"] == "P2"
    next_capture = request.captures[8]
    session.begin_capture(next_capture.capture_id, next_capture.captured_at)
    session.retake("P1")
    with pytest.raises(ValueError, match="ticket"):
        session.accept_burst([next_capture])


def test_noise_benchmark_uses_fixed_held_out_seeds_and_reports_rejections():
    errors = {mode: [] for mode in ["legacy", "weighted", "robust_joint"]}
    for seed in range(800, 810):
        data, _ = noisy_fixture(seed=seed, noise=0.3)
        for mode, values in errors.items():
            result = measure(MeasureRequest.model_validate({**data, "estimation_mode": mode}))
            assert result["model"] is not None, result["required_actions"]
            values.append(np.abs(np.array(result["model"]["dimensions_m"][:2]) - [0.6, 0.45]))
    assert np.mean(errors["robust_joint"]) < np.mean(errors["legacy"])


def test_select_api_and_final_measurement_apply_same_policy():
    from fastapi.testclient import TestClient
    from sunsync_registration.api import app

    data, _ = noisy_fixture(outlier=True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/select",
            json={"board": data["board"], "captures": data["captures"][:8]},
        )
    assert response.status_code == 200
    final = measure(MeasureRequest.model_validate(data))
    assert response.json()["selected_capture_ids"] == final["points"]["P1"]["selected_capture_ids"]
    assert final["points"]["P1"]["raw_samples"] == 8
    assert all(
        d["selected"] is False
        for d in final["capture_diagnostics"]
        if d["capture_id"].endswith("-7")
    )


def test_selection_limit_cannot_satisfy_more_required_samples():
    data, _ = noisy_fixture()
    result = select_observations(
        SelectRequest(
            board=data["board"], captures=data["captures"][:8], required_samples=5, max_selected=3
        )
    )
    assert result["status"] == "retake"
    assert result["selected_samples"] == 0
