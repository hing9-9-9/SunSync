"""Expiring cross-device measurement sessions for the development UI."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Field as DBField
from sqlmodel import SQLModel, select

from app.api.ui_spaces import DB, get_space, replace_space
from app.ownership import CurrentUser, require_space
from app.ui_scene import UISpace


class Handoff(SQLModel, table=True):
    token: str = DBField(primary_key=True)
    code: str = DBField(index=True, unique=True)
    space_id: str
    status: str = "waiting"
    expires_at: str


class Start(BaseModel):
    space_id: str


class Claim(BaseModel):
    code: str = Field(min_length=8, max_length=8)


router = APIRouter(prefix="/ui/handoffs", tags=["handoffs"])


def current(token: str, session: DB) -> Handoff:
    row = session.get(Handoff, token)
    if row is None:
        raise HTTPException(404, "연결 정보를 찾을 수 없습니다.")
    if datetime.fromisoformat(row.expires_at) <= datetime.now(UTC):
        raise HTTPException(410, "연결 시간이 만료되었습니다. 웹에서 새 연결을 만들어 주세요.")
    return row


@router.post("", status_code=201)
def start(data: Start, session: DB, user: CurrentUser):
    require_space(session, data.space_id, user)
    get_space(data.space_id, session)
    code = secrets.token_hex(4).upper()
    while session.exec(select(Handoff).where(Handoff.code == code)).first():
        code = secrets.token_hex(4).upper()
    row = Handoff(
        token=secrets.token_urlsafe(32),
        code=code,
        space_id=data.space_id,
        expires_at=(datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.post("/claim")
def claim(data: Claim, session: DB, user: CurrentUser):
    row = session.exec(select(Handoff).where(Handoff.code == data.code.upper())).first()
    if row is None:
        raise HTTPException(404, "연결 코드를 확인해 주세요.")
    try:
        require_space(session, row.space_id, user)  # a code of another account looks unknown
    except HTTPException:
        raise HTTPException(404, "연결 코드를 확인해 주세요.") from None
    row = current(row.token, session)
    if row.status == "completed":
        raise HTTPException(409, "이미 완료된 연결입니다.")
    row.status = "connected"
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("/{token}")
def status(token: str, session: DB):
    row = current(token, session)
    return {**row.model_dump(), "space": get_space(row.space_id, session)}


@router.post("/{token}/connect")
def connect(token: str, session: DB):
    row = current(token, session)
    if row.status == "completed":
        raise HTTPException(409, "이미 완료된 연결입니다.")
    row.status = "connected"
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**row.model_dump(), "space": get_space(row.space_id, session)}


@router.post("/{token}/complete")
def complete(token: str, space: UISpace, session: DB):
    row = current(token, session)
    if row.status != "connected" or space.id != row.space_id:
        raise HTTPException(409, "연결된 공간과 촬영 상태를 확인해 주세요.")
    saved = replace_space(row.space_id, space, session)
    row.status = "completed"
    session.add(row)
    session.commit()
    return saved
