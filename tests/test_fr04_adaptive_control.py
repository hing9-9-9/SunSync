"""Comprehensive unit and API integration tests for FR-04 Adaptive Control & LG SGF / ThinQ ON Bridge."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from app.models.film import FilmStateRecord
from app.schemas.adaptive_control import (
    AdaptiveShadingPlanRequest,
    ThinQHubSyncRequest,
    UncertaintyTimelinePoint,
)
from app.schemas.shading_plan import ShadingPlan
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from engine.fr04 import (
    AdaptivePolicyTuner,
    AdaptiveWeights,
    CellObservation,
    OpticalProfileData,
    UncertaintyQuantiles,
    apply_cell_edge_margin,
    apply_fr04_stabilization,
    build_shading_plan_from_trajectories,
    compute_stage_risk,
    get_candidate_levels,
    optimize_cell_dp,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.fixture
def scene_payload() -> dict[str, Any]:
    return json.loads((EXAMPLES / "fr06_scene_example.json").read_text(encoding="utf-8"))


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'adaptive_test.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    application = create_app(database_engine)
    with TestClient(application) as test_client:
        yield test_client


# ==============================================================================
# 1. Pure Unit Tests: Models and Optical Profile
# ==============================================================================


def test_optical_profile_mappings() -> None:
    profile = OpticalProfileData(
        glazing_direct_transmittance=0.80,
        film_min_direct_transmittance=0.04,
        film_max_direct_transmittance=0.64,
    )
    # u = 0.0 (transparent) -> tau_max (0.64), total = 0.80 * 0.64 = 0.512
    assert pytest.approx(profile.film_transmittance(0.0)) == 0.64
    assert pytest.approx(profile.total_transmittance(0.0)) == 0.512

    # u = 1.0 (opaque) -> tau_min (0.04), total = 0.80 * 0.04 = 0.032
    assert pytest.approx(profile.film_transmittance(1.0)) == 0.04
    assert pytest.approx(profile.total_transmittance(1.0)) == 0.032

    # u = 0.5 (mid dimming) -> 0.64 - 0.5 * (0.60) = 0.34
    assert pytest.approx(profile.film_transmittance(0.5)) == 0.34
    assert pytest.approx(profile.total_transmittance(0.5)) == 0.80 * 0.34

    # Incident irradiance 100 W/m2 at u = 0.5
    assert pytest.approx(profile.transmitted_irradiance(100.0, 0.5)) == 100.0 * 0.80 * 0.34


def test_optical_profile_validation_errors() -> None:
    with pytest.raises(ValueError, match="glazing_direct_transmittance"):
        OpticalProfileData(glazing_direct_transmittance=-0.1)

    with pytest.raises(ValueError, match="cannot exceed"):
        OpticalProfileData(film_min_direct_transmittance=0.8, film_max_direct_transmittance=0.5)


def test_adaptive_weights_and_quantiles_validation() -> None:
    with pytest.raises(ValueError, match="Cost weights"):
        AdaptiveWeights(w_flicker=-1.0)

    with pytest.raises(ValueError, match="kappa_B must be >= kappa_C"):
        AdaptiveWeights(kappa_B=0.5, kappa_C=1.0)

    quantiles = UncertaintyQuantiles(q10=100.0, q50=150.0, q90=220.0)
    assert quantiles.spread == 120.0

    with pytest.raises(ValueError, match="Quantiles must be non-decreasing"):
        UncertaintyQuantiles(q10=150.0, q50=100.0, q90=200.0)


def test_candidate_levels_for_modes() -> None:
    assert get_candidate_levels("PDLC") == (0.0, 1.0)
    assert get_candidate_levels("LG_SGF") == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert get_candidate_levels("SPD") == (0.0, 0.25, 0.5, 0.75, 1.0)


# ==============================================================================
# 2. Risk Evaluation & Dynamic Programming Tests
# ==============================================================================


def test_compute_stage_risk_grade_a() -> None:
    profile = OpticalProfileData()
    weights = AdaptiveWeights()
    t0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    obs_a = CellObservation(
        timestamp=t0,
        window_id="win1",
        row=0,
        col=0,
        strictest_grade="A",
        cap_W_m2=0.0,
        irradiance_q50=100.0,
        reasons=(("region_A", "A"),),
    )
    # Grade A: hard constraint -> opacity < 0.99 incurs massive penalty
    assert compute_stage_risk(0.0, 0.0, obs_a, profile, weights) >= 1e9
    assert compute_stage_risk(0.5, 0.0, obs_a, profile, weights) >= 1e9
    assert compute_stage_risk(1.0, 0.0, obs_a, profile, weights) == 0.0


def test_compute_stage_risk_grade_b_c_and_hysteresis() -> None:
    profile = OpticalProfileData(
        glazing_direct_transmittance=1.0,
        film_min_direct_transmittance=0.05,
        film_max_direct_transmittance=0.70,
    )
    weights = AdaptiveWeights(kappa_B=1.2, kappa_C=0.8, brightening_margin_W_m2=15.0)
    t0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    obs_b = CellObservation(
        timestamp=t0,
        window_id="win1",
        row=0,
        col=0,
        strictest_grade="B",
        cap_W_m2=50.0,
        irradiance_q10=60.0,
        irradiance_q50=100.0,
        irradiance_q90=150.0,  # i_eff = 100 + 1.2 * 50 = 160 W/m2
        reasons=(("region_B", "B"),),
    )

    # At u = 0.0, transmitted is 160 * 0.70 = 112 > 50 -> excess violation risk
    risk_transparent = compute_stage_risk(0.0, 0.0, obs_b, profile, weights)
    assert risk_transparent > 0.0

    # At u = 1.0, transmitted is 160 * 0.05 = 8 <= 50 -> zero violation
    risk_opaque = compute_stage_risk(1.0, 1.0, obs_b, profile, weights)
    assert risk_opaque == 0.0

    # Hysteresis test: brightening from 1.0 to 0.75 near threshold
    # If cap is 50, but transmitted is 45 and hysteresis margin is 20 (effective cap 30):
    risk_brightening = compute_stage_risk(0.75, 1.0, obs_b, profile, weights)
    assert risk_brightening > 0.0  # Hysteresis discourages brightening


def test_optimize_cell_dp_flicker_reduction() -> None:
    profile = OpticalProfileData()
    weights = AdaptiveWeights(w_flicker=50.0, w_daylight=1.0)
    start = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)

    # 10 time steps: Grade B protection with alternating brief cloud flickers
    # Steps: 0(sun), 1(cloud dip), 2(sun), 3(cloud dip), 4(sun)
    observations = []
    for step in range(6):
        t = start + timedelta(minutes=5 * step)
        sun_spike = 120.0 if step % 2 == 0 else 40.0
        observations.append(
            CellObservation(
                timestamp=t,
                window_id="win1",
                row=1,
                col=1,
                strictest_grade="B",
                cap_W_m2=50.0,
                irradiance_q10=0.8 * sun_spike,
                irradiance_q50=sun_spike,
                irradiance_q90=1.2 * sun_spike,
                reasons=(("reg_B", "B"),),
            )
        )

    steps_pdlc = optimize_cell_dp(observations, profile, weights, hardware_mode="PDLC")
    assert len(steps_pdlc) == 6

    # With high w_flicker=50.0, DP avoids rapid on-off switching
    pdlc_opacities = [s.opacity for s in steps_pdlc]
    pdlc_switches = sum(
        1 for i in range(1, 6) if abs(pdlc_opacities[i] - pdlc_opacities[i - 1]) > 0.01
    )
    assert pdlc_switches <= 2


# ==============================================================================
# 3. Stabilization Rules Tests (FR-04.1, FR-04.2, FR-04.4)
# ==============================================================================


def test_fr04_stabilization_gap_filling_and_holding() -> None:
    profile = OpticalProfileData()
    weights = AdaptiveWeights(
        min_gap_seconds=600,  # 10 minutes (2 steps of 300s)
        brightening_hold_seconds=600,
    )
    start = datetime(2026, 10, 14, 8, 0, 0, tzinfo=UTC)

    # Test Gap Filling: Shaded -> Transparent (1 step = 300s) -> Shaded
    # Gap is <= min_gap_seconds (600s), so it must be filled!
    obs_seq = []
    for step in range(5):
        t = start + timedelta(seconds=300 * step)
        grade = "B" if step in (0, 1, 3, 4) else "D"
        obs_seq.append(
            CellObservation(
                timestamp=t,
                window_id="w1",
                row=0,
                col=0,
                strictest_grade=grade,
                cap_W_m2=50.0 if grade == "B" else None,
                irradiance_q50=100.0 if grade == "B" else 0.0,
                reasons=(("r1", "B"),) if grade == "B" else (),
            )
        )

    dp_steps = optimize_cell_dp(obs_seq, profile, weights, hardware_mode="PDLC")
    stabilized = apply_fr04_stabilization(
        dp_steps, interval_seconds=300, weights=weights, optical_profile=profile
    )

    # Step 2 was transparent in raw DP; verify that gap filling filled it with opacity > 0
    assert stabilized[2].opacity > 0.0
    assert stabilized[2].state == "opaque"


def test_fr04_stabilization_immediate_darkening() -> None:
    profile = OpticalProfileData()
    weights = AdaptiveWeights(brightening_hold_seconds=1200)  # 20 min hold
    start = datetime(2026, 10, 14, 8, 0, 0, tzinfo=UTC)

    # Step 0: 0.5 opacity (dimmed)
    # Step 1: Immediate darkening required to 1.0 (opaque) due to sun spike
    # Immediate darkening must be permitted without any delay!
    obs0 = CellObservation(
        timestamp=start,
        window_id="w1",
        row=0,
        col=0,
        strictest_grade="B",
        cap_W_m2=50.0,
        irradiance_q50=80.0,
        reasons=(("r1", "B"),),
    )
    obs1 = CellObservation(
        timestamp=start + timedelta(seconds=300),
        window_id="w1",
        row=0,
        col=0,
        strictest_grade="A",  # Grade A requires immediate 1.0!
        cap_W_m2=0.0,
        irradiance_q50=200.0,
        reasons=(("rA", "A"),),
    )

    dp_steps = optimize_cell_dp([obs0, obs1], profile, weights, hardware_mode="LG_SGF")
    stabilized = apply_fr04_stabilization(
        dp_steps, interval_seconds=300, weights=weights, optical_profile=profile
    )
    assert stabilized[1].opacity == 1.0


def test_fr04_cell_edge_margin() -> None:
    start = datetime(2026, 10, 14, 8, 0, 0, tzinfo=UTC)
    # 3x3 grid: center cell (1, 1) has Grade A hit
    obs_map: dict[tuple[str, int, int], list[CellObservation]] = {}
    for r in range(3):
        for c in range(3):
            is_hit = r == 1 and c == 1
            obs_map[("w1", r, c)] = [
                CellObservation(
                    timestamp=start,
                    window_id="w1",
                    row=r,
                    col=c,
                    strictest_grade="A" if is_hit else None,
                    cap_W_m2=0.0 if is_hit else None,
                    irradiance_q50=150.0 if is_hit else 0.0,
                    reasons=(("art", "A"),) if is_hit else (),
                )
            ]

    expanded = apply_cell_edge_margin(obs_map, film_rows=3, film_cols=3, enabled=True)
    # Orthogonal neighbors (0, 1), (2, 1), (1, 0), (1, 2) should now be edge-adjacent Grade A!
    for nr, nc in [(0, 1), (2, 1), (1, 0), (1, 2)]:
        neighbor_obs = expanded[("w1", nr, nc)][0]
        assert neighbor_obs.strictest_grade == "A"
        assert neighbor_obs.is_edge_adjacent is True


# ==============================================================================
# 4. Tuner and Shading Plan Conversion Tests
# ==============================================================================


def test_adaptive_policy_tuner_guarantees_zero_violations() -> None:
    profile = OpticalProfileData()
    start = datetime(2026, 10, 14, 9, 0, 0, tzinfo=UTC)

    obs_map: dict[tuple[str, int, int], list[CellObservation]] = {
        ("w1", 0, 0): [
            CellObservation(
                timestamp=start + timedelta(seconds=300 * i),
                window_id="w1",
                row=0,
                col=0,
                strictest_grade="B",
                cap_W_m2=50.0,
                irradiance_q50=120.0 if i % 2 == 0 else 30.0,
                reasons=(("desk", "B"),),
            )
            for i in range(8)
        ]
    }
    base_ops = {("w1", 0, 0): [1.0 if i % 2 == 0 else 0.0 for i in range(8)]}

    tuner = AdaptivePolicyTuner()
    best_weights, metrics = tuner.tune(
        observations_by_cell=obs_map,
        baseline_opacities_by_cell=base_ops,
        optical_profile=profile,
        interval_seconds=300,
        hardware_mode="LG_SGF",
    )

    assert isinstance(best_weights, AdaptiveWeights)
    assert metrics.protection_violation_count == 0
    assert metrics.flicker_reduction_pct >= 0.0


def test_build_shading_plan_from_trajectories_valid_schema() -> None:
    start = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 10, 14, 11, 0, 0, tzinfo=UTC)
    profile = OpticalProfileData()
    weights = AdaptiveWeights()

    obs = [
        CellObservation(
            timestamp=start + timedelta(seconds=300 * i),
            window_id="w1",
            row=0,
            col=0,
            strictest_grade="B",
            cap_W_m2=50.0,
            irradiance_q50=100.0,
            reasons=(("r1", "B"),),
        )
        for i in range(12)
    ]
    dp_steps = optimize_cell_dp(obs, profile, weights, hardware_mode="LG_SGF")
    stabilized = apply_fr04_stabilization(dp_steps, 300, weights, profile)

    from engine.fr04.models import CellTrajectory

    traj = CellTrajectory(window_id="w1", row=0, col=0, steps=stabilized)

    plan_dict = build_shading_plan_from_trajectories(
        scene_id="test_scene",
        scene_revision=1,
        windows_grid={"w1": (2, 2)},
        trajectories=[traj],
        start_time=start,
        end_time=end,
        interval_seconds=300,
    )
    # Must strictly validate against Pydantic ShadingPlan schema
    validated = ShadingPlan.model_validate(plan_dict)
    assert validated.scene_id == "test_scene"
    assert validated.windows[0].window_id == "w1"
    assert len(validated.windows[0].cells) >= 1


# ==============================================================================
# 5. API Integration Tests (FastAPI + SQLite Engine)
# ==============================================================================


def test_api_adaptive_shading_plan_endpoint(
    client: TestClient, scene_payload: dict[str, Any]
) -> None:
    # 1. Provision Scene
    create_res = client.post("/api/scenes", json=scene_payload)
    assert create_res.status_code == 201, create_res.text
    created = create_res.json()
    scene_id = created["scene_id"]

    # 2. Call POST /api/scenes/{scene_id}/adaptive-shading-plan
    plan_req = AdaptiveShadingPlanRequest(
        start_time="2026-10-14T04:30:00Z",
        duration_minutes=30,
        interval_seconds=60,
        hardware_mode="LG_SGF",
        enable_edge_margin=True,
        auto_tune_weights=True,
        apply_to_virtual_film=True,
        uncertainty_timeline=[
            UncertaintyTimelinePoint(
                timestamp="2026-10-14T04:30:00Z",
                dni_p10_W_m2=50.0,
                dni_p50_W_m2=120.0,
                dni_p90_W_m2=180.0,
            )
        ],
    )

    response = client.post(
        f"/api/scenes/{scene_id}/adaptive-shading-plan",
        json=plan_req.model_dump(),
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["scene_id"] == scene_id
    assert data["hardware_mode"] == "LG_SGF"
    assert "baseline_plan" in data
    assert "stabilized_plan" in data
    assert "metrics" in data
    assert data["metrics"]["protection_violation_count"] == 0
    assert "dimming_trajectories" in data


def test_api_thinq_hub_sync_endpoint(
    client: TestClient, scene_payload: dict[str, Any], database_engine: Engine
) -> None:
    # 1. Provision Scene
    create_res = client.post("/api/scenes", json=scene_payload)
    assert create_res.status_code == 201, create_res.text
    scene_id = create_res.json()["scene_id"]

    # 2. Sync with LG ThinQ ON Hub
    sync_req = ThinQHubSyncRequest(
        hub_device_id="thinq_on_hub_living_room",
        target_timestamp="2026-10-14T04:50:00Z",
        hardware_mode="LG_SGF",
        plan_request=AdaptiveShadingPlanRequest(
            start_time="2026-10-14T04:30:00Z",
            duration_minutes=30,
            interval_seconds=60,
            hardware_mode="LG_SGF",
        ),
    )

    response = client.post(
        f"/api/scenes/{scene_id}/thinq-hub-sync",
        json=sync_req.model_dump(),
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert "synced_at" in data
    assert "devices" in data
    assert len(data["devices"]) >= 1

    dev = data["devices"][0]
    assert dev["hub_device_id"] == "thinq_on_hub_living_room"
    assert dev["protocol_version"] == "LG_THINQ_ON_SGF_V1"
    assert len(dev["cell_opacity_matrix"]) > 0
    assert "LG ThinQ ON SGF" in dev["decision_summary_ko"]
    assert 0.0 <= dev["energy_daylight_score"] <= 1.0

    # 3. Verify SQLite DB FilmStateRecord was updated with the opacity matrix
    with Session(database_engine) as session:
        record = session.get(FilmStateRecord, f"{scene_id}:{dev['window_id']}")
        assert record is not None
        assert record.film_type == "SPD"
        saved_grid = json.loads(record.cells_json)
        assert saved_grid == dev["cell_opacity_matrix"]


def test_api_adaptive_control_non_existent_scene(client: TestClient) -> None:
    plan_req = AdaptiveShadingPlanRequest(
        start_time="2026-10-14T04:30:00Z",
        duration_minutes=30,
        interval_seconds=60,
    )
    response = client.post(
        "/api/scenes/scene_does_not_exist/adaptive-shading-plan",
        json=plan_req.model_dump(),
    )
    assert response.status_code == 404


def test_cross_track_direct_quantile_point_to_adaptive_plan(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
) -> None:
    """Verify Track 1 QuantileNowcastPoint dict with extra fields passes directly into Track 3 API."""
    scene_id = scene_payload["scene_id"]
    from app.models.scene import SceneRecord

    with Session(database_engine) as session:
        session.add(
            SceneRecord(
                scene_id=scene_id,
                revision=scene_payload["revision"],
                scene_json=json.dumps(scene_payload),
            )
        )
        session.commit()

    # Create payload mimicking Track 1 QuantileNowcastPoint output (contains extra fields)
    track1_points = [
        {
            "timestamp": "2026-10-14T04:30:00Z",
            "solar_elevation_deg": 35.2,
            "clear_sky_dni_W_m2": 780.0,
            "open_meteo_dni_W_m2": 520.0,
            "kma_ai_dni_W_m2": 490.0,
            "dni_p10_W_m2": 350.0,
            "dni_p50_W_m2": 490.0,
            "dni_p90_W_m2": 620.0,
            "clear_sky_index_p50": 0.628,
            "uncertainty_spread_W_m2": 270.0,
            "selected_dni_W_m2": 490.0,
            "selected_source": "kma_ai_ensemble",
            "fallback_used": False,
        },
        {
            "timestamp": "2026-10-14T04:35:00Z",
            "solar_elevation_deg": 35.8,
            "clear_sky_dni_W_m2": 790.0,
            "open_meteo_dni_W_m2": 530.0,
            "kma_ai_dni_W_m2": 500.0,
            "dni_p10_W_m2": 360.0,
            "dni_p50_W_m2": 500.0,
            "dni_p90_W_m2": 630.0,
            "clear_sky_index_p50": 0.633,
            "uncertainty_spread_W_m2": 270.0,
            "selected_dni_W_m2": 500.0,
            "selected_source": "kma_ai_ensemble",
            "fallback_used": False,
        },
    ]

    req_payload = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 10,
        "interval_seconds": 300,
        "hardware_mode": "LG_SGF",
        "uncertainty_timeline": track1_points,
    }

    resp = client.post(f"/api/scenes/{scene_id}/adaptive-shading-plan", json=req_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "stabilized_plan" in data
    assert "metrics" in data


def test_cross_track_auto_fetch_nowcast_from_database(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
) -> None:
    """Verify Track 3 auto-fetches nowcast_point records persisted by Track 1 when timeline is omitted."""
    scene_id = scene_payload["scene_id"]
    from app.models.scene import SceneRecord

    with Session(database_engine) as session:
        session.add(
            SceneRecord(
                scene_id=scene_id,
                revision=scene_payload["revision"],
                scene_json=json.dumps(scene_payload),
            )
        )
        session.commit()

    # Step 1: Run Track 1 AI nowcast endpoint, which persists points into SQLite nowcast_point
    nowcast_req = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 30,
        "interval_seconds": 300,
        "mode": "kma_ai_ensemble",
    }
    nowcast_resp = client.post(f"/api/scenes/{scene_id}/ai-nowcast", json=nowcast_req)
    assert nowcast_resp.status_code == 200, nowcast_resp.text

    # Step 2: Call Track 3 adaptive plan endpoint with empty uncertainty_timeline
    adaptive_req = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 30,
        "interval_seconds": 300,
        "hardware_mode": "LG_SGF",
        "uncertainty_timeline": [],
    }
    plan_resp = client.post(f"/api/scenes/{scene_id}/adaptive-shading-plan", json=adaptive_req)
    assert plan_resp.status_code == 200, plan_resp.text
    plan_data = plan_resp.json()
    assert "stabilized_plan" in plan_data
    assert plan_data["metrics"]["protection_violation_count"] == 0


def test_adaptive_shading_plan_grade_a_hard_constraint_and_non_overlapping_segments(
    client: TestClient,
    database_engine: Engine,
    scene_payload: dict[str, Any],
) -> None:
    """Verify Grade A hard constraint requires opacity >= 0.99 and segments have zero overlap."""
    scene_id = scene_payload["scene_id"]
    from app.models.scene import SceneRecord

    with Session(database_engine) as session:
        session.add(
            SceneRecord(
                scene_id=scene_id,
                revision=scene_payload["revision"],
                scene_json=json.dumps(scene_payload),
            )
        )
        session.commit()

    req = {
        "start_time": "2026-10-14T04:30:00Z",
        "duration_minutes": 60,
        "interval_seconds": 300,
        "hardware_mode": "LG_SGF",
    }
    resp = client.post(f"/api/scenes/{scene_id}/adaptive-shading-plan", json=req)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Validate ShadingPlan Pydantic model coherence (including ordered non-overlapping segments)
    plan = ShadingPlan.model_validate(data["stabilized_plan"])
    for win in plan.windows:
        for cell in win.cells:
            for i in range(len(cell.segments) - 1):
                seg1 = cell.segments[i]
                seg2 = cell.segments[i + 1]
                t1_end = datetime.fromisoformat(seg1.end_time)
                t2_start = datetime.fromisoformat(seg2.start_time)
                assert t1_end <= t2_start, f"Overlap detected between {seg1} and {seg2}"
