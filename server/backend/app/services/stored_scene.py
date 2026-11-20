"""Validate stored scenes and explicitly recover the historical pairing transpose."""

import json

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.geometry import close
from app.schemas.scene import Marker, Scene, Window


def load_stored_scene(payload: str) -> Scene:
    """Reject invalid persisted coordinates instead of using them for measurements."""
    try:
        return Scene.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail="Stored scene requires repair: " + str(exc),
        ) from exc


def load_scene_for_pairing(payload: str) -> Scene:
    """Repair only known transposed rotations, in memory, on explicit re-pairing.

    Translation, marker IDs and unrelated geometry are never reinterpreted.
    Persistence happens only after the pairing service validates the complete scene.
    """
    try:
        return Scene.model_validate_json(payload)
    except ValidationError as exc:
        if any("window marker rotation columns must match" not in e["msg"] for e in exc.errors()):
            raise HTTPException(
                status_code=409,
                detail="Stored scene cannot be repaired by marker-pairing: " + str(exc),
            ) from exc
    data = json.loads(payload)
    try:
        windows = {w.window_id: w for w in map(Window.model_validate, data.get("windows", []))}
        repaired = set()
        for raw in data.get("markers", []):
            marker = Marker.model_validate(raw)
            if marker.plane != "window" or marker.window_id not in windows:
                continue
            window = windows[marker.window_id]
            matrix = marker.T_ENU_marker
            expected = window.marker_transform(0.0, 0.0)
            if all(close(matrix[i:12:4], expected[i:12:4]) for i in range(3)):
                continue
            # Only the old row-basis convention is recoverable automatically.
            if not (
                close(matrix[0:3], window.u_axis)
                and close(matrix[4:7], window.v_axis)
                and close(matrix[8:11], window.inward_normal)
            ):
                continue
            corrected = list(matrix)
            for row in range(3):
                for col in range(3):
                    corrected[4 * row + col] = expected[4 * row + col]
            raw["T_ENU_marker"] = corrected
            repaired.add(marker.marker_id)
        if repaired and (
            data.get("registration_calibration") is not None
            or any(o.get("marker_id") in repaired for o in data.get("observations", []))
        ):
            raise HTTPException(
                status_code=409,
                detail="Legacy marker has dependent measurements or registration calibration. "
                "Back up the scene and rebuild the measurement reference in a new scene; "
                "existing furniture coordinates will not be reinterpreted automatically.",
            )
        return Scene.model_validate(data)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Stored scene cannot be repaired by marker-pairing: " + str(exc),
        ) from exc
