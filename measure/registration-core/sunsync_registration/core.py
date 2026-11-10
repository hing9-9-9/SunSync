import hashlib
import json
from datetime import datetime
import numpy as np
from .models import (
    Alignment,
    Board,
    Floor,
    ModelPlacement,
    PlanRequest,
    MeasureRequest,
    Shape,
    rigid,
)
from .pose import estimate
from .geometry import fit_surface


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plan(request: PlanRequest):
    kind = request.shape.kind
    if kind == "rectangle":
        points = [[-0.5, 0.5], [0.5, 0.5], [0.5, -0.5], [-0.5, -0.5]]
    elif kind == "circle":
        points = [[0, 1], [1, 0], [0, -1], [-1, 0]]
    elif kind == "ellipse":
        a = np.deg2rad([90, 45, 0, -90, -135, 180])
        points = np.column_stack([np.cos(a), 0.6 * np.sin(a)]).tolist()
    else:
        points = [list(v) for v in request.shape.vertices]
    steps = [
        {
            "point_id": f"P{i+1}",
            "role": "surface",
            "guide_xy": p,
            "required_samples": request.samples_per_point,
            "instruction_ko": f"P{i+1} 지점에 렌즈를 맞추고 고정 마커 보드를 촬영하세요.",
        }
        for i, p in enumerate(points)
    ]
    if request.height_mode == "capture_highest":
        steps.append(
            {
                "point_id": "H1",
                "role": "highest",
                "guide_xy": None,
                "required_samples": request.samples_per_point,
                "instruction_ko": "가구 전체의 가장 높은 지점에 렌즈를 맞추고 보드를 촬영하세요.",
            }
        )
    return {
        "plan_id": digest(request.model_dump()),
        "space_id": request.space_id,
        "furniture_id": request.furniture_id,
        "steps": steps,
        "guide_note": "Guide coordinates define topology and sampling instructions, not measured dimensions.",
    }


