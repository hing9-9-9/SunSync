"""Google login routes: who is logged in, the way to Google and back, and the logout."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.auth import (
    COOKIE_NAME,
    LOCAL_USER,
    LOGIN_ATTEMPT_LIFETIME,
    LOGIN_COOKIE,
    LOGIN_COOKIE_PATH,
    LOGIN_REQUIRED,
    LoginError,
    auth_settings,
    check_origin,
    end_session,
    finish_login,
    start_login,
    user_for_token,
)
from app.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])
DB = Annotated[Session, Depends(get_session)]

LOGIN_FAILED = "/?login=failed"


def failed_login() -> RedirectResponse:
    response = RedirectResponse(LOGIN_FAILED, status_code=302)
    response.delete_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
    return response


@router.get("/session")
def read_session(request: Request, db: DB) -> dict:
    """Who is logged in. The web app asks this when it starts, and Caddy asks it for /measure."""

    settings = auth_settings()
    if not settings.enabled:
        return {"auth_enabled": False, "user": LOCAL_USER}
    user = user_for_token(db, settings, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(401, LOGIN_REQUIRED)
    return {"auth_enabled": True, "user": {"name": user.name, "email": user.email}}


@router.get("/google/login")
def google_login(db: DB, next_path: Annotated[str, Query(alias="next")] = "/") -> RedirectResponse:
    settings = auth_settings()
    if not settings.enabled:
        raise HTTPException(404, "구글 로그인이 켜져 있지 않습니다.")
    address, browser = start_login(db, settings, next_path)
    response = RedirectResponse(address, status_code=302)
    response.set_cookie(
        LOGIN_COOKIE,
        browser,
        max_age=int(LOGIN_ATTEMPT_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",  # still sent when Google sends the browser back to the callback
        path=LOGIN_COOKIE_PATH,
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request, db: DB, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    settings = auth_settings()
    if not settings.enabled:
        raise HTTPException(404, "구글 로그인이 켜져 있지 않습니다.")
    if error or not code or not state:
        return failed_login()
    try:
        token, next_path = finish_login(
            db, settings, code, state, request.cookies.get(LOGIN_COOKIE)
        )
    except LoginError:
        return failed_login()
    response = RedirectResponse(next_path, status_code=302)
    response.delete_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(settings.max_age.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", status_code=204)
def logout(request: Request, db: DB) -> Response:
    settings = auth_settings()
    if settings.enabled:
        check_origin(request, settings)
        end_session(db, request.cookies.get(COOKIE_NAME))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
