"""FR06 geometry, provenance and semantic contract regression tests."""

import copy
import json
import math
from pathlib import Path

import pytest
from app.schemas.scene import ProtectedRegion, Scene
from pydantic import ValidationError

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/fr06_scene_example.json"


@pytest.fixture
def scene_data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def room_only(data):
    data = copy.deepcopy(data)
    for key in (
        "windows",
        "occluders",
        "protected_regions",
        "markers",
        "optical_profiles",
        "policy",
        "scenario",
        "weather",
    ):
        data.pop(key)
    data["observations"] = [data["observations"][0]]
    return data


def set_value(data, path, value):
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


def test_full_example_accepts_dict_and_json_without_conversion_workaround(scene_data):
    scene = Scene.model_validate(scene_data)
    assert scene.model_dump(mode="json") == scene_data
    assert Scene.model_validate_json(scene.model_dump_json()) == scene
    assert scene.protected_regions[0].receiving_normal == (0, -1, 0)
    assert scene.protected_regions[2].quality.observation_ids == ["obs_aruco_floor"]


def test_manual_room_can_be_saved_without_simulation_inputs(scene_data):
    scene = Scene.model_validate(room_only(scene_data))
    assert scene.windows == scene.occluders == scene.protected_regions == []
    assert scene.policy is scene.scenario is scene.weather is None
    assert Scene.model_validate_json(scene.model_dump_json()) == scene


def test_all_manual_input_uses_the_same_contract(scene_data):
    scene_data["markers"] = []
    scene_data["observations"] = scene_data["observations"][:1]
    for item in [
        scene_data["room"],
        *scene_data["windows"],
        *scene_data["occluders"],
        *scene_data["protected_regions"],
    ]:
        item["quality"]["observation_ids"] = ["obs_manual_room"]
    assert Scene.model_validate(scene_data).room.height_m == 3


def test_arbitrary_enu_rotation_preserves_window_cuboid_surface_and_marker(scene_data):
    angle = 37.0
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def rotate(point):
        x, y, z = point
        return [c * x - s * y, s * x + c * y, z]

    scene_data["room"]["floor_vertices_m"] = [
        rotate(p) for p in scene_data["room"]["floor_vertices_m"]
    ]
    for window in scene_data["windows"]:
        window["vertices_m"] = [rotate(p) for p in window["vertices_m"]]
        for key in ("inward_normal", "u_axis", "v_axis"):
            window[key] = rotate(window[key])
        window["azimuth_deg_true"] = (window["azimuth_deg_true"] - angle) % 360
    for occluder in scene_data["occluders"]:
        occluder["base_center_m"] = rotate(occluder["base_center_m"])
        occluder["yaw_deg_ccw_from_east"] += angle
    for region in scene_data["protected_regions"]:
        for key in ("vertices_m", "sample_points_m"):
            region[key] = [rotate(p) for p in region[key]]
        region["receiving_normal"] = rotate(region["receiving_normal"])
    for marker in scene_data["markers"]:
        matrix = marker["T_ENU_marker"]
        matrix[:8] = [c * matrix[i] - s * matrix[4 + i] for i in range(4)] + [
            s * matrix[i] + c * matrix[4 + i] for i in range(4)
        ]
    for observation in scene_data["observations"]:
        if observation["method"] == "aruco_planar":
            observation["points_enu_m"] = [rotate(p) for p in observation["points_enu_m"]]
    scene = Scene.model_validate(scene_data)
    assert scene.windows[0].azimuth_deg_true == 143
    assert scene.occluders[0].yaw_deg_ccw_from_east == 37


def test_tilted_triangle_surface_is_supported(scene_data):
    region = scene_data["protected_regions"][1]
    region["vertices_m"] = [[1, 1, 0.3], [2, 1, 0.3], [1, 2, 1.3]]
    region["receiving_normal"] = [0, -1 / math.sqrt(2), 1 / math.sqrt(2)]
    region["sample_points_m"] = [[4 / 3, 4 / 3, 1.9 / 3]]
    assert Scene.model_validate(scene_data).protected_regions[1].host_occluder_id is None


@pytest.mark.parametrize("dni", [None, 0, 900])
def test_a_geometric_requirement_does_not_depend_on_dni(scene_data, dni):
    scene_data["weather"]["records"][0]["dni_W_m2"] = dni
    scene_data["optical_profiles"][0]["film_min_direct_transmittance"] = 0.2
    scene = Scene.model_validate(scene_data)
    assert scene.weather.records[0].dni_W_m2 == dni
    assert scene.policy.grades.A.rule == "block_geometric_direct_paths"
    assert scene.policy.grades.A.constraint == "hard"