def measure(request: MeasureRequest):
    instructions = plan(request.plan)
    steps = {s["point_id"]: s for s in instructions["steps"]}
    grouped = {key: [] for key in steps}
    diagnostics = []
    accepted_times = []
    ids = [c.capture_id for c in request.captures]
    if len(set(ids)) != len(ids):
        raise ValueError("capture_id must be unique")
    enu = rigid(request.alignment.T_enu_board) if request.alignment else None
    up = None
    origin_height = None
    if enu is not None:
        up = enu[:3, :3].T @ np.array([0, 0, 1.0])
        origin_height = float(enu[2, 3])
    if request.floor:
        explicit_up = np.array(request.floor.up_board)
        if up is not None and (
            not np.allclose(up, explicit_up, atol=1e-6)
            or not np.isclose(
                origin_height, request.floor.board_origin_height_m, atol=1e-4
            )
        ):
            raise ValueError("Floor reference disagrees with ENU transform")
        up = explicit_up
        origin_height = request.floor.board_origin_height_m
    for capture in request.captures:
        if capture.point_id not in grouped:
            raise ValueError("Unknown point_id; use the active plan step")
        result = estimate(
            request.board,
            capture.camera,
            capture.detections,
            request.max_reprojection_rmse_px,
        )
        diagnostics.append(
            {"capture_id": capture.capture_id, "point_id": capture.point_id, **result}
        )
        if result["status"] == "ok":
            accepted_times.append(capture.captured_at)
            t = rigid(result["T_board_camera"])
            point = t[:3, :3] @ capture.camera_to_point_m + t[:3, 3]
            grouped[capture.point_id].append(point)
    points = {}
    actions = []
    for key, values in grouped.items():
        status = {
            "valid_samples": len(values),
            "required_samples": steps[key]["required_samples"],
            "uncertainty_m": None,
            "confirmed": False,
        }
        if len(values) < steps[key]["required_samples"]:
            points[key] = {**status, "status": "insufficient_samples"}
            actions.append(f"recapture:{key}")
            continue
        array = np.asarray(values)
        median = np.median(array, axis=0)
        spread = float(np.max(np.linalg.norm(array - median, axis=1)))
        if spread > request.max_repeat_spread_m:
            points[key] = {
                **status,
                "status": "inconsistent_samples",
                "repeat_spread_m": spread,
            }
            actions.append(f"recapture:{key}")
            continue
        points[key] = {
            **status,
            "status": "ok",
            "point_board_m": median.tolist(),
            "repeat_spread_m": spread if len(values) > 1 else None,
        }
        if enu is not None:
            points[key]["point_enu_m"] = (enu[:3, :3] @ median + enu[:3, 3]).tolist()
    reference = {
        "space_id": request.plan.space_id,
        "board": request.board.model_dump(),
        "alignment": request.alignment.model_dump() if request.alignment else None,
        "floor": request.floor.model_dump() if request.floor else None,
    }
    response = {
        "schema_version": "sunsync.registration/1",
        "units": "m",
        "reference": reference,
        "reference_id": digest(reference),
        "plan_id": instructions["plan_id"],
        "furniture_id": request.plan.furniture_id,
        "furniture_kind": request.plan.furniture_kind,
        "model_id": request.plan.model_id,
        "shape": request.plan.shape.model_dump(),
        "points": points,
        "capture_diagnostics": diagnostics,
        "surface": None,
        "model": None,
        "status": "incomplete",
        "required_actions": actions,
        "confirmed": False,
        "geometry_source": "measured_points_and_standard_model",
        "captured_at": (
            max(
                accepted_times,
                key=lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
            )
            if accepted_times and all(accepted_times)
            else None
        ),
        "manual_height_m": request.plan.manual_height_m,
        "manual_base_height_m": request.plan.manual_base_height_m,
        "surface_tolerance_m": request.max_surface_residual_m,
        "measurement_note": "Repeat spread and reprojection residual do not prove metric accuracy.",
    }
    surface_keys = [
        s["point_id"] for s in instructions["steps"] if s["role"] == "surface"
    ]
    if any(points[k]["status"] != "ok" for k in surface_keys):
        return response
    surface_points = np.array([points[k]["point_board_m"] for k in surface_keys])
    try:
        fit = fit_surface(
            request.plan.shape.kind, surface_points, request.max_surface_residual_m
        )
    except ValueError as error:
        response["status"] = "invalid_surface"
        actions.append(str(error))
        return response
    centre = fit["centre"]
    surface = {
        "width_m": fit["width"],
        "depth_m": fit["depth"],
        "center_board_m": centre.tolist(),
        "kind": request.plan.shape.kind,
        "outline_board_m": surface_points.tolist() if request.plan.shape.kind == "polygon" else None,
        "bounding_box": {"width_m": fit["width"], "depth_m": fit["depth"],
                         "center_board_m": centre.tolist(), "width_axis_board": fit["axis"].tolist()},
        "max_residual_m": fit["max_residual_m"],
        "height_above_floor_m": None,
    }
    response["surface"] = surface
    response["status"] = "surface_measured"
    if up is None:
        actions.append("provide_floor_reference_or_confirmed_enu_alignment")
        return response
    heights = surface_points @ up + origin_height
    if (
        float(np.max(abs(heights - heights.mean()))) > request.max_surface_residual_m
        or np.min(heights) < 0
    ):
        response["status"] = "invalid_surface"
        actions.append("surface_not_horizontal_or_below_floor")
        return response
    surface_height = float(np.mean(heights))
    surface["height_above_floor_m"] = surface_height
    mode = request.plan.height_mode
    if mode == "manual":
        height = request.plan.manual_height_m
    elif mode == "capture_highest":
        if points["H1"]["status"] != "ok":
            return response
        height = float(np.dot(points["H1"]["point_board_m"], up) + origin_height)
        if height < surface_height - request.max_surface_residual_m:
            actions.append("highest_point_below_surface")
            return response
    else:
        height = surface_height
    if height <= 0:
        actions.append("height_must_be_positive")
        return response
    base_height = (
        0 if request.plan.floor_supported else request.plan.manual_base_height_m
    )
    if mode == "manual" and (
        surface_height < base_height - request.max_surface_residual_m
        or surface_height > base_height + height + request.max_surface_residual_m
    ):
        actions.append("surface_outside_manual_model_height")
        return response
    base = centre - up * (surface_height - base_height)
    x = fit["axis"] - up * np.dot(fit["axis"], up)
    x /= np.linalg.norm(x)
    y = np.cross(up, x)
    t = np.eye(4)
    t[:3, :3] = np.column_stack([x, y, up])
    t[:3, 3] = base
    model = {
        "model_id": request.plan.model_id,
        "dimensions_m": [fit["width"], fit["depth"], height],
        "T_board_model": t.ravel().tolist(),
        "base_center_board_m": base.tolist(),
        "height_source": mode,
        "floor_supported": request.plan.floor_supported,
        "origin": "base_bounding_box_center",
        "local_axes": "x width, y depth, z up",
        "shape_approximation": True,
        "orientation_requires_user_review": True,
    }
    if enu is not None:
        transformed = enu @ t
        model.update(
            T_enu_model=transformed.ravel().tolist(),
            base_center_enu_m=transformed[:3, 3].tolist(),
            yaw_deg_ccw_from_east=float(
                np.degrees(np.arctan2(transformed[1, 0], transformed[0, 0])) % 360
            ),
        )
    response["model"] = model
    response["status"] = "model_ready_unreviewed"
    return response


