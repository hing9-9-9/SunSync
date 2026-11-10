"""Session-scoped random film markers for app capture (virtual film transport)."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from pydantic import Field as InputField
from sqlalchemy import update
from sqlmodel import Field, Session, SQLModel, col

from app.db import get_session
from app.models.film import FilmStateRecord
from app.models.scene import SceneRecord
from app.schemas.film import MarkerModeRequest, MarkerPairingRequest
from app.services.film import generate_aruco_4x4_pattern, pair_marker_for_scene, set_marker_mode
from app.services.stored_scene import load_stored_scene


class CaptureMarkerSession(SQLModel, table=True):
    scene_id: str = Field(primary_key=True)
    session_id: str
    window_id: str
    marker_json: str
    expires_at: str
    active: bool = True


class StartCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    window_id: str | None = None
    module_row: Annotated[int, InputField(ge=0, strict=True)] = 0
    module_col: Annotated[int, InputField(ge=0, strict=True)] = 0


DB = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/scenes/{scene_id}/capture-marker", tags=["capture-marker"])


def lock_scene(scene_id: str, db: Session) -> SceneRecord:
    # Serialize competing starts/ends across workers using the database write lock.
    db.execute(
        update(SceneRecord)
        .where(col(SceneRecord.scene_id) == scene_id)
        .values(revision=col(SceneRecord.revision))
    )
    record = db.get(SceneRecord, scene_id)
    if record is None:
        raise HTTPException(404, "Scene not found")
    return record


def film_matches(row: CaptureMarkerSession, db: Session) -> bool:
    film = db.get(FilmStateRecord, f"{row.scene_id}:{row.window_id}")
    marker = json.loads(row.marker_json)
    if not film or not film.marker_mode_active or film.marker_code_id != marker["code_id"]:
        return False
    start_row, start_col = marker["start_row"], marker["start_col"]
    if (film.marker_start_row, film.marker_start_col) != (start_row, start_col):
        return False
    cells = json.loads(film.cells_json)
    return [line[start_col : start_col + 6] for line in cells[start_row : start_row + 6]] == (
        generate_aruco_4x4_pattern(marker["code_id"])
    )


def describe(row: CaptureMarkerSession, db: Session) -> dict:
    if not row.active or datetime.fromisoformat(row.expires_at) <= datetime.now(UTC):
        raise HTTPException(410, "Capture marker session expired or ended; start capture again")
    record = db.get(SceneRecord, row.scene_id)
    if record is None:
        raise HTTPException(404, "Scene not found")
    scene = load_stored_scene(record.scene_json)
    marker = json.loads(row.marker_json)
    actual = next((m for m in scene.markers if m.marker_id == marker["marker_id"]), None)
    if actual is None or actual.model_dump(mode="json") != marker or not film_matches(row, db):
        raise HTTPException(409, "Capture marker changed; cancel and start capture again")
    film = db.get(FilmStateRecord, f"{row.scene_id}:{row.window_id}")
    assert film is not None
    window = next(w for w in scene.windows if w.window_id == row.window_id)
    module_row, module_col = marker["start_row"] // 10, marker["start_col"] // 10
    try:
        module_rows, module_cols = window.film_module_shape()
        sr, sc, u, v = window.module_marker_placement(module_row, module_col)
    except ValueError as exc:
        raise HTTPException(409, "Film layout changed; start capture again") from exc
    if (
        (film.film_rows, film.film_cols) != (window.film_rows, window.film_cols)
        or (sr, sc) != (marker["start_row"], marker["start_col"])
        or any(
            abs(a - b) > 1e-6
            for a, b in zip(window.marker_transform(u, v), marker["T_ENU_marker"], strict=True)
        )
    ):
        raise HTTPException(409, "Film layout changed; start capture again")
    return {
        "session_id": row.session_id,
        "scene_id": row.scene_id,
        "window_id": row.window_id,
        "film_module": {
            "row": module_row,
            "col": module_col,
            "rows": module_rows,
            "cols": module_cols,
            "side_m": 1.0,
            "marker_center_uv_m": [u, v],
        },
        "expires_at": row.expires_at,
        "display_status": "virtual_applied",
        "marker": marker,
        "board": {
            "board_id": "window_board",
            "dictionary": marker["dictionary"],
            "ids_by_row": [[marker["code_id"]]],
            "marker_side_m": marker["side_length_m"],
            "gap_x_m": 0,
            "gap_y_m": 0,
            "border_bits": 1,
        },
        "film_cells": json.loads(film.cells_json),
    }


@router.post("")
def start_capture(scene_id: str, request: StartCapture, db: DB) -> dict:
    record = lock_scene(scene_id, db)
    previous = db.get(CaptureMarkerSession, scene_id)
    token = str(request.request_id)
    if previous and previous.session_id == token:
        if request.window_id is not None and request.window_id != previous.window_id:
            raise HTTPException(409, "Capture request is already bound to another window")
        marker = json.loads(previous.marker_json)
        if (request.module_row, request.module_col) != (
            marker["start_row"] // 10,
            marker["start_col"] // 10,
        ):
            raise HTTPException(409, "Capture request is already bound to another film module")
        return describe(previous, db)
    if (
        previous
        and previous.active
        and datetime.fromisoformat(previous.expires_at) > datetime.now(UTC)
    ):
        raise HTTPException(409, "Another capture session is active; finish or cancel it first")
    scene = load_stored_scene(record.scene_json)
    windows = [
        w for w in scene.windows if request.window_id is None or w.window_id == request.window_id
    ]
    if len(windows) != 1:
        raise HTTPException(422, "Choose exactly one installed window for capture")
    window = windows[0]
    try:
        start_row, start_col, _, _ = window.module_marker_placement(
            request.module_row, request.module_col
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    used = {m.code_id for m in scene.markers if m.window_id != window.window_id}
    available = [code for code in range(50) if code not in used]
    if not available:
        raise HTTPException(409, "No unused marker IDs for this scene")
    code = secrets.choice(available)
    if (
        previous
        and previous.active
        and previous.window_id != window.window_id
        and film_matches(previous, db)
    ):
        set_marker_mode(
            db,
            scene_id,
            MarkerModeRequest(window_id=previous.window_id, enabled=False),
            commit=False,
        )
    # Pair geometry first, then apply the pattern through the normal film command path.
    # Both operations and the session are committed together; a failure rolls them all back.
    pair_marker_for_scene(
        db,
        scene_id,
        MarkerPairingRequest(
            code_id=code,
            window_id=window.window_id,
            module_row=request.module_row,
            module_col=request.module_col,
            auto_provision_window=False,
            enable_marker_display=False,
            clamp_furniture_to_room=False,
        ),
        commit=False,
    )
    set_marker_mode(
        db,
        scene_id,
        MarkerModeRequest(
            window_id=window.window_id,
            enabled=True,
            code_id=code,
            start_row=start_row,
            start_col=start_col,
        ),
        commit=False,
    )
    scene = load_stored_scene(record.scene_json)
    marker = next(m for m in scene.markers if m.window_id == window.window_id)
    row = previous or CaptureMarkerSession(
        scene_id=scene_id,
        session_id=token,
        window_id=window.window_id,
        marker_json="",
        expires_at="",
    )
    row.session_id = token
    row.window_id = window.window_id
    row.marker_json = marker.model_dump_json()
    row.expires_at = (datetime.now(UTC) + timedelta(minutes=60)).isoformat()
    row.active = True
    db.add(row)
    db.flush()
    response = describe(row, db)
    db.commit()
    return response


@router.get("/{session_id}")
def read_capture(scene_id: str, session_id: UUID, db: DB) -> dict:
    row = db.get(CaptureMarkerSession, scene_id)
    if row is None or row.session_id != str(session_id):
        raise HTTPException(404, "Capture marker session not found")
    return describe(row, db)


@router.post("/{session_id}/end")
def end_capture(scene_id: str, session_id: UUID, db: DB) -> dict:
    lock_scene(scene_id, db)
    row = db.get(CaptureMarkerSession, scene_id)
    if row is None or row.session_id != str(session_id):
        raise HTTPException(409, "Capture marker session changed; cannot end another session")
    if row.active and film_matches(row, db):
        set_marker_mode(
            db,
            scene_id,
            MarkerModeRequest(
                window_id=row.window_id,
                enabled=False,
            ),
            commit=False,
        )
    row.active = False
    db.add(row)
    db.commit()
    return {"session_id": row.session_id, "status": "ended"}