def test_policy_caps_are_configurable_not_film_percentages(scene_data):
    scene_data["policy"]["grades"]["B"]["max_direct_irradiance_W_m2"] = 15
    scene_data["policy"]["grades"]["C"]["max_direct_irradiance_W_m2"] = 120
    scene_data["optical_profiles"][0]["film_min_direct_transmittance"] = 0.2
    scene = Scene.model_validate(scene_data)
    assert scene.policy.grades.C.max_direct_irradiance_W_m2 == 120
    # Storage does not falsely claim hardware feasibility or drop A because tau_min > 0.
    assert scene.protected_regions[0].grade == "A"


def test_unknown_uncertainty_and_unconfirmed_draft_are_preserved(scene_data):
    data = room_only(scene_data)
    data["data_kind"] = "measured_draft"
    data["room"]["quality"]["confirmed"] = False
    data["observations"][0]["confirmed"] = False
    data["north_alignment"]["confirmed"] = False
    scene = Scene.model_validate(data)
    assert scene.observations[0].uncertainty_m is None
    assert scene.room.quality.confirmed is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "0.1.0"),
        (("units",), "cm"),
        (("coordinate_frame",), "XYZ"),
        (("scene_id",), "room/other"),
        (("revision",), True),
        (("revision",), 1.5),
        (("revision",), 2**63),
        (("room", "height_m"), 0),
        (("room", "height_m"), -1),
        (("room", "height_m"), True),
        (("room", "height_m"), "3"),
        (("room", "height_m"), float("inf")),
        (("room", "floor_vertices_m", 0, 0), float("nan")),
        (("room", "floor_vertices_m", 0, 0), False),
        (("room", "floor_vertices_m"), [[0, 0, 1], [8, 0, 1], [8, 6, 1], [0, 6, 1]]),
        (("room", "floor_vertices_m"), [[0, 0, 0], [1, 0, 0], [2, 0, 0]]),
        (("room", "floor_vertices_m"), [[0, 0, 0], [8, 0, 0], [8, 6, 0], [0, 0, 0]]),
        (("room", "floor_vertices_m"), [[0, 0, 0], [8, 6, 0], [0, 6, 0], [8, 0, 0]]),
        (("room", "quality", "confirmed"), "true"),
        (("room", "quality", "observation_ids"), ["missing"]),
        (("room", "quality", "observation_ids"), ["obs_manual_room", "obs_manual_room"]),
        (("room", "quality", "observation_ids"), ["obs_aruco_floor"]),
        (("site", "latitude_deg"), 91),
        (("site", "longitude_deg"), float("inf")),
        (("site", "timezone"), "Mars/Olympus"),
        (("created_at",), "2026-02-30T00:00:00Z"),
        (("created_at",), "2026-10-14T00:00:00"),
        (("created_at",), "2026-10-14T00:00:00+25:00"),
        (("windows", 0, "room_id"), "missing"),
        (("windows", 0, "optical_profile_id"), "missing"),
        (("windows", 0, "azimuth_deg_true"), 0),
        (("windows", 0, "u_axis"), [1, 0, 0]),
        (("windows", 0, "inward_normal"), [0, 2, 0]),
        (("windows", 0, "vertices_m", 2, 0), 1.5),
        (("windows", 0, "initial_film_direct_transmittance"), 1.01),
        (("windows", 0, "quality", "observation_ids"), ["obs_aruco_floor"]),
        (("occluders", 0, "base_center_m"), [30, 3, 0]),
        (("occluders", 0, "base_center_m"), [3, 3, -0.1]),
        (("occluders", 0, "dimensions_m", 2), 4),
        (("occluders", 0, "dimensions_m", 0), 0),
        (("occluders", 0, "quality", "observation_ids"), ["obs_aruco_floor"]),
        (("protected_regions", 1, "region_id"), "region_A"),
        (("protected_regions", 0, "host_occluder_id"), "missing"),
        (("protected_regions", 0, "policy_id"), "missing"),
        (("protected_regions", 0, "grade"), "E"),
        (("protected_regions", 0, "sample_points_m", 0, 1), 2.8),
        (("protected_regions", 0, "sample_points_m", 0, 0), 4.1),
        (("protected_regions", 1, "vertices_m", 0, 2), 0.8),
        (("protected_regions", 1, "quality", "observation_ids"), ["obs_aruco_floor"]),
        (("markers", 0, "T_ENU_marker", 0), 2),
        (("markers", 0, "T_ENU_marker", 11), 1),
        (("markers", 0, "T_ENU_marker", 15), 0),
        (("markers", 0, "code_id"), True),
        (("markers", 0, "code_id"), 50),
        (("observations", 0, "confirmed"), False),
        (("observations", 4, "marker_id"), "missing"),
        (("observations", 4, "points_marker_m", 0, 2), 1),
        (("observations", 4, "points_enu_m", 0, 0), 2.2),
        (("observations", 4, "points_enu_m", 0, 2), 1),
        (("observations", 4, "derived_fields"), ["shelf_001.dimensions_m[2]"]),
        (("observations", 4, "derived_fields"), ["shelf_001.dimensions_m"]),
        (("observations", 4, "derived_fields"), ["region_A.vertices_m"]),
        (("observations", 4, "derived_fields"), ["missing.floor_vertices_m"]),
        (("observations", 4, "derived_fields"), ["room_001.height_m"]),
        (("observations", 4, "derived_fields"), ["room_001.floor_vertices_m"]),
        (("optical_profiles", 0, "film_min_direct_transmittance"), 1.2),
        (("optical_profiles", 0, "film_max_direct_transmittance"), 0.5),
        (("policy", "grades", "A", "rule"), "intensity_cap"),
        (("policy", "grades", "A", "max_direct_irradiance_W_m2"), None),
        (("policy", "grades", "B", "max_direct_irradiance_W_m2"), 100),
        (("policy", "grades", "B", "max_direct_irradiance_W_m2"), 0),
        (("policy", "grades", "C", "max_direct_irradiance_W_m2"), None),
        (("policy", "grades", "D", "max_direct_irradiance_W_m2"), 0),
        (("policy", "grades", "D", "rank"), 1),
        (("policy", "brightening_margin_W_m2"), 20),
        (("scenario", "scene_revision"), 999),
        (("scenario", "scene_revision"), 2**63),
        (("scenario", "time_step_s"), 11),
        (("scenario", "end_time"), "2026-10-14T02:00:00Z"),
        (("scenario", "weather_id"), "missing"),
        (("scenario", "policy_id"), "missing"),
        (("weather", "records", 0, "dni_W_m2"), -1),
        (("weather", "records", 0, "dni_W_m2"), True),
        (("weather", "unit"), "lux"),
        (("weather", "variable"), "global_horizontal_irradiance"),
        (("weather", "temporal_meaning"), "preceding_hour_mean"),
        (("weather", "records", 1, "valid_time"), "2026-10-14T03:00:00Z"),
        (("weather", "records", 0, "interval_start"), "2026-10-14T02:00:00Z"),
        (("weather", "fetched_at"), "2026-02-30T00:00:00Z"),
        (("weather", "forecast_reference_time"), "2026-10-14T00:00:00"),
        (("weather", "is_local_sensor"), 0),
        (("weather",), None),
        (("policy",), None),
    ],
)
def test_rejects_invalid_scene_values(scene_data, path, value):
    set_value(scene_data, path, value)
    with pytest.raises(ValidationError):
        Scene.model_validate(scene_data)


