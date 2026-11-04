"""Versioned board-camera fragment; not the legacy FR-06 0.2.0 contract."""

import hashlib
import re
import numpy as np
from .core import compose_scene
from .fragment_schema import BoardCameraFragmentV1
from .models import rigid


def schema_id(value, suffix=""):
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        base = value
    else:
        slug = re.sub(r"[^A-Za-z0-9_]", "_", value)[:48]
        base = "id_" + slug + "_" + hashlib.sha256(value.encode()).hexdigest()[:16]
    return base + ("_" + suffix if suffix else "")


def export_fragment(measurement):
    scene = compose_scene([measurement])
    reference = measurement["reference"]
    if reference["alignment"] is None:
        raise ValueError("FR-06 requires confirmed ENU alignment")
    model = scene["furniture"][0]
    board = reference["board"]
    s = board["marker_side_m"]
    tbm = np.eye(4)
    tbm[:3, :3] = np.diag([1, -1, -1])
    tbm[:3, 3] = [s / 2, s / 2, 0]
    te_b = rigid(reference["alignment"]["T_enu_board"])
    te_m = te_b @ tbm
    normal_up = abs(te_m[2, 2])
    if normal_up < 0.05:
        plane = "vertical"
    elif normal_up > 0.999:
        plane = "horizontal"
    else:
        raise ValueError(
            "Fragment adapter supports vertical/horizontal marker planes only"
        )
    mid = schema_id(board["board_id"], "reference_marker")
    fid = schema_id(measurement["furniture_id"])
    oid = schema_id(measurement["furniture_id"], "observation")
    room_id = schema_id(reference["space_id"])
    pairs = [
        (key, p) for key, p in measurement["points"].items() if p["status"] == "ok"
    ]
    marker_points = []
    enu_points = []
    for key, p in pairs:
        pb = np.array(p["point_board_m"])
        pe = te_b[:3, :3] @ pb + te_b[:3, 3]
        if not np.allclose(pe, p["point_enu_m"], atol=1e-6):
            raise ValueError("Point ENU coordinates inconsistent")
        marker_points.append((tbm[:3, :3].T @ (pb - tbm[:3, 3])).tolist())
        enu_points.append(pe.tolist())
    derived = [fid + ".dimensions_m[0:2]", fid + ".yaw_deg_ccw_from_east"]
    if abs(model["base_center_enu_m"][2]) <= 1e-8:
        derived.append(fid + ".base_center_m")
    fragment = {
        "schema_version": "sunsync.fr06-fragment/1",
        "units": "m",
        "coordinate_frame": "ENU",
        "markers": [
            {
                "marker_id": mid,
                "family": "aruco",
                "dictionary": board["dictionary"],
                "code_id": board["ids_by_row"][0][0],
                "side_length_m": s,
                "plane": plane,
                "T_ENU_marker": te_m.ravel().tolist(),
            }
        ],
        "observations": [
            {
                "observation_id": oid,
                "method": "aruco_board_camera_at_point",
                "captured_at": None,
                "uncertainty_m": None,
                "confirmed": False,
                "note": "Guided camera-at-point measurement; placement bias unmeasured.",
                "marker_id": mid,
                "plane": plane,
                "points_marker_m": marker_points,
                "points_enu_m": enu_points,
                "derived_fields": derived,
                "independent_tape_error_m": None,
            }
        ],
        "occluders": [
            {
                "occluder_id": fid,
                "room_id": room_id,
                "name": measurement["furniture_kind"] + " standard-model bounding box",
                "kind": "cuboid",
                "base_center_m": model["base_center_enu_m"],
                "dimensions_m": model["dimensions_m"],
                "yaw_deg_ccw_from_east": model["yaw_deg_ccw_from_east"],
                "material": "opaque",
                "quality": {"observation_ids": [oid], "confirmed": False},
            }
        ],
        "windows": [],
        "protected_regions": [],
        "extensions": {
            "legacy_fr06_0_2_0_compatible": False,
            "receiver_contract": "sunsync.fr06-fragment/1",
            "marker_plane_semantics": "Plane describes the marker, not the measured furniture points.",
            "capture_time_status": "unknown",
            "id_mapping": {
                "board_id": {"source": board["board_id"], "marker_id": mid},
                "furniture_id": {
                    "source": measurement["furniture_id"],
                    "occluder_id": fid,
                },
                "space_id": {"source": reference["space_id"], "room_id": room_id},
            },
            "export_kind": "measurement_fragment_not_complete_scene",
            "reference_id": measurement["reference_id"],
            "point_ids": [k for k, p in pairs],
            "board": board,
            "standard_model_placement": model,
            "geometry_source": "opaque_bounding_volume_of_standard_model",
            "model_note": "Bounding box fills empty spaces between legs; not an exact sunlight occlusion mesh.",
            "review_status": "unreviewed",
            "method_extension_requires_receiver_agreement": True,
        },
    }
    return BoardCameraFragmentV1.model_validate(fragment).model_dump(mode="json")
