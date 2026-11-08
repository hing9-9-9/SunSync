"""FR-05 Virtual Film Device, Simulation Clock, and Multi-user Pairing Service."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from fastapi import HTTPException
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points
from sqlmodel import Session, col, select

from app.models.film import FilmApplicationLogRecord, FilmStateRecord
from app.models.scene import SceneRecord
from app.schemas.film import (
    AppliedCellState,
    CellCommand,
    FilmApplicationLogEntry,
    FilmApplicationLogListResponse,
    LogStatus,
    MarkerModeRequest,
    MarkerPairingRequest,
    MarkerPairingResponse,
    ScheduleApplyResponse,
    WindowFilmCommandRequest,
    WindowFilmStateResponse,
)
from app.schemas.geometry import EPS, cuboid_footprint
from app.schemas.scene import (
    ManualObservation,
    Marker,
    Occluder,
    OpticalProfile,
    Quality,
    Scene,
    Window,
)
from app.schemas.shading_plan import ShadingPlan, WindowPlan
from app.services.stored_scene import load_scene_for_pairing, load_stored_scene
from app.ui_scene import UISpaceRecord


def normalize_iso_timestamp(value: str | datetime) -> str:
    """Normalize any timezone-aware ISO 8601 timestamp to UTC YYYY-MM-DDTHH:MM:SSZ.

    Ensures that lexicographical string comparisons (<, <=, >, >=) and sorting in
    SQLite queries exactly correspond to true chronological ordering.
    """
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = value
    if dt.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone offset")
    dt_utc = dt.astimezone(UTC)
    if dt_utc.microsecond == 0:
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def opacity_to_applied_state(opacity: float) -> AppliedCellState:
    if abs(opacity - 0.0) <= 1e-6:
        return "transparent"
    if abs(opacity - 1.0) <= 1e-6:
        return "opaque"
    return "dimmed"


_ARUCO_CACHE: dict[int, list[list[float]]] = {}


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
    # cv2 marker: 0 is black (opaque), 255 is white (transparent)
    pattern = [[1.0 if float(img[r, c]) == 0.0 else 0.0 for c in range(6)] for r in range(6)]
    _ARUCO_CACHE[code_id] = pattern
    return [row[:] for row in pattern]


@runtime_checkable
class FilmDeviceProtocol(Protocol):
    """Hardware-agnostic protocol for any smart film (PDLC, SPD, etc.)."""

    film_type: str
    rows: int
    cols: int

    def get_opacity(self, row: int, col: int) -> float: ...
    def get_grid(self) -> list[list[float]]: ...
    def apply_commands(
        self, commands: Sequence[tuple[int, int, float] | CellCommand]
    ) -> list[tuple[int, int, float, float]]: ...


class VirtualFilmDevice:
    """Virtual smart film device with cell-addressable opacity state machine.

    Supports PDLC (binary opaque/transparent) and SPD (continuously dimmable)
    across arbitrary grid sizes (default 10x10).
    """

    def __init__(
        self,
        rows: int = 10,
        cols: int = 10,
        film_type: str = "PDLC",
        initial_cells: list[list[float]] | None = None,
    ) -> None:
        if (
            isinstance(rows, bool)
            or not isinstance(rows, int)
            or isinstance(cols, bool)
            or not isinstance(cols, int)
        ):
            raise TypeError(f"Grid dimensions must be integers, got rows={rows!r}, cols={cols!r}")
        if rows < 1 or cols < 1:
            raise ValueError(
                f"Grid dimensions must be positive integers, got rows={rows}, cols={cols}"
            )
        if not film_type or not isinstance(film_type, str):
            raise ValueError(f"film_type must be a non-empty string, got {film_type!r}")

        self.rows = rows
        self.cols = cols
        self.film_type = film_type
        self.marker_mode_active = False
        self.marker_code_id: int | None = None
        self.marker_start_row: int | None = None
        self.marker_start_col: int | None = None
        self.marker_backup: list[list[float]] | None = None

        if initial_cells is not None:
            if len(initial_cells) != rows or any(len(r) != cols for r in initial_cells):
                raise ValueError(f"initial_cells dimension does not match {rows}x{cols}")
            self.cells = []
            for r in range(rows):
                row_vals: list[float] = []
                for c in range(cols):
                    v = initial_cells[r][c]
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        raise TypeError(f"Invalid cell value {v} at ({r}, {c})")
                    fv = float(v)
                    if math.isnan(fv) or math.isinf(fv) or not (0.0 <= fv <= 1.0):
                        raise ValueError(f"Invalid opacity {v} at ({r}, {c})")
                    row_vals.append(fv)
                self.cells.append(row_vals)
        else:
            self.cells = [[0.0 for _ in range(cols)] for _ in range(rows)]

    def get_opacity(self, row: int, col: int) -> float:
        if (
            isinstance(row, bool)
            or not isinstance(row, int)
            or isinstance(col, bool)
            or not isinstance(col, int)
        ):
            raise TypeError(f"Cell coordinates must be integers, got ({row!r}, {col!r})")
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise ValueError(
                f"Cell coordinate ({row}, {col}) out of bounds ({self.rows}x{self.cols})"
            )
        return self.cells[row][col]

    def get_grid(self) -> list[list[float]]:
        return [row[:] for row in self.cells]

    def apply_commands(
        self, commands: Sequence[tuple[int, int, float] | CellCommand]
    ) -> list[tuple[int, int, float, float]]:
        # Phase 1: validate all commands before mutating state
        normalized: list[tuple[int, int, float]] = []
        for cmd in commands:
            if isinstance(cmd, CellCommand):
                r, c = cmd.row, cmd.col
                op = cmd.opacity if cmd.opacity is not None else 0.0
            elif isinstance(cmd, (tuple, list)) and len(cmd) == 3:
                r, c, op = cmd
            else:
                raise TypeError(
                    f"Command must be CellCommand or 3-tuple (row, col, opacity), got {cmd!r}"
                )

            if (
                isinstance(r, bool)
                or not isinstance(r, int)
                or isinstance(c, bool)
                or not isinstance(c, int)
            ):
                raise TypeError(f"Cell coordinates must be integers, got ({r!r}, {c!r})")
            if not (0 <= r < self.rows and 0 <= c < self.cols):
                raise ValueError(
                    f"Cell coordinate ({r}, {c}) out of bounds ({self.rows}x{self.cols})"
                )
            if isinstance(op, bool) or not isinstance(op, (int, float)):
                raise TypeError(f"Invalid opacity value {op!r}")
            fop = float(op)
            if math.isnan(fop) or math.isinf(fop) or not (0.0 <= fop <= 1.0):
                raise ValueError(f"Opacity must be in range [0.0, 1.0], got {op}")
            normalized.append((r, c, fop))

        # Phase 2: apply atomically
        results: list[tuple[int, int, float, float]] = []
        for r, c, op in normalized:
            old_op = self.cells[r][c]
            self.cells[r][c] = op
            results.append((r, c, old_op, op))
            if self.marker_backup is not None:
                self.marker_backup[r][c] = op
        return results

    def enable_marker_mode(self, code_id: int, start_row: int = 2, start_col: int = 2) -> None:
        """FR-05.2: Display 6x6 ArUco DICT_4X4_50 marker on smart film."""
        if isinstance(code_id, bool) or not isinstance(code_id, int) or not (0 <= code_id < 50):
            raise ValueError(f"ArUco code_id must be in 0..49, got {code_id!r}")
        if (
            isinstance(start_row, bool)
            or not isinstance(start_row, int)
            or not (0 <= start_row and start_row + 6 <= self.rows)
        ):
            raise ValueError(f"Marker start_row={start_row} exceeds film rows {self.rows}")
        if (
            isinstance(start_col, bool)
            or not isinstance(start_col, int)
            or not (0 <= start_col and start_col + 6 <= self.cols)
        ):
            raise ValueError(f"Marker start_col={start_col} exceeds film cols {self.cols}")

        if not self.marker_mode_active:
            self.marker_backup = [row[:] for row in self.cells]
        elif self.marker_backup is not None:
            self.cells = [row[:] for row in self.marker_backup]

        pattern = generate_aruco_4x4_pattern(code_id)
        for r in range(6):
            for c in range(6):
                self.cells[start_row + r][start_col + c] = pattern[r][c]

        self.marker_mode_active = True
        self.marker_code_id = code_id
        self.marker_start_row = start_row
        self.marker_start_col = start_col

    def disable_marker_mode(self) -> None:
        """FR-05.2: Restore exact pre-marker cell opacities."""
        if self.marker_mode_active:
            if self.marker_backup is not None:
                self.cells = [row[:] for row in self.marker_backup]
            self.marker_backup = None
            self.marker_mode_active = False
            self.marker_code_id = None
            self.marker_start_row = None
            self.marker_start_col = None


def evaluate_window_plan_at(window_plan: WindowPlan, at: datetime | str) -> list[list[float]]:
    """Instantaneously evaluate film cell opacities at timestamp `at` (Simulation Clock)."""
    at_dt = datetime.fromisoformat(at) if isinstance(at, str) else at
    grid = [[0.0 for _ in range(window_plan.film_cols)] for _ in range(window_plan.film_rows)]
    for cell in window_plan.cells:
        for seg in cell.segments:
            seg_start = datetime.fromisoformat(seg.start_time)
            seg_end = datetime.fromisoformat(seg.end_time)
            if seg_start <= at_dt < seg_end:
                if seg.state in ("opaque", "infeasible", "unknown"):
                    grid[cell.row][cell.col] = 1.0
                else:
                    grid[cell.row][cell.col] = 0.0
                break
    return grid


def apply_schedule_to_scene(
    session: Session,
    scene_id: str,
    plan: ShadingPlan,
    at: str | None = None,
    plan_id: str | None = None,
) -> ScheduleApplyResponse:
    """Apply an FR-02 ShadingPlan across scene virtual films, recording transitions and persistent logs."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    if at is not None:
        try:
            evaluated_at_str = normalize_iso_timestamp(at)
            at_dt = datetime.fromisoformat(evaluated_at_str)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422, detail="at must be a valid timezone-aware ISO 8601 timestamp"
            ) from None
    else:
        at_dt = None
        all_starts = [
            seg.start_time for win in plan.windows for cell in win.cells for seg in cell.segments
        ]
        evaluated_at_str = (
            normalize_iso_timestamp(max(all_starts))
            if all_starts
            else normalize_iso_timestamp(plan.start_time)
        )

    resolved_plan_id = (
        plan_id if plan_id is not None else f"plan_{scene_id}_{int(datetime.now(UTC).timestamp())}"
    )

    # Purge existing logs for this exact plan_id to guarantee idempotency on retries/re-applications
    existing_plan_logs = session.exec(
        select(FilmApplicationLogRecord)
        .where(FilmApplicationLogRecord.scene_id == scene_id)
        .where(FilmApplicationLogRecord.plan_id == resolved_plan_id)
    ).all()
    for el in existing_plan_logs:
        session.delete(el)

    applied_count = 0
    target_unmet_count = 0
    window_responses: list[WindowFilmStateResponse] = []
    now_iso = normalize_iso_timestamp(datetime.now(UTC))

    for window_plan in plan.windows:
        state_key = f"{scene_id}:{window_plan.window_id}"
        record = session.get(FilmStateRecord, state_key)
        if record is None:
            initial_cells = [
                [0.0 for _ in range(window_plan.film_cols)] for _ in range(window_plan.film_rows)
            ]
            record = FilmStateRecord(
                state_key=state_key,
                scene_id=scene_id,
                window_id=window_plan.window_id,
                film_type="PDLC",
                film_rows=window_plan.film_rows,
                film_cols=window_plan.film_cols,
                cells_json=json.dumps(initial_cells),
                updated_at=now_iso,
            )
        else:
            initial_cells = json.loads(record.cells_json)

        # Replay cell transitions chronologically for logging
        for cell_plan in window_plan.cells:
            # Baseline cell opacity before any segments of the plan take effect
            current_op = 0.0
            # Guarantee chronological segment order
            sorted_segments = sorted(
                cell_plan.segments, key=lambda s: datetime.fromisoformat(s.start_time)
            )

            for idx, seg in enumerate(sorted_segments):
                seg_start_iso = normalize_iso_timestamp(seg.start_time)
                seg_end_iso = normalize_iso_timestamp(seg.end_time)
                seg_start_dt = datetime.fromisoformat(seg_start_iso)
                seg_end_dt = datetime.fromisoformat(seg_end_iso)
                seg_op = 1.0 if seg.state in ("opaque", "infeasible", "unknown") else 0.0

                seg_status: LogStatus
                seg_note: str
                if seg.state == "infeasible":
                    seg_status = "target_unmet"
                    seg_note = "목표 미달"
                elif seg.state == "unknown":
                    seg_status = "unknown_intensity"
                    seg_note = "조도 미상"
                else:
                    seg_status = "applied"
                    seg_note = ""

                reasons_json = json.dumps([r.model_dump() for r in seg.reasons])

                # Segment start transition
                if at_dt is None or seg_start_dt <= at_dt:
                    prev_op = current_op
                    new_op = seg_op
                    must_log = (abs(prev_op - new_op) > 1e-6) or (
                        seg.state in ("infeasible", "unknown")
                    )
                    if must_log:
                        log_rec = FilmApplicationLogRecord(
                            scene_id=scene_id,
                            window_id=window_plan.window_id,
                            timestamp=seg_start_iso,
                            row=cell_plan.row,
                            col=cell_plan.col,
                            previous_opacity=prev_op,
                            new_opacity=new_op,
                            previous_state=opacity_to_applied_state(prev_op),
                            new_state=opacity_to_applied_state(new_op),
                            plan_id=resolved_plan_id,
                            status=seg_status,
                            note=seg_note,
                            reasons_json=reasons_json,
                            created_at=now_iso,
                        )
                        session.add(log_rec)
                        if seg_status == "target_unmet":
                            target_unmet_count += 1
                        else:
                            applied_count += 1
                    current_op = seg_op

                # Segment end transition (if no adjacent next segment starts at seg.end_time)
                has_next = (
                    idx + 1 < len(sorted_segments)
                    and sorted_segments[idx + 1].start_time == seg.end_time
                )
                if not has_next and (at_dt is None or seg_end_dt <= at_dt):
                    prev_op = current_op
                    new_op = 0.0
                    if abs(prev_op - new_op) > 1e-6:
                        log_rec = FilmApplicationLogRecord(
                            scene_id=scene_id,
                            window_id=window_plan.window_id,
                            timestamp=seg_end_iso,
                            row=cell_plan.row,
                            col=cell_plan.col,
                            previous_opacity=prev_op,
                            new_opacity=new_op,
                            previous_state=opacity_to_applied_state(prev_op),
                            new_state="transparent",
                            plan_id=resolved_plan_id,
                            status="applied",
                            note="",
                            reasons_json="[]",
                            created_at=now_iso,
                        )
                        session.add(log_rec)
                        applied_count += 1
                    current_op = 0.0

        # Evaluate final film state at evaluated_at_str
        grid_at = evaluate_window_plan_at(window_plan, evaluated_at_str)
        record.cells_json = json.dumps(grid_at)
        record.active_plan_id = resolved_plan_id
        record.active_plan_json = plan.model_dump_json()
        record.updated_at = now_iso
        session.add(record)

        window_responses.append(
            WindowFilmStateResponse(
                scene_id=scene_id,
                window_id=window_plan.window_id,
                film_type="PDLC" if record.film_type not in ("PDLC", "SPD") else record.film_type,  # type: ignore[arg-type]
                film_rows=record.film_rows,
                film_cols=record.film_cols,
                cells=grid_at,
                states=[[opacity_to_applied_state(c) for c in row] for row in grid_at],
                marker_mode_active=record.marker_mode_active,
                marker_code_id=record.marker_code_id,
                active_plan_id=record.active_plan_id,
                evaluated_at=evaluated_at_str,
                updated_at=record.updated_at,
            )
        )

    session.commit()
    return ScheduleApplyResponse(
        scene_id=scene_id,
        plan_id=resolved_plan_id,
        applied_records_count=applied_count,
        target_unmet_count=target_unmet_count,
        evaluated_at=evaluated_at_str,
        windows=window_responses,
    )