@pytest.mark.parametrize("target", [(), ("room",), ("policy", "grades", "A")])
def test_unknown_fields_do_not_silently_enter_contract(scene_data, target):
    set_value(scene_data, (*target, "unexpected"), 1)
    with pytest.raises(ValidationError, match="Extra inputs"):
        Scene.model_validate(scene_data)


def test_missing_dni_must_be_explicit_null(scene_data):
    scene_data["weather"]["records"][0].pop("dni_W_m2")
    with pytest.raises(ValidationError, match="dni_W_m2"):
        Scene.model_validate(scene_data)


def test_aruco_requires_equal_point_counts(scene_data):
    scene_data["observations"][4]["points_enu_m"].pop()
    with pytest.raises(ValidationError, match="point counts"):
        Scene.model_validate(scene_data)


def test_aruco_cannot_claim_height_even_with_manual_provenance(scene_data):
    scene_data["room"]["quality"]["observation_ids"].append("obs_aruco_floor")
    scene_data["observations"][4]["derived_fields"] = ["room_001.height_m"]
    with pytest.raises(ValidationError, match="cannot supply height"):
        Scene.model_validate(scene_data)


def test_aruco_room_floor_and_manual_height_are_accepted(scene_data):
    scene_data["room"]["quality"]["observation_ids"].append("obs_aruco_floor")
    scene_data["observations"][4]["derived_fields"].append("room_001.floor_vertices_m")
    assert Scene.model_validate(scene_data).room.height_m == 3


def test_reflection_marker_transform_is_not_a_rigid_enu_rotation(scene_data):
    scene_data["markers"][0]["T_ENU_marker"][0] = -1
    with pytest.raises(ValidationError, match="right-handed"):
        Scene.model_validate(scene_data)


def test_window_inside_room_but_off_wall_is_rejected(scene_data):
    for point in scene_data["windows"][0]["vertices_m"]:
        point[1] = 1
    with pytest.raises(ValidationError, match="one room wall"):
        Scene.model_validate(scene_data)