def canonical_model(value: dict):
    """Rebuild placement from validated transforms, never caller-derived fields."""
    if (
        value.get("schema_version") != "sunsync.registration/1"
        or value.get("units") != "m"
    ):
        raise ValueError("Unsupported measurement contract")
    if value.get("status") != "model_ready_unreviewed":
        raise ValueError("Only complete model placements can enter a scene")
    reference = value["reference"]
    if value["reference_id"] != digest(reference):
        raise ValueError("Reference identity mismatch")
    Board.model_validate(reference["board"])
    model = value.get("model")
    if model is None:
        raise ValueError("Missing model")
    if model["model_id"] != value["model_id"]:
        raise ValueError("Model ID disagrees with measurement")
    tm = rigid(model["T_board_model"])
    data = {
        "model_id": model["model_id"],
        "dimensions_m": model["dimensions_m"],
        "T_board_model": tm.ravel().tolist(),
        "base_center_board_m": tm[:3, 3].tolist(),
        "height_source": model["height_source"],
        "floor_supported": model["floor_supported"],
    }
    alignment = reference["alignment"]
    floor = Floor.model_validate(reference["floor"]) if reference["floor"] else None
    if alignment is not None:
        a = Alignment.model_validate(alignment)
        te_b = rigid(a.T_enu_board)
        expected = te_b @ tm
        if not np.allclose(rigid(model["T_enu_model"]), expected, atol=1e-6):
            raise ValueError("Model ENU transform inconsistent with reference")
        up = te_b[:3, :3].T @ np.array([0.0, 0.0, 1.0])
        if floor and (
            not np.allclose(up, floor.up_board, atol=1e-6)
            or not np.isclose(te_b[2, 3], floor.board_origin_height_m, atol=1e-4)
        ):
            raise ValueError("Floor reference disagrees with ENU transform")
        data.update(
            T_enu_model=expected.ravel().tolist(),
            base_center_enu_m=expected[:3, 3].tolist(),
            yaw_deg_ccw_from_east=float(
                np.degrees(np.arctan2(expected[1, 0], expected[0, 0])) % 360
            ),
        )
    elif floor:
        up = np.array(floor.up_board)
        if model.get("T_enu_model") is not None:
            raise ValueError("ENU placement without ENU reference")
    else:
        raise ValueError("Model placement requires a floor or ENU reference")
    if not np.allclose(tm[:3, 2], up, atol=1e-6):
        raise ValueError("Model up axis disagrees with floor reference")
    shape = Shape.model_validate(value["shape"])
    count = {"rectangle": 4, "circle": 4, "ellipse": 6}.get(
        shape.kind, len(shape.vertices)
    )
    points = value["points"]
    if any(points[f"P{i+1}"]["status"] != "ok" for i in range(count)):
        raise ValueError("Model requires complete measured surface points")
    surface_points = np.array(
        [points[f"P{i+1}"]["point_board_m"] for i in range(count)], float
    )
    if not np.isfinite(surface_points).all():
        raise ValueError("Non-finite measured points")
    tolerance = float(value["surface_tolerance_m"])
    if not 0 < tolerance <= 0.1:
        raise ValueError("Invalid surface tolerance")
    fit = fit_surface(shape.kind, surface_points, tolerance)
    origin_height = (
        float(te_b[2, 3]) if alignment is not None else floor.board_origin_height_m
    )
    heights = surface_points @ up + origin_height
    if min(heights) < 0 or max(abs(heights - heights.mean())) > tolerance:
        raise ValueError("Measured surface must be horizontal and above the floor")
    surface_height = float(heights.mean())
    mode = model["height_source"]
    if mode == "manual":
        height = value["manual_height_m"]
    elif mode == "capture_highest":
        if points["H1"]["status"] != "ok":
            raise ValueError("Missing highest point")
        height = float(np.dot(points["H1"]["point_board_m"], up) + origin_height)
        if height < surface_height - tolerance:
            raise ValueError("Highest point below measured surface")
    elif mode == "surface_is_top":
        height = surface_height
    else:
        raise ValueError("Unknown height source")
    if height is None or not np.isfinite(height) or height <= 0:
        raise ValueError("Invalid measured/manual height")
    if not np.allclose(
        model["dimensions_m"], [fit["width"], fit["depth"], height], atol=1e-6, rtol=0
    ):
        raise ValueError(
            "Model dimensions disagree with measured points and height source"
        )
    return ModelPlacement.model_validate(data).model_dump(
        mode="json", exclude_none=True
    )