def apply_film_commands(
    session: Session,
    scene_id: str,
    request: WindowFilmCommandRequest,
) -> WindowFilmStateResponse:
    """Apply manual batch cell commands to a virtual film window."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    scene = load_stored_scene(scene_record.scene_json)
    target_window_id = (
        request.window_id
        if request.window_id is not None
        else (scene.windows[0].window_id if scene.windows else "window_default")
    )

    state_key = f"{scene_id}:{target_window_id}"
    record = session.get(FilmStateRecord, state_key)
    target_window = next((w for w in scene.windows if w.window_id == target_window_id), None)
    rows = target_window.film_rows if target_window else 10
    cols = target_window.film_cols if target_window else 10
    film_type = request.film_type or (record.film_type if record else "PDLC")

    if record is None:
        device = VirtualFilmDevice(rows=rows, cols=cols, film_type=film_type)
        record = FilmStateRecord(
            state_key=state_key,
            scene_id=scene_id,
            window_id=target_window_id,
            film_type=film_type,
            film_rows=rows,
            film_cols=cols,
            cells_json=json.dumps(device.get_grid()),
            updated_at=datetime.now(UTC).isoformat(),
        )
    else:
        device = VirtualFilmDevice(
            rows=record.film_rows,
            cols=record.film_cols,
            film_type=record.film_type,
            initial_cells=json.loads(record.cells_json),
        )
        if record.marker_mode_active:
            device.marker_mode_active = True
            device.marker_code_id = record.marker_code_id
            device.marker_start_row = record.marker_start_row
            device.marker_start_col = record.marker_start_col
            if record.marker_backup_json:
                device.marker_backup = json.loads(record.marker_backup_json)

    # Validate coordinates
    for cmd in request.commands:
        if not (0 <= cmd.row < device.rows and 0 <= cmd.col < device.cols):
            raise HTTPException(
                status_code=422,
                detail=f"Cell coordinate ({cmd.row}, {cmd.col}) out of bounds ({device.rows}x{device.cols})",
            )

    transitions = device.apply_commands(request.commands)
    timestamp = (
        normalize_iso_timestamp(request.timestamp)
        if request.timestamp
        else normalize_iso_timestamp(datetime.now(UTC))
    )
    now_iso = normalize_iso_timestamp(datetime.now(UTC))

    for r, c, prev_op, new_op in transitions:
        log_entry = FilmApplicationLogRecord(
            scene_id=scene_id,
            window_id=target_window_id,
            timestamp=timestamp,
            row=r,
            col=c,
            previous_opacity=prev_op,
            new_opacity=new_op,
            previous_state=opacity_to_applied_state(prev_op),
            new_state=opacity_to_applied_state(new_op),
            plan_id=request.plan_id,
            status="applied",
            note=request.note,
            reasons_json="[]",
            created_at=now_iso,
        )
        session.add(log_entry)

    record.cells_json = json.dumps(device.get_grid())
    record.updated_at = now_iso
    session.add(record)
    session.commit()

    grid = device.get_grid()
    return WindowFilmStateResponse(
        scene_id=scene_id,
        window_id=target_window_id,
        film_type="PDLC" if record.film_type not in ("PDLC", "SPD") else record.film_type,  # type: ignore[arg-type]
        film_rows=record.film_rows,
        film_cols=record.film_cols,
        cells=grid,
        states=[[opacity_to_applied_state(c) for c in row] for row in grid],
        marker_mode_active=record.marker_mode_active,
        marker_code_id=record.marker_code_id,
        active_plan_id=record.active_plan_id,
        evaluated_at=timestamp,
        updated_at=record.updated_at,
    )


def set_marker_mode(
    session: Session,
    scene_id: str,
    request: MarkerModeRequest,
    *,
    commit: bool = True,
) -> WindowFilmStateResponse:
    """FR-05.2: Toggle 6x6 ArUco marker display on smart film window."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    scene = load_stored_scene(scene_record.scene_json)
    target_window_id = (
        request.window_id
        if request.window_id is not None
        else (scene.windows[0].window_id if scene.windows else "window_default")
    )

    state_key = f"{scene_id}:{target_window_id}"
    record = session.get(FilmStateRecord, state_key)
    target_window = next((w for w in scene.windows if w.window_id == target_window_id), None)
    rows = target_window.film_rows if target_window else 10
    cols = target_window.film_cols if target_window else 10

    if record is None:
        device = VirtualFilmDevice(rows=rows, cols=cols, film_type="PDLC")
        record = FilmStateRecord(
            state_key=state_key,
            scene_id=scene_id,
            window_id=target_window_id,
            film_type="PDLC",
            film_rows=rows,
            film_cols=cols,
            cells_json=json.dumps(device.get_grid()),
            updated_at=datetime.now(UTC).isoformat(),
        )
    else:
        device = VirtualFilmDevice(
            rows=record.film_rows,
            cols=record.film_cols,
            film_type=record.film_type,
            initial_cells=json.loads(record.cells_json),
        )
        if record.marker_mode_active:
            device.marker_mode_active = True
            device.marker_code_id = record.marker_code_id
            device.marker_start_row = record.marker_start_row
            device.marker_start_col = record.marker_start_col
            if record.marker_backup_json:
                device.marker_backup = json.loads(record.marker_backup_json)

    old_grid = device.get_grid()
    if request.enabled:
        if not (0 <= request.start_row and request.start_row + 6 <= device.rows):
            raise HTTPException(status_code=422, detail="Marker rows exceed window film grid")
        if not (0 <= request.start_col and request.start_col + 6 <= device.cols):
            raise HTTPException(status_code=422, detail="Marker cols exceed window film grid")
        device.enable_marker_mode(
            code_id=request.code_id,
            start_row=request.start_row,
            start_col=request.start_col,
        )
        record.marker_mode_active = True
        record.marker_code_id = request.code_id
        record.marker_start_row = request.start_row
        record.marker_start_col = request.start_col
        record.marker_backup_json = (
            json.dumps(device.marker_backup) if device.marker_backup is not None else None
        )
    else:
        device.disable_marker_mode()
        record.marker_mode_active = False
        record.marker_code_id = None
        record.marker_start_row = None
        record.marker_start_col = None
        record.marker_backup_json = None

    new_grid = device.get_grid()
    timestamp = (
        normalize_iso_timestamp(request.timestamp)
        if request.timestamp
        else normalize_iso_timestamp(datetime.now(UTC))
    )
    now_iso = normalize_iso_timestamp(datetime.now(UTC))

    for r in range(device.rows):
        for c in range(device.cols):
            if abs(old_grid[r][c] - new_grid[r][c]) > 1e-6:
                session.add(
                    FilmApplicationLogRecord(
                        scene_id=scene_id,
                        window_id=target_window_id,
                        timestamp=timestamp,
                        row=r,
                        col=c,
                        previous_opacity=old_grid[r][c],
                        new_opacity=new_grid[r][c],
                        previous_state=opacity_to_applied_state(old_grid[r][c]),
                        new_state=opacity_to_applied_state(new_grid[r][c]),
                        plan_id=None,
                        status="applied",
                        note="marker_mode",
                        reasons_json="[]",
                        created_at=now_iso,
                    )
                )

    record.cells_json = json.dumps(new_grid)
    record.updated_at = now_iso
    session.add(record)
    if commit:
        session.commit()
    else:
        session.flush()

    return WindowFilmStateResponse(
        scene_id=scene_id,
        window_id=target_window_id,
        film_type="PDLC" if record.film_type not in ("PDLC", "SPD") else record.film_type,  # type: ignore[arg-type]
        film_rows=record.film_rows,
        film_cols=record.film_cols,
        cells=new_grid,
        states=[[opacity_to_applied_state(c) for c in row] for row in new_grid],
        marker_mode_active=record.marker_mode_active,
        marker_code_id=record.marker_code_id,
        active_plan_id=record.active_plan_id,
        evaluated_at=timestamp,
        updated_at=record.updated_at,
    )


