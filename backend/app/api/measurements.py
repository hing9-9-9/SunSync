"""Persistent measurement jobs; user identity is supplied by the account API layer."""

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, col, select

from app.api.ui_spaces import DB, get_space, replace_space
from app.models.scene import SceneRecord
from app.ownership import CurrentUser, may_access, require_space
from app.ui_scene import UISpace, UISpaceRecord


class MeasurementJob(SQLModel, table=True):
    token: str = Field(primary_key=True)
    space_id: str = Field(index=True)
    status: str = "waiting"
    created_at: str


class Start(BaseModel):
    space_id: str


router = APIRouter(prefix="/ui/measurements", tags=["measurements"])


def get_job(token: str, session: DB):
    row = session.get(MeasurementJob, token)
    if row is None:
        raise HTTPException(404, "측정 작업을 찾을 수 없습니다.")
    get_space(row.space_id, session)
    return row


def _owned(session: DB, space_id: str, user: CurrentUser) -> bool:
    record = session.get(UISpaceRecord, space_id)
    scene = session.get(SceneRecord, record.scene_id) if record is not None else None
    return scene is not None and may_access(user, scene.owner_id)


@router.get("")
def list_jobs(session: DB, user: CurrentUser):
    jobs = session.exec(
        select(MeasurementJob).order_by(col(MeasurementJob.created_at).desc())
    ).all()
    return [job for job in jobs if _owned(session, job.space_id, user)]


@router.post("", status_code=201)
def start(data: Start, session: DB, user: CurrentUser):
    require_space(session, data.space_id, user)
    get_space(data.space_id, session)
    existing = session.exec(
        select(MeasurementJob).where(
            MeasurementJob.space_id == data.space_id, MeasurementJob.status != "completed"
        )
    ).first()
    if existing:
        return existing
    row = MeasurementJob(
        token=secrets.token_urlsafe(32),
        space_id=data.space_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("/{token}")
def status(token: str, session: DB):
    row = get_job(token, session)
    return {**row.model_dump(), "space": get_space(row.space_id, session)}


@router.post("/{token}/connect")
def connect(token: str, session: DB):
    row = get_job(token, session)
    if row.status == "completed":
        raise HTTPException(409, "이미 완료된 측정입니다.")
    row.status = "connected"
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**row.model_dump(), "space": get_space(row.space_id, session)}


@router.post("/{token}/complete")
def complete(token: str, space: UISpace, session: DB):
    row = get_job(token, session)
    if row.status != "connected" or row.space_id != space.id:
        raise HTTPException(409, "측정 중인 공간을 확인해 주세요.")
    saved = replace_space(space.id, space, session)
    row.status = "completed"
    session.add(row)
    session.commit()
    return saved
