"""Device Shadow composition, canonical state digest, delta computation, and ETag helpers."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

_ARUCO_CACHE: dict[int, list[list[float]]] = {}


def default_marker_overlay() -> dict[str, Any]:
    """Return canonical disabled marker overlay descriptor."""
    return {
        "enabled": False,
        "code_id": None,
        "start_row": None,
        "start_col": None,
    }


def normalize_marker_overlay(marker_overlay: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a marker overlay dict to canonical keys and types."""
    if not marker_overlay or not bool(marker_overlay.get("enabled")):
        return default_marker_overlay()

    code_id = marker_overlay.get("code_id")
    start_row = marker_overlay.get("start_row")
    start_col = marker_overlay.get("start_col")
    return {
        "enabled": True,
        "code_id": int(code_id) if code_id is not None else None,
        "start_row": int(start_row) if start_row is not None else None,
        "start_col": int(start_col) if start_col is not None else None,
    }


def quantize_cells(cells: list[list[float]]) -> list[list[float]]:
    """Return a deep copy of cells with values quantized to 3 decimal places."""
    result: list[list[float]] = []
    for r, row in enumerate(cells):
        q_row: list[float] = []
        for c, val in enumerate(row):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise TypeError(f"Cell value at ({r}, {c}) must be a number, got {val!r}")
            fv = float(val)
            if math.isnan(fv) or math.isinf(fv):
                raise ValueError(f"Cell value at ({r}, {c}) must be finite, got {val!r}")
            rounded = round(fv, 3)
            q_row.append(0.0 if rounded == 0.0 else rounded)
        result.append(q_row)
    return result


def generate_aruco_4x4_pattern(code_id: int) -> list[list[float]]:
    """Generate the 6x6 ArUco DICT_4X4_50 marker pattern as film cell opacities.

    Outer 1-cell border is 1.0 (opaque/black). Inner 4x4 binary pattern is
    1.0 for black bits and 0.0 for white bits.
    """
    if isinstance(code_id, bool) or not isinstance(code_id, int) or not (0 <= code_id < 50):
        raise ValueError(
            f"ArUco DICT_4X4_50 code_id must be an integer between 0 and 49, got {code_id!r}"
        )
    if code_id in _ARUCO_CACHE:
        return [row[:] for row in _ARUCO_CACHE[code_id]]

    import cv2

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(dictionary, code_id, 6)
    pattern = [[1.0 if float(img[r, c]) == 0.0 else 0.0 for c in range(6)] for r in range(6)]
    _ARUCO_CACHE[code_id] = pattern
    return [row[:] for row in pattern]


def compose_effective_cells(
    base_cells: list[list[float]],
    marker_overlay: dict[str, Any] | None = None,
) -> list[list[float]]:
    """Compose effective actuator cells from base_cells and optional 6x6 ArUco overlay.

    Never mutates base_cells.
    """
    effective = quantize_cells(base_cells)
    marker = normalize_marker_overlay(marker_overlay)
    if not marker["enabled"]:
        return effective

    rows = len(effective)
    cols = len(effective[0]) if rows > 0 else 0

    code_id = marker["code_id"]
    start_row = marker["start_row"]
    start_col = marker["start_col"]

    if code_id is None or start_row is None or start_col is None:
        raise ValueError("Enabled marker_overlay requires code_id, start_row, and start_col")

    if not (0 <= start_row and start_row + 6 <= rows):
        raise ValueError(
            f"Marker rows [{start_row}..{start_row + 5}] exceed window grid rows ({rows})"
        )
    if not (0 <= start_col and start_col + 6 <= cols):
        raise ValueError(
            f"Marker cols [{start_col}..{start_col + 5}] exceed window grid cols ({cols})"
        )

    pattern = generate_aruco_4x4_pattern(code_id)
    for r in range(6):
        for c in range(6):
            effective[start_row + r][start_col + c] = pattern[r][c]
    return effective


def compute_state_digest(
    effective_cells: list[list[float]],
    marker_overlay: dict[str, Any] | None = None,
) -> str:
    """Compute canonical SHA-256 hex digest over quantized effective_cells and marker state."""
    q_cells = quantize_cells(effective_cells)
    marker = normalize_marker_overlay(marker_overlay)
    canonical = json.dumps(
        {"cells": q_cells, "marker": marker},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def format_etag(reported_version: int, state_digest: str) -> str:
    """Format weak/strong HTTP ETag for a window shadow."""
    return f'"v{reported_version}-{state_digest[:12]}"'


def compute_shadow_delta(
    desired_effective: list[list[float]],
    reported_effective: list[list[float]],
    desired_marker: dict[str, Any] | None,
    reported_marker: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute cell-level and marker-level delta between desired and reported shadow states."""
    d_cells = quantize_cells(desired_effective)
    r_cells = quantize_cells(reported_effective)
    changed_cells: list[dict[str, Any]] = []

    max_rows = max(len(d_cells), len(r_cells))
    for r in range(max_rows):
        d_row = d_cells[r] if r < len(d_cells) else []
        r_row = r_cells[r] if r < len(r_cells) else []
        max_cols = max(len(d_row), len(r_row))
        for c in range(max_cols):
            d_val = d_row[c] if c < len(d_row) else None
            r_val = r_row[c] if c < len(r_row) else None
            if d_val is None or r_val is None or abs(d_val - r_val) > 1e-6:
                changed_cells.append(
                    {
                        "row": r,
                        "col": c,
                        "desired": d_val,
                        "reported": r_val,
                    }
                )

    d_marker = normalize_marker_overlay(desired_marker)
    r_marker = normalize_marker_overlay(reported_marker)
    marker_diff: dict[str, dict[str, Any]] = {}
    for field in ("enabled", "code_id", "start_row", "start_col"):
        if d_marker[field] != r_marker[field]:
            marker_diff[field] = {
                "desired": d_marker[field],
                "reported": r_marker[field],
            }

    return {
        "in_sync": not changed_cells and not marker_diff,
        "changed_cells": changed_cells,
        "marker_diff": marker_diff,
    }