def get_scene_film_state(
    session: Session,
    scene_id: str,
    window_id: str | None = None,
    at: str | None = None,
) -> list[WindowFilmStateResponse]:
    """Retrieve or evaluate virtual film state(s) for a scene."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    scene = load_stored_scene(scene_record.scene_json)

    if at is not None:
        try:
            evaluated_at_str = normalize_iso_timestamp(at)
            at_dt = datetime.fromisoformat(evaluated_at_str)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422, detail="at must be a valid timezone-aware ISO 8601 timestamp"
            ) from None
    else:
        at_dt = None
        evaluated_at_str = normalize_iso_timestamp(datetime.now(UTC))

    # Determine windows
    if window_id is not None:
        target_window_ids = [window_id]
    else:
        target_window_ids = [w.window_id for w in scene.windows]
        recorded_states = session.exec(
            select(FilmStateRecord).where(FilmStateRecord.scene_id == scene_id)
        ).all()
        for rec in recorded_states:
            if rec.window_id not in target_window_ids:
                target_window_ids.append(rec.window_id)
        if not target_window_ids:
            target_window_ids = ["window_default"]

    responses: list[WindowFilmStateResponse] = []
    for w_id in target_window_ids:
        state_key = f"{scene_id}:{w_id}"
        record = session.get(FilmStateRecord, state_key)
        target_win = next((w for w in scene.windows if w.window_id == w_id), None)
        rows = target_win.film_rows if target_win else (record.film_rows if record else 10)
        cols = target_win.film_cols if target_win else (record.film_cols if record else 10)
        film_type = record.film_type if record else "PDLC"

        if at_dt is not None:
            if record is not None and record.active_plan_json:
                plan = ShadingPlan.model_validate_json(record.active_plan_json)
                win_plan = next((wp for wp in plan.windows if wp.window_id == w_id), None)
                if win_plan is not None:
                    grid = evaluate_window_plan_at(win_plan, at_dt)
                else:
                    grid = [[0.0 for _ in range(cols)] for _ in range(rows)]
            else:
                logs = session.exec(
                    select(FilmApplicationLogRecord)
                    .where(FilmApplicationLogRecord.scene_id == scene_id)
                    .where(FilmApplicationLogRecord.window_id == w_id)
                    .where(FilmApplicationLogRecord.timestamp <= evaluated_at_str)
                    .order_by(
                        col(FilmApplicationLogRecord.timestamp).asc(),
                        col(FilmApplicationLogRecord.log_id).asc(),
                    )
                ).all()
                grid = [[0.0 for _ in range(cols)] for _ in range(rows)]
                for entry in logs:
                    if 0 <= entry.row < rows and 0 <= entry.col < cols:
                        grid[entry.row][entry.col] = entry.new_opacity
        else:
            if record is not None:
                grid = json.loads(record.cells_json)
            else:
                grid = [[0.0 for _ in range(cols)] for _ in range(rows)]

        responses.append(
            WindowFilmStateResponse(
                scene_id=scene_id,
                window_id=w_id,
                film_type="PDLC" if film_type not in ("PDLC", "SPD") else film_type,  # type: ignore[arg-type]
                film_rows=rows,
                film_cols=cols,
                cells=grid,
                states=[[opacity_to_applied_state(c) for c in row] for row in grid],
                marker_mode_active=record.marker_mode_active if record else False,
                marker_code_id=record.marker_code_id if record else None,
                active_plan_id=record.active_plan_id if record else None,
                evaluated_at=evaluated_at_str,
                updated_at=record.updated_at if record else datetime.now(UTC).isoformat(),
            )
        )

    return responses


def get_film_logs(
    session: Session,
    scene_id: str,
    window_id: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 500,
) -> FilmApplicationLogListResponse:
    """Query chronological application logs with optional filters."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    query = select(FilmApplicationLogRecord).where(FilmApplicationLogRecord.scene_id == scene_id)
    if window_id is not None:
        query = query.where(FilmApplicationLogRecord.window_id == window_id)
    if status is not None:
        query = query.where(FilmApplicationLogRecord.status == status)
    if since is not None:
        try:
            norm_since = normalize_iso_timestamp(since)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422, detail="since must be a valid timezone-aware ISO 8601 timestamp"
            ) from None
        query = query.where(FilmApplicationLogRecord.timestamp >= norm_since)
    if until is not None:
        try:
            norm_until = normalize_iso_timestamp(until)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422, detail="until must be a valid timezone-aware ISO 8601 timestamp"
            ) from None
        query = query.where(FilmApplicationLogRecord.timestamp <= norm_until)

    query = query.order_by(
        col(FilmApplicationLogRecord.timestamp).asc(), col(FilmApplicationLogRecord.log_id).asc()
    ).limit(limit)
    records = session.exec(query).all()

    entries: list[FilmApplicationLogEntry] = []
    for rec in records:
        reasons = json.loads(rec.reasons_json)
        entries.append(
            FilmApplicationLogEntry(
                log_id=rec.log_id,
                scene_id=rec.scene_id,
                window_id=rec.window_id,
                timestamp=rec.timestamp,
                row=rec.row,
                col=rec.col,
                previous_opacity=rec.previous_opacity,
                new_opacity=rec.new_opacity,
                previous_state=rec.previous_state,  # type: ignore[arg-type]
                new_state=rec.new_state,  # type: ignore[arg-type]
                plan_id=rec.plan_id,
                status=rec.status,  # type: ignore[arg-type]
                note=rec.note,
                reasons=reasons,
                created_at=rec.created_at,
            )
        )

    return FilmApplicationLogListResponse(
        scene_id=scene_id,
        total=len(entries),
        logs=entries,
    )


