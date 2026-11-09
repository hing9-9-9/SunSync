"""Who owns a space: one check for every route that reaches a stored space.

A space is a stored Scene (`scenes`) plus its UI copy (`ui_spaces`). The Scene carries the owner.
When the login is off (local development) there is no user and nothing is filtered. When it is
on, a space is visible only to its owner; a space without an owner (created before accounts
existed) is visible to nobody. Another user's space answers 404, the same as a missing one, so
its existence is not revealed.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.auth import current_user
from app.db import get_session
from app.models.auth import AuthUser
from app.models.scene import SceneRecord
from app.ui_scene import UISpaceRecord, canonical_id

CurrentUser = Annotated[AuthUser | None, Depends(current_user)]
NOT_FOUND = "공간을 찾을 수 없습니다."


def owner_of(user: AuthUser | None) -> str | None:
    return user.id if user is not None else None


def may_access(user: AuthUser | None, owner_id: str | None) -> bool:
    return user is None or owner_id == user.id


def scene_owner(session: Session, scene_id: str) -> tuple[bool, str | None]:
    """Return (scene exists, its owner)."""
    record = session.get(SceneRecord, scene_id)
    return (record is not None, record.owner_id if record is not None else None)


def _space_scene_id(session: Session, space_id: str) -> str:
    record = session.get(UISpaceRecord, space_id)
    return record.scene_id if record is not None else canonical_id("scene", space_id)


def require_scene(session: Session, scene_id: str, user: AuthUser | None) -> None:
    """404 when the scene exists but belongs to someone else. A missing scene is left to the route."""
    exists, owner = scene_owner(session, scene_id)
    if exists and not may_access(user, owner):
        raise HTTPException(404, NOT_FOUND)


def require_space(session: Session, space_id: str, user: AuthUser | None) -> None:
    require_scene(session, _space_scene_id(session, space_id), user)


def _token_space_id(session: Session, kind: str, token: str) -> str | None:
    # Imported here: these modules import ui_spaces, which imports this one.
    from app.api.handoffs import Handoff
    from app.api.measurements import MeasurementJob

    row = session.get(Handoff, token) if kind == "handoffs" else session.get(MeasurementJob, token)
    return row.space_id if row is not None else None


def check_ownership(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    user: CurrentUser,
) -> None:
    """App-wide dependency: look at the ids in the path and refuse another user's space.

    Routes that take ids from the body (creating a hand-off or a measurement, claiming a code)
    and list routes call the helpers above themselves.
    """
    if user is None:
        return
    params = request.path_params
    if "scene_id" in params:
        require_scene(session, params["scene_id"], user)
    if "space_id" in params:
        require_space(session, params["space_id"], user)
    if "token" in params:
        parts = request.url.path.split("/")
        kind = next((p for p in parts if p in ("handoffs", "measurements")), None)
        if kind is not None:
            space_id = _token_space_id(session, kind, params["token"])
            if space_id is not None:
                require_space(session, space_id, user)