def compose_scene(measurements: list[dict]):
    if not measurements:
        raise ValueError("At least one measurement is required")
    models = [canonical_model(value) for value in measurements]
    ids = [v["furniture_id"] for v in measurements]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate furniture_id")
    space_ids = {v["reference"]["space_id"] for v in measurements}
    if len(space_ids) != 1:
        raise ValueError("Different space IDs cannot be merged")
    enu = all(v["reference"]["alignment"] is not None for v in measurements)
    if enu:
        first = measurements[0]["reference"]
        first_alignment = rigid(first["alignment"]["T_enu_board"])
        first_up = first_alignment[:3, :3].T @ np.array([0.0, 0.0, 1.0])
        for measurement in measurements[1:]:
            reference = measurement["reference"]
            alignment = rigid(reference["alignment"]["T_enu_board"])
            floor = reference["floor"]
            up = (
                np.array(floor["up_board"])
                if floor
                else alignment[:3, :3].T @ np.array([0.0, 0.0, 1.0])
            )
            height = floor["board_origin_height_m"] if floor else alignment[2, 3]
            if (
                not np.allclose(alignment, first_alignment, atol=1e-6, rtol=0)
                or not np.allclose(up, first_up, atol=1e-6, rtol=0)
                or not np.isclose(height, first_alignment[2, 3], atol=1e-6, rtol=0)
            ):
                raise ValueError(
                    "Different ENU alignments/floor references cannot be merged"
                )
    if not enu and len({v["reference_id"] for v in measurements}) != 1:
        raise ValueError(
            "Different board references require ENU alignment before merging"
        )
    return {
        "schema_version": "sunsync.scene/1",
        "space_id": next(iter(space_ids)),
        "coordinate_frame": "ENU" if enu else "board",
        "units": "m",
        "furniture": [
            {
                "furniture_id": v["furniture_id"],
                "furniture_kind": v["furniture_kind"],
                "reference_id": v["reference_id"],
                **model,
            }
            for v, model in zip(measurements, models)
        ],
        "confirmed": False,
        "geometry_source": "standard_model_approximation",
    }