def test_outward_window_frame_is_rejected(scene_data):
    window = scene_data["windows"][0]
    window["vertices_m"] = [window["vertices_m"][i] for i in (1, 0, 3, 2)]
    window["inward_normal"] = [0, -1, 0]
    window["u_axis"] = [1, 0, 0]
    window["azimuth_deg_true"] = 0
    with pytest.raises(ValidationError, match="point into"):
        Scene.model_validate(scene_data)


def test_host_surface_normal_cannot_point_into_cuboid(scene_data):
    region = scene_data["protected_regions"][0]
    region["vertices_m"].reverse()
    region["receiving_normal"] = [0, 1, 0]
    with pytest.raises(ValidationError, match="host normal"):
        Scene.model_validate(scene_data)


def test_host_region_cannot_float_away_from_surface(scene_data):
    region = scene_data["protected_regions"][0]
    for key in ("vertices_m", "sample_points_m"):
        for point in region[key]:
            point[1] += 0.1
    with pytest.raises(ValidationError, match="host face"):
        Scene.model_validate(scene_data)


def test_reversed_polygon_winding_rejected_even_if_plane_is_valid(scene_data):
    scene_data["protected_regions"][1]["vertices_m"].reverse()
    with pytest.raises(ValidationError, match="winding"):
        Scene.model_validate(scene_data)


def concave_room(scene_data):
    data = room_only(scene_data)
    data["room"]["floor_vertices_m"] = [
        [0, 0, 0],
        [8, 0, 0],
        [8, 6, 0],
        [5, 6, 0],
        [5, 4, 0],
        [3, 4, 0],
        [3, 6, 0],
        [0, 6, 0],
    ]
    return data


def test_simple_concave_room_supported(scene_data):
    assert len(Scene.model_validate(concave_room(scene_data)).room.floor_vertices_m) == 8


@pytest.mark.parametrize("kind", ["region", "cuboid"])
def test_all_vertices_inside_is_insufficient_for_concave_room(scene_data, kind):
    data = concave_room(scene_data)
    if kind == "region":
        region = copy.deepcopy(scene_data["protected_regions"][1])
        region["vertices_m"] = [[1, 3.5, 0], [7, 3.5, 0], [7, 5.5, 0], [1, 5.5, 0]]
        region["sample_points_m"] = copy.deepcopy(region["vertices_m"])
        region["quality"]["observation_ids"] = ["obs_manual_room"]
        data["protected_regions"] = [region]
        data["policy"] = scene_data["policy"]
    else:
        occluder = copy.deepcopy(scene_data["occluders"][0])
        occluder["base_center_m"] = [4, 4.5, 0]
        occluder["dimensions_m"] = [6, 2, 1]
        occluder["quality"]["observation_ids"] = ["obs_manual_room"]
        data["occluders"] = [occluder]
    with pytest.raises(ValidationError, match="outside room"):
        Scene.model_validate(data)


def mean_weather(data):
    weather = data["weather"]
    weather["temporal_meaning"] = "preceding_hour_mean"
    weather["variable"] = "direct_normal_irradiance"
    weather["interpolation"] = "none"
    for hour, record in enumerate(weather["records"], 2):
        record["interval_start"] = f"2026-10-14T0{hour}:00:00Z"
        record["interval_end"] = record["valid_time"]
    return weather


def test_hour_mean_preserved_as_mean_with_explicit_intervals(scene_data):
    mean_weather(scene_data)
    assert Scene.model_validate(scene_data).weather.temporal_meaning == "preceding_hour_mean"


@pytest.mark.parametrize(
    "mutation", ["missing_start", "short_interval", "wrong_end", "interpolation"]
)
def test_hour_mean_cannot_masquerade_as_instant_measurement(scene_data, mutation):
    weather = mean_weather(scene_data)
    if mutation == "missing_start":
        weather["records"][0]["interval_start"] = None
    elif mutation == "short_interval":
        weather["records"][0]["interval_start"] = "2026-10-14T02:30:00Z"
    elif mutation == "wrong_end":
        weather["records"][0]["interval_end"] = "2026-10-14T02:59:00Z"
    else:
        weather["interpolation"] = "linear_estimate"
    with pytest.raises(ValidationError):
        Scene.model_validate(scene_data)


def test_open_meteo_requires_fetch_timestamp(scene_data):
    scene_data["weather"]["source"] = "open_meteo"
    scene_data["weather"]["fetched_at"] = None
    with pytest.raises(ValidationError, match="fetched_at"):
        Scene.model_validate(scene_data)


def test_surface_validation_applies_without_scene_wrapper(scene_data):
    region = scene_data["protected_regions"][0]
    region["sample_points_m"][0][2] = 2
    with pytest.raises(ValidationError, match="evaluation surface"):
        ProtectedRegion.model_validate(region)
