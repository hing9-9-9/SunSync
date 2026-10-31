"""Run isolated FR-06/FR-01 mock scenarios without touching production SQLite."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from math import cos, radians, sin
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import create_app
from app.services import sunpath as sunpath_service
from fastapi.testclient import TestClient
from sqlmodel import create_engine

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARTIFACT_ROOT = ROOT / "artifacts" / "mock_validation"
SOLAR_TOLERANCE_DEG = 0.10
VECTOR_TOLERANCE = 0.002
GEOMETRY_TOLERANCE_M = 0.02
KST = ZoneInfo("Asia/Seoul")


@dataclass
class ScenarioResult:
    scenario: str
    outcome: str
    detail: str


class NumericalValidationError(AssertionError):
    """The API worked, but an independent numerical oracle did not match."""


def _offline_open_meteo_snapshot(
    *_args: object, **_kwargs: object
) -> dict[datetime, tuple[float | None, datetime, datetime]]:
    """Keep geometric regression fixtures deterministic and network-free."""

    raise sunpath_service.OpenMeteoError("open_meteo_transport_error")


def _quality() -> dict[str, Any]:
    return {"observation_ids": ["mock_obs_manual"], "confirmed": True}


def _policy() -> dict[str, Any]:
    return {
        "policy_id": "mock_policy",
        "version": "0.2.0",
        "status": "proposal_demo_only",
        "metric": "direct_solar_irradiance_W_m2",
        "aggregation": "max_over_sample_points",
        "threshold_status": "synthetic_demo_not_agreed",
        "max_time_step_s": 60,
        "brightening_margin_W_m2": 5,
        "brightening_hold_s": 60,
        "grades": {
            "A": {
                "rank": 1,
                "constraint": "hard",
                "rule": "block_geometric_direct_paths",
                "max_direct_irradiance_W_m2": 0,
            },
            "B": {
                "rank": 2,
                "constraint": "soft",
                "rule": "intensity_cap",
                "max_direct_irradiance_W_m2": 20,
            },
            "C": {
                "rank": 3,
                "constraint": "soft",
                "rule": "intensity_cap",
                "max_direct_irradiance_W_m2": 80,
            },
            "D": {
                "rank": 4,
                "constraint": "none",
                "rule": "daylight_priority",
                "max_direct_irradiance_W_m2": None,
            },
        },
    }


def build_scene(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a complete FR-06 document from mock-only declarative fixture metadata."""

    scenario = spec["scenario"]
    scene = {
        "schema_version": "0.2.0",
        "scene_id": scenario,
        "revision": 1,
        "data_kind": "synthetic_fixture",
        "created_at": "2026-10-17T03:30:00Z",
        "units": "m",
        "coordinate_frame": "ENU",
        "site": {
            "latitude_deg": 37.5665,
            "longitude_deg": 126.9780,
            "timezone": "Asia/Seoul",
            "source": "mock_seoul_city_hall_reference",
        },
        "north_alignment": {
            "source": "true_heading",
            "accuracy_deg": 0.0,
            "confirmed": True,
            "note": "Mock ENU axes aligned to true north.",
        },
        "room": {
            "room_id": "mock_room_main",
            "name": "Mock room",
            "floor_vertices_m": [[0, 0, 0], [10, 0, 0], [10, 5, 0], [0, 5, 0]],
            "height_m": 3.0,
            "quality": _quality(),
        },
        "windows": [
            {
                "window_id": "mock_window_south",
                "room_id": "mock_room_main",
                "vertices_m": [[10, 0, 0], [0, 0, 0], [0, 0, 3], [10, 0, 3]],
                "inward_normal": [0, 1, 0],
                "u_axis": [-1, 0, 0],
                "v_axis": [0, 0, 1],
                "azimuth_deg_true": 180.0,
                "optical_profile_id": "mock_optics",
                "initial_film_direct_transmittance": 1.0,
                "quality": _quality(),
            }
        ],
        "occluders": [],
        "protected_regions": [
            {
                "region_id": "mock_region_target",
                "room_id": "mock_room_main",
                "name": "Mock target",
                "host_occluder_id": None,
                "vertices_m": [[4.9, 1, 0.9], [5.1, 1, 0.9], [5.1, 1, 1.1], [4.9, 1, 1.1]],
                "receiving_normal": [0, -1, 0],
                "grade": "A",
                "policy_id": "mock_policy",
                "quality": _quality(),
            }
        ],
        "observations": [
            {
                "observation_id": "mock_obs_manual",
                "method": "manual",
                "captured_at": "2026-10-17T03:30:00Z",
                "uncertainty_m": 0.0,
                "confirmed": True,
                "note": "Mock-only analytic geometry.",
            }
        ],
        "optical_profiles": [
            {
                "optical_profile_id": "mock_optics",
                "status": "assumed",
                "model": "direct_scalar_transmittance",
                "glazing_direct_transmittance": 1.0,
                "film_min_direct_transmittance": 0.0,
                "film_max_direct_transmittance": 1.0,
                "actual_sgf_properties": None,
            }
        ],
        "policy": _policy(),
    }
    if spec["kind"] == "occluder_blocked":
        scene["occluders"] = [
            {
                "occluder_id": "mock_occluder_blocker",
                "room_id": "mock_room_main",
                "name": "Mock blocker",
                "kind": "cuboid",
                "base_center_m": [4.8, 0.5, 0],
                "dimensions_m": [0.4, 0.4, 3.0],
                "yaw_deg_ccw_from_east": 0.0,
                "material": "opaque",
                "quality": _quality(),
            }
        ]
    if spec["kind"] == "multi_sample_temporal":
        scene["protected_regions"] = [
            {
                "region_id": "mock_region_multi_sample",
                "room_id": "mock_room_main",
                "name": "Mock multi-sample target",
                "host_occluder_id": None,
                "vertices_m": [[4.8, 1, 0.2], [5.2, 1, 0.2], [5.2, 1, 2.2], [4.8, 1, 2.2]],
                "receiving_normal": [0, -1, 0],
                "grade": "A",
                "policy_id": "mock_policy",
                "quality": _quality(),
            }
        ]
    if spec["kind"] == "invalid_window_geometry":
        scene["windows"][0]["vertices_m"][2] = [0, 0, 2.5]
    return scene