def clamp_cuboid_to_room(
    base_center: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    yaw: float,
    floor_poly: Polygon,
    room_height: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float], bool]:
    """Clamp furniture cuboid footprint and height inside the room boundaries."""
    cx, cy, cz = base_center
    w, d, h = dimensions
    was_clamped = False

    if cz < 0.0:
        cz = 0.0
        was_clamped = True
    if h > room_height:
        h = max(0.1, round(room_height * 0.95, 4))
        was_clamped = True
    if cz + h > room_height:
        cz = max(0.0, round(room_height - h, 4))
        was_clamped = True

    footprint = Polygon([(p[0], p[1]) for p in cuboid_footprint((cx, cy, cz), (w, d, h), yaw)])
    if not floor_poly.buffer(1e-4).covers(footprint):
        was_clamped = True
        if not floor_poly.buffer(1e-4).covers(Point(cx, cy)):
            pt = nearest_points(floor_poly, Point(cx, cy))[0]
            cx, cy = pt.x, pt.y
        centroid = floor_poly.representative_point()
        for _ in range(100):
            footprint = Polygon(
                [(p[0], p[1]) for p in cuboid_footprint((cx, cy, cz), (w, d, h), yaw)]
            )
            if floor_poly.buffer(1e-5).covers(footprint):
                break
            cx = 0.85 * cx + 0.15 * centroid.x
            cy = 0.85 * cy + 0.15 * centroid.y
            w = max(0.1, w * 0.95)
            d = max(0.1, d * 0.95)

    rcx, rcy, rcz = round(cx, 4), round(cy, 4), round(cz, 4)
    rw, rd, rh = round(w, 4), round(d, 4), round(h, 4)
    r_footprint = Polygon(
        [(p[0], p[1]) for p in cuboid_footprint((rcx, rcy, rcz), (rw, rd, rh), yaw)]
    )
    if not floor_poly.buffer(1e-4).covers(r_footprint):
        was_clamped = True
        centroid = floor_poly.representative_point()
        for _ in range(20):
            rcx = round(0.9 * rcx + 0.1 * centroid.x, 4)
            rcy = round(0.9 * rcy + 0.1 * centroid.y, 4)
            rw = max(0.1, round(rw * 0.95, 4))
            rd = max(0.1, round(rd * 0.95, 4))
            r_footprint = Polygon(
                [(p[0], p[1]) for p in cuboid_footprint((rcx, rcy, rcz), (rw, rd, rh), yaw)]
            )
            if floor_poly.buffer(1e-5).covers(r_footprint):
                break

    return (
        (rcx, rcy, rcz),
        (rw, rd, rh),
        was_clamped,
    )


def provision_default_window(scene: Scene) -> Window:
    """Create a default 1.0m x 1.0m window with 10x10 film cells on a valid room wall."""
    floor_vertices = scene.room.floor_vertices_m
    floor_poly = Polygon([(p[0], p[1]) for p in floor_vertices])
    height = scene.room.height_m

    # Find first horizontal edge >= 1.0m
    found_edge: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    for i in range(len(floor_vertices)):
        p_a = floor_vertices[i]
        p_b = floor_vertices[(i + 1) % len(floor_vertices)]
        edge_len = math.hypot(p_b[0] - p_a[0], p_b[1] - p_a[1])
        if edge_len >= 1.0 - EPS:
            found_edge = (p_a, p_b)
            break

    if found_edge is None:
        p_a = (0.0, 0.0, 0.0)
        p_b = (5.0, 0.0, 0.0)
    else:
        p_a, p_b = found_edge

    dx = p_b[0] - p_a[0]
    dy = p_b[1] - p_a[1]
    l_edge = math.hypot(dx, dy)
    ex = dx / l_edge
    ey = dy / l_edge

    # Inward normal test
    n_in = (-ey, ex, 0.0)
    mid_wall = ((p_a[0] + p_b[0]) / 2.0, (p_a[1] + p_b[1]) / 2.0)
    test_pt = Point(mid_wall[0] + n_in[0] * 0.05, mid_wall[1] + n_in[1] * 0.05)
    if not floor_poly.contains(test_pt):
        n_in = (ey, -ex, 0.0)

    # u_axis must satisfy cross(u_axis, v_axis=(0,0,1)) == n_in
    u_x = -n_in[1]
    u_y = n_in[0]
    u_axis = (u_x, u_y, 0.0)
    v_axis = (0.0, 0.0, 1.0)

    # 1.0m window centered along edge
    p0_xy = (mid_wall[0] - 0.5 * u_axis[0], mid_wall[1] - 0.5 * u_axis[1])
    p1_xy = (mid_wall[0] + 0.5 * u_axis[0], mid_wall[1] + 0.5 * u_axis[1])
    z_bottom = 1.0 if height >= 2.2 else max(0.0, (height - 1.0) / 2.0)
    z_top = z_bottom + 1.0

    p0 = (round(p0_xy[0], 4), round(p0_xy[1], 4), round(z_bottom, 4))
    p1 = (round(p1_xy[0], 4), round(p1_xy[1], 4), round(z_bottom, 4))
    p2 = (round(p1_xy[0], 4), round(p1_xy[1], 4), round(z_top, 4))
    p3 = (round(p0_xy[0], 4), round(p0_xy[1], 4), round(z_top, 4))

    azimuth = math.degrees(math.atan2(-n_in[0], -n_in[1])) % 360.0

    # Ensure optical profile and observation exist
    if scene.optical_profiles:
        opt_id = scene.optical_profiles[0].optical_profile_id
    else:
        opt_id = "optical_profile_default"
        scene.optical_profiles.append(
            OpticalProfile(
                optical_profile_id=opt_id,
                status="assumed",
                model="direct_scalar_transmittance",
                glazing_direct_transmittance=0.8,
                film_min_direct_transmittance=0.05,
                film_max_direct_transmittance=0.7,
                actual_sgf_properties=None,
            )
        )

    if scene.observations:
        obs_id = scene.observations[0].observation_id
    else:
        obs_id = "obs_default"
        scene.observations.append(
            ManualObservation(
                observation_id=obs_id,
                method="manual",
                captured_at=datetime.now(UTC).isoformat(),
                uncertainty_m=0.01,
                confirmed=True,
                note="auto-provisioned observation",
            )
        )

    return Window(
        window_id="window_default",
        room_id=scene.room.room_id,
        vertices_m=[p0, p1, p2, p3],
        inward_normal=(round(n_in[0], 6), round(n_in[1], 6), 0.0),
        u_axis=(round(u_axis[0], 6), round(u_axis[1], 6), 0.0),
        v_axis=v_axis,
        azimuth_deg_true=round(azimuth, 4),
        optical_profile_id=opt_id,
        initial_film_direct_transmittance=0.1,
        quality=Quality(observation_ids=[obs_id], confirmed=True),
        film_rows=10,
        film_cols=10,
    )