def _load_specs(selected: str | None) -> list[tuple[Path, dict[str, Any]]]:
    paths = sorted(FIXTURES.glob("valid/*.json")) + sorted(FIXTURES.glob("invalid/*.json"))
    result: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        spec = json.loads(path.read_text(encoding="utf-8"))
        if selected is None or spec["scenario"] == selected:
            result.append((path, spec))
    if not result:
        raise ValueError(f"unknown scenario: {selected}")
    return result


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_instant(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _format_point(point: list[float] | tuple[float, ...]) -> str:
    return "(" + ", ".join(f"{value:.3f}" for value in point) + ") m"


def _format_vector(vector: list[float] | tuple[float, ...]) -> str:
    return "(" + ", ".join(f"{value:.6f}" for value in vector) + ")"


def _format_kst(value: str | datetime) -> str:
    instant = datetime.fromisoformat(value) if isinstance(value, str) else value
    return instant.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _verbose_scene(scene: dict[str, Any]) -> list[str]:
    """Render only fixture geometry supplied to the API, in the scene's ENU metres."""

    room = scene["room"]
    lines = [
        "  space (scene input; ENU, m):",
        f"    room: {room['room_id']} (height: {room['height_m']:.3f} m)",
        "      floor vertices:",
    ]
    lines.extend(
        f"        [{index}] {_format_point(point)}"
        for index, point in enumerate(room["floor_vertices_m"])
    )
    for window in scene["windows"]:
        lines.extend([f"    window: {window['window_id']}", "      vertices:"])
        lines.extend(
            f"        [{index}] {_format_point(point)}"
            for index, point in enumerate(window["vertices_m"])
        )
    for region in scene["protected_regions"]:
        lines.extend([f"    protected region: {region['region_id']}", "      vertices:"])
        lines.extend(
            f"        [{index}] {_format_point(point)}"
            for index, point in enumerate(region["vertices_m"])
        )
    for occluder in scene["occluders"]:
        lines.extend(
            [
                f"    occluder: {occluder['occluder_id']}",
                f"      base center: {_format_point(occluder['base_center_m'])}",
                f"      dimensions: {_format_point(occluder['dimensions_m'])}",
                f"      yaw: {occluder['yaw_deg_ccw_from_east']:.2f}° CCW from east",
            ]
        )
    return lines


def _verbose_prediction(payload: dict[str, Any]) -> list[str]:
    """Render response diagnostics without deriving values absent from the contract."""

    positions = {position["timestamp"]: position for position in payload["solar_positions"]}
    lines = ["  prediction (API response; timestamps shown in KST):"]
    for region in payload["regions"]:
        first = region["first_predicted_exposure_time"]
        first_text = _format_kst(first) if first is not None else "none"
        lines.append(
            f"    region: {region['region_id']} (exposed at start: {region['exposed_at_start']}, first exposure: {first_text})"
        )
        for entry in region["timeline"]:
            position = positions[entry["timestamp"]]
            lines.extend(
                [
                    f"      time: {_format_kst(entry['timestamp'])}",
                    f"        sun: elevation {position['elevation_deg']:.3f}°, azimuth (true) {position['azimuth_deg_true']:.3f}°",
                    f"        sun direction (ENU): {_format_vector(position['sun_direction_enu'])}",
                    f"        result: {entry['status']} ({entry['exposed_sample_count']} exposed sample(s))",
                ]
            )
            for sample in entry["samples"]:
                lines.extend(
                    [
                        f"        sample[{sample['sample_index']}]: {sample['status']} ({sample['reason']})",
                        f"          target point: {_format_point(sample['point_m'])}",
                    ]
                )
                path = sample["candidate_path"]
                if path is not None:
                    lines.extend(
                        [
                            f"          window: {path['window_id']}",
                            f"            hit: {_format_point(path['window_hit_m'])} (distance: {path['sample_to_window_distance_m']:.3f} m)",
                            f"            ray to sun (ENU): {_format_vector(path['reverse_direction_enu'])}",
                            f"            incoming ray (ENU): {_format_vector(path['incoming_direction_enu'])}",
                        ]
                    )
                blocker = sample["blocker"]
                if blocker is not None:
                    lines.extend(
                        [
                            f"          blocker: {blocker['object_id']} ({blocker['kind']})",
                            f"            hit: {_format_point(blocker['hit_m'])} (distance: {blocker['distance_m']:.3f} m)",
                        ]
                    )
    return lines


def _close_enough(actual: list[float], expected: list[float], tolerance: float) -> bool:
    return len(actual) == len(expected) and all(
        abs(actual_value - expected_value) <= tolerance
        for actual_value, expected_value in zip(actual, expected, strict=True)
    )


def _artifact_dir() -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    result = ARTIFACT_ROOT / run_id
    result.mkdir(parents=True, exist_ok=False)
    return result


def _expect(response: Any, status: int, context: str) -> dict[str, Any]:
    if response.status_code != status:
        raise AssertionError(
            f"{context}: expected HTTP {status}, got {response.status_code}: {response.text}"
        )
    return response.json()


def _validate_numerics(
    payload: dict[str, Any], spec: dict[str, Any], verbose: bool, scene: dict[str, Any]
) -> str:
    expected = spec["expected"]
    position = payload["solar_positions"][0]
    reference = spec.get("solar_reference")
    if reference is not None:
        if abs(position["elevation_deg"] - reference["elevation_deg"]) > SOLAR_TOLERANCE_DEG:
            raise NumericalValidationError("solar elevation differs from independent reference")
        if abs(position["azimuth_deg_true"] - reference["azimuth_deg_true"]) > SOLAR_TOLERANCE_DEG:
            raise NumericalValidationError("solar azimuth differs from independent reference")
        elevation = radians(reference["elevation_deg"])
        azimuth = radians(reference["azimuth_deg_true"])
        expected_vector = [
            cos(elevation) * sin(azimuth),
            cos(elevation) * cos(azimuth),
            sin(elevation),
        ]
        if not _close_enough(position["sun_direction_enu"], expected_vector, VECTOR_TOLERANCE):
            raise NumericalValidationError("solar ENU vector differs from independent reference")
    sample = payload["regions"][0]["timeline"][0]["samples"][0]
    if sample["status"] != expected["status"] or sample["reason"] != expected["reason"]:
        raise NumericalValidationError(
            f"sample result differs: {sample['status']} / {sample['reason']}"
        )
    if "window_id" in expected:
        path = sample["candidate_path"]
        if path is None or path["window_id"] != expected["window_id"]:
            raise NumericalValidationError("expected candidate window is missing")
        if not _close_enough(path["window_hit_m"], expected["window_hit_m"], GEOMETRY_TOLERANCE_M):
            raise NumericalValidationError(
                "window hit differs from independent analytic coordinate"
            )
    if "blocker_id" in expected:
        blocker = sample["blocker"]
        if blocker is None or blocker["object_id"] != expected["blocker_id"]:
            raise NumericalValidationError("expected blocker is missing")
        if not _close_enough(blocker["hit_m"], expected["blocker_hit_m"], GEOMETRY_TOLERANCE_M):
            raise NumericalValidationError(
                "blocker hit differs from independent analytic coordinate"
            )
    actual_first = _parse_instant(payload["regions"][0]["first_predicted_exposure_time"])
    expected_first = _parse_instant(expected["first_predicted_exposure_time"])
    if actual_first != expected_first:
        raise NumericalValidationError("first predicted exposure timestamp differs")
    if spec["kind"] == "no_exposure" and any(
        timeline_entry["samples"][0]["reason"] != "sun_below_horizon"
        for timeline_entry in payload["regions"][0]["timeline"]
    ):
        raise NumericalValidationError("night fixture contains a non-night sample")
    detail = f"{sample['status']} / {sample['reason']}"
    if not verbose:
        return detail
    return "\n".join([detail, *_verbose_scene(scene), *_verbose_prediction(payload)])


def _validate_multi_sample_temporal(
    payload: dict[str, Any], spec: dict[str, Any], verbose: bool, scene: dict[str, Any]
) -> str:
    """Check independent grid and rectangular-aperture invariants for the Seoul fixture."""

    expected = spec["expected"]
    timestamps = payload["timestamps"]
    if len(timestamps) != expected["timestamp_count"]:
        raise NumericalValidationError("timestamp count differs from the requested 2-hour grid")
    parsed_instants = [_parse_instant(value) for value in timestamps]
    if any(instant is None for instant in parsed_instants):
        raise NumericalValidationError("prediction returned a null timestamp")
    instants: list[datetime] = [instant for instant in parsed_instants if instant is not None]
    if any(
        (following - preceding).total_seconds() != expected["interval_seconds"]
        for preceding, following in pairwise(instants)
    ):
        raise NumericalValidationError(
            "timestamps are not spaced at the requested 10-minute interval"
        )

    regions = payload["regions"]
    if len(regions) != 1 or regions[0]["region_id"] != "mock_region_multi_sample":
        raise NumericalValidationError("multi-sample region response is missing")
    region = regions[0]
    if _parse_instant(region["first_predicted_exposure_time"]) != _parse_instant(
        expected["first_predicted_exposure_time"]
    ):
        raise NumericalValidationError("first predicted exposure timestamp differs")
    if not region["exposed_at_start"]:
        raise NumericalValidationError("fixture must be exposed at the first requested timestamp")
    if len(region["timeline"]) != len(timestamps):
        raise NumericalValidationError("region timeline does not cover every timestamp")

    counts: list[int] = []
    for entry in region["timeline"]:
        samples = entry["samples"]
        if len(samples) != expected["sample_count"]:
            raise NumericalValidationError("a timestamp does not include every protected sample")
        if [sample["sample_index"] for sample in samples] != list(range(expected["sample_count"])):
            raise NumericalValidationError("sample indexes are not stable")
        exposed = [sample for sample in samples if sample["status"] == "exposed"]
        if entry["exposed_sample_count"] != len(exposed):
            raise NumericalValidationError("exposed_sample_count does not equal sample statuses")
        if entry["status"] != ("exposed" if exposed else "not_exposed"):
            raise NumericalValidationError("region aggregation differs from sample statuses")
        exposed_indexes = [sample["sample_index"] for sample in exposed]
        if exposed_indexes != list(range(len(exposed_indexes))):
            raise NumericalValidationError(
                "vertical samples are not exposed as a lower-height prefix"
            )
        for sample in samples:
            path = sample["candidate_path"]
            if sample["status"] == "exposed":
                if path is None or path["window_id"] != expected["window_id"]:
                    raise NumericalValidationError("exposed sample lacks the expected window path")
                hit = path["window_hit_m"]
                if abs(hit[1]) > GEOMETRY_TOLERANCE_M or not (
                    0.0 < hit[0] < 10.0 and 0.0 < hit[2] < 3.0
                ):
                    raise NumericalValidationError(
                        "window hit lies outside the independent aperture bounds"
                    )
            elif sample["reason"] != "window_missed" or path is not None:
                raise NumericalValidationError(
                    "non-exposed multi-sample result is not an aperture miss"
                )
        counts.append(len(exposed))
    if expected["requires_exposure_count_change"] and len(set(counts)) < 2:
        raise NumericalValidationError("fixture did not show an exposure-set change over time")

    detail = "exposed samples by timestamp: " + ", ".join(str(count) for count in counts)
    if not verbose:
        return detail
    return "\n".join([detail, *_verbose_scene(scene), *_verbose_prediction(payload)])


def _run_valid(
    client: TestClient, spec: dict[str, Any], scenario_dir: Path, verbose: bool
) -> ScenarioResult:
    scene = build_scene(spec)
    _write_json(scenario_dir / "request.json", {"scene": scene, "prediction": spec["request"]})
    try:
        _expect(client.post("/api/scenes/validate", json=scene), 200, "scene validation")
        _expect(client.post("/api/scenes", json=scene), 201, "scene creation")
        before = _expect(client.get(f"/api/scenes/{scene['scene_id']}"), 200, "scene before")
        response = _expect(
            client.post(
                f"/api/scenes/{scene['scene_id']}/sunpath-predictions",
                json={**spec["request"], "expected_scene_revision": 1},
            ),
            200,
            "prediction",
        )
        _write_json(scenario_dir / "response.json", response)
        detail = (
            _validate_multi_sample_temporal(response, spec, verbose, scene)
            if spec["kind"] == "multi_sample_temporal"
            else _validate_numerics(response, spec, verbose, scene)
        )
        after = _expect(client.get(f"/api/scenes/{scene['scene_id']}"), 200, "scene after")
        _write_json(scenario_dir / "scene_before.json", before)
        _write_json(scenario_dir / "scene_after.json", after)
        if before != after:
            raise AssertionError("prediction mutated stored Scene")
        return ScenarioResult(spec["scenario"], "PASS", detail)
    except NumericalValidationError as error:
        return ScenarioResult(spec["scenario"], "NUMERICAL_FAIL", str(error))
    except AssertionError as error:
        return ScenarioResult(spec["scenario"], "API_FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - record setup failures per scenario
        return ScenarioResult(spec["scenario"], "SETUP_FAIL", repr(error))


def _run_invalid(client: TestClient, spec: dict[str, Any], scenario_dir: Path) -> ScenarioResult:
    scene = build_scene(spec)
    _write_json(scenario_dir / "request.json", {"scene": scene})
    try:
        response = client.post("/api/scenes/validate", json=scene)
        if response.status_code != spec["expected"]["validate_status"]:
            raise AssertionError(
                f"validate expected {spec['expected']['validate_status']}, got {response.status_code}"
            )
        persisted = client.get(f"/api/scenes/{scene['scene_id']}").status_code != 404
        if persisted:
            raise AssertionError("invalid fixture was persisted")
        return ScenarioResult(spec["scenario"], "PASS", "validate=422, persisted=false")
    except AssertionError as error:
        return ScenarioResult(spec["scenario"], "API_FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - record setup failures per scenario
        return ScenarioResult(spec["scenario"], "SETUP_FAIL", repr(error))


def _run_contract_checks(client: TestClient) -> list[ScenarioResult]:
    scene = build_scene({"scenario": "mock_scene_contract", "kind": "clear_path"})
    _expect(client.post("/api/scenes", json=scene), 201, "contract scene creation")
    path = f"/api/scenes/{scene['scene_id']}/sunpath-predictions"
    request = {
        "start_time": "2003-10-17T12:30:30-07:00",
        "duration_minutes": 1,
        "interval_seconds": 60,
    }
    checks = [
        (
            "mock_contract_missing_scene",
            client.post("/api/scenes/missing/sunpath-predictions", json=request),
            404,
        ),
        (
            "mock_contract_revision_mismatch",
            client.post(path, json={**request, "expected_scene_revision": 2}),
            409,
        ),
        (
            "mock_contract_unknown_region",
            client.post(path, json={**request, "protected_region_ids": ["mock_missing"]}),
            422,
        ),
        (
            "mock_contract_duplicate_region",
            client.post(
                path,
                json={
                    **request,
                    "protected_region_ids": ["mock_region_target", "mock_region_target"],
                },
            ),
            422,
        ),
    ]
    return [
        ScenarioResult(
            name,
            "PASS" if response.status_code == expected else "API_FAIL",
            f"HTTP {response.status_code}",
        )
        for name, response, expected in checks
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    try:
        specs = _load_specs(args.scenario)
        artifact_dir = _artifact_dir()
    except Exception as error:  # noqa: BLE001 - report fixture/artifact setup failure
        print(f"SETUP_FAIL: {error}")
        return 1
    results: list[ScenarioResult] = []
    try:
        with tempfile.TemporaryDirectory(prefix="sunsync_mock_validation_") as temporary:
            database = Path(temporary) / "validation.sqlite"
            engine = create_engine(
                f"sqlite:///{database}", connect_args={"check_same_thread": False}
            )
            try:
                with patch.object(
                    sunpath_service, "_open_meteo_snapshot", _offline_open_meteo_snapshot
                ), TestClient(create_app(engine)) as client:
                    for _, spec in specs:
                        scenario_dir = artifact_dir / spec["scenario"]
                        scenario_dir.mkdir()
                        result = (
                            _run_invalid(client, spec, scenario_dir)
                            if spec["kind"] == "invalid_window_geometry"
                            else _run_valid(client, spec, scenario_dir, args.verbose)
                        )
                        results.append(result)
                    if args.scenario is None:
                        results.extend(_run_contract_checks(client))
            finally:
                engine.dispose()
    except Exception as error:  # noqa: BLE001 - preserve a runner-level setup failure
        results.append(ScenarioResult("runner", "SETUP_FAIL", repr(error)))
    summary = {
        "run_id": artifact_dir.name,
        "results": [asdict(result) for result in results],
    }
    _write_json(artifact_dir / "summary.json", summary)
    lines = [
        "FR-01 / FR-06 mock validation",
        f"run: {artifact_dir.name}",
        "database: temporary isolated SQLite",
        "",
    ]
    lines.extend(f"{result.outcome:14} {result.scenario}\n  {result.detail}" for result in results)
    counts = {
        outcome: sum(result.outcome == outcome for result in results)
        for outcome in ("PASS", "SETUP_FAIL", "API_FAIL", "NUMERICAL_FAIL")
    }
    lines.extend(
        [
            "",
            "Summary: " + ", ".join(f"{count} {outcome}" for outcome, count in counts.items()),
            f"Artifacts: {artifact_dir}",
        ]
    )
    (artifact_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(result.outcome == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