def pair_marker_for_scene(
    session: Session,
    scene_id: str,
    request: MarkerPairingRequest,
    *,
    commit: bool = True,
) -> MarkerPairingResponse:
    """Multi-user isolated ArUco auto-bind and room-boundary clamping."""
    scene_record = session.get(SceneRecord, scene_id)
    if scene_record is None:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    scene = load_scene_for_pairing(scene_record.scene_json)

    # 1. Target window
    target_window: Window
    if request.window_id is not None:
        win = next((w for w in scene.windows if w.window_id == request.window_id), None)
        if win is None:
            raise HTTPException(
                status_code=404,
                detail=f"Window '{request.window_id}' not found in scene '{scene_id}'",
            )
        target_window = win
    elif scene.windows:
        target_window = scene.windows[0]
    elif request.auto_provision_window:
        target_window = provision_default_window(scene)
        scene.windows.append(target_window)
    else:
        raise HTTPException(
            status_code=400,
            detail="Scene has no windows and auto_provision_window is False",
        )

    # Ensure cells are square for ArUco 6x6 placement
    if abs(target_window.cell_width_m - target_window.cell_height_m) > EPS:
        if abs(target_window.width_m - target_window.height_m) <= EPS:
            target_window.film_rows = 10
            target_window.film_cols = 10
        else:
            cell_size = target_window.cell_height_m
            target_window.film_cols = max(1, round(target_window.width_m / cell_size))

    # 2. ArUco code_id assignment (scoped to scene)
    used_codes = {m.code_id for m in scene.markers if m.window_id != target_window.window_id}
    if request.code_id not in used_codes:
        assigned_code = request.code_id
    else:
        assigned_code = next(c for c in range(50) if c not in used_codes)

    # 3. Rigid transform T_ENU_marker on target window plane
    start_row = 2
    start_col = 2
    marker_rows = 6
    marker_cols = 6
    side_length = round(6 * target_window.cell_width_m, 6)

    u_center = (start_col + 3) * target_window.cell_width_m
    v_center = target_window.height_m - (start_row + 3) * target_window.cell_height_m
    t_enu = target_window.marker_transform(u_center, v_center)

    marker_id = f"marker_{target_window.window_id}"
    existing_marker = next(
        (m for m in scene.markers if m.window_id == target_window.window_id), None
    )
    if existing_marker is not None:
        marker_id = existing_marker.marker_id
        existing_marker.code_id = assigned_code
        existing_marker.side_length_m = side_length
        existing_marker.T_ENU_marker = t_enu
        existing_marker.start_row = start_row
        existing_marker.start_col = start_col
        existing_marker.marker_rows = marker_rows
        existing_marker.marker_cols = marker_cols
    else:
        new_marker = Marker(
            marker_id=marker_id,
            family="aruco",
            dictionary="DICT_4X4_50",
            code_id=assigned_code,
            side_length_m=side_length,
            plane="window",
            T_ENU_marker=t_enu,
            window_id=target_window.window_id,
            start_row=start_row,
            start_col=start_col,
            marker_rows=marker_rows,
            marker_cols=marker_cols,
        )
        scene.markers.append(new_marker)

    # 4. Furniture clamping
    clamped_ids: list[str] = []
    floor_poly = Polygon([(p[0], p[1]) for p in scene.room.floor_vertices_m])
    room_h = scene.room.height_m

    if request.furniture:
        for f_in in request.furniture:
            base_c, dims, was_cl = clamp_cuboid_to_room(
                f_in.base_center_m,
                f_in.dimensions_m,
                f_in.yaw_deg_ccw_from_east,
                floor_poly,
                room_h,
            )
            if was_cl:
                clamped_ids.append(f_in.occluder_id)
            existing_occ = next(
                (o for o in scene.occluders if o.occluder_id == f_in.occluder_id), None
            )
            if existing_occ is not None:
                existing_occ.name = f_in.name
                existing_occ.type = f_in.type
                existing_occ.base_center_m = base_c
                existing_occ.dimensions_m = dims
                existing_occ.yaw_deg_ccw_from_east = f_in.yaw_deg_ccw_from_east
            else:
                obs_id = (
                    scene.observations[0].observation_id if scene.observations else "obs_default"
                )
                scene.occluders.append(
                    Occluder(
                        occluder_id=f_in.occluder_id,
                        room_id=scene.room.room_id,
                        name=f_in.name,
                        kind="cuboid",
                        type=f_in.type,
                        base_center_m=base_c,
                        dimensions_m=dims,
                        yaw_deg_ccw_from_east=f_in.yaw_deg_ccw_from_east,
                        material="opaque",
                        quality=Quality(observation_ids=[obs_id], confirmed=True),
                    )
                )

    if request.clamp_furniture_to_room and not request.furniture:
        for occ in scene.occluders:
            base_c, dims, was_cl = clamp_cuboid_to_room(
                occ.base_center_m, occ.dimensions_m, occ.yaw_deg_ccw_from_east, floor_poly, room_h
            )
            if was_cl:
                clamped_ids.append(occ.occluder_id)
                occ.base_center_m = base_c
                occ.dimensions_m = dims

    # 5. Persist scene with incremented revision
    scene.revision += 1
    if scene.scenario is not None:
        scene.scenario.scene_revision = scene.revision
    validated_scene = Scene.model_validate(scene.model_dump())
    scene_record.revision = validated_scene.revision
    scene_record.scene_json = validated_scene.model_dump_json()
    session.add(scene_record)

    # Keep UISpaceRecord in lockstep if space was created via /api/ui/spaces
    ui_space_record = session.exec(
        select(UISpaceRecord).where(UISpaceRecord.scene_id == scene_id)
    ).first()
    if ui_space_record is not None:
        ui_space_record.scene_revision = validated_scene.revision
        try:
            ui_payload = json.loads(ui_space_record.payload_json)
            ui_payload["serverRevision"] = validated_scene.revision
            ui_space_record.payload_json = json.dumps(ui_payload)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        session.add(ui_space_record)

    # 6. Film state marker activation
    state_key = f"{scene_id}:{target_window.window_id}"
    record = session.get(FilmStateRecord, state_key)
    if record is None:
        device = VirtualFilmDevice(
            rows=target_window.film_rows,
            cols=target_window.film_cols,
            film_type="PDLC",
        )
        record = FilmStateRecord(
            state_key=state_key,
            scene_id=scene_id,
            window_id=target_window.window_id,
            film_type="PDLC",
            film_rows=target_window.film_rows,
            film_cols=target_window.film_cols,
            cells_json=json.dumps(device.get_grid()),
            updated_at=datetime.now(UTC).isoformat(),
        )
    else:
        device = VirtualFilmDevice(
            rows=record.film_rows,
            cols=record.film_cols,
            film_type=record.film_type,
            initial_cells=json.loads(record.cells_json),
        )

    if request.enable_marker_display:
        device.enable_marker_mode(
            code_id=assigned_code,
            start_row=start_row,
            start_col=start_col,
        )
        record.marker_mode_active = True
        record.marker_code_id = assigned_code
        record.marker_start_row = start_row
        record.marker_start_col = start_col
        record.marker_backup_json = (
            json.dumps(device.marker_backup) if device.marker_backup is not None else None
        )
        record.cells_json = json.dumps(device.get_grid())
        record.updated_at = datetime.now(UTC).isoformat()
        session.add(record)

    if commit:
        session.commit()
    else:
        session.flush()

    grid = device.get_grid()
    film_resp = WindowFilmStateResponse(
        scene_id=scene_id,
        window_id=target_window.window_id,
        film_type="PDLC" if record.film_type not in ("PDLC", "SPD") else record.film_type,  # type: ignore[arg-type]
        film_rows=record.film_rows,
        film_cols=record.film_cols,
        cells=grid,
        states=[[opacity_to_applied_state(c) for c in row] for row in grid],
        marker_mode_active=record.marker_mode_active,
        marker_code_id=record.marker_code_id,
        active_plan_id=record.active_plan_id,
        evaluated_at=datetime.now(UTC).isoformat(),
        updated_at=record.updated_at,
    )

    return MarkerPairingResponse(
        scene_id=scene_id,
        scene_revision=validated_scene.revision,
        window_id=target_window.window_id,
        marker_id=marker_id,
        code_id=assigned_code,
        clamped_furniture_ids=clamped_ids,
        film_state=film_resp,
    )
