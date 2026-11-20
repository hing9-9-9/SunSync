"""Google login routes: who is logged in, the way to Google and back, and the logout."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.auth import (
    LOCAL_USER,
    LOGIN_ATTEMPT_LIFETIME,
    LOGIN_COOKIE,
    LOGIN_COOKIE_PATH,
    LOGIN_REQUIRED,
    NO_STORE,
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
LOGIN_OFF = "구글 로그인이 켜져 있지 않습니다."


def failed_login() -> RedirectResponse:
    response = RedirectResponse(LOGIN_FAILED, status_code=302, headers=NO_STORE)
    response.delete_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
    return response


@router.get("/session")
def read_session(request: Request, db: DB, response: Response) -> dict:
    """Who is logged in. The web app asks this when it starts."""

    response.headers.update(NO_STORE)
    settings = auth_settings()
    if not settings.enabled:
        return {"auth_enabled": False, "user": LOCAL_USER}
    user = user_for_token(db, settings, request.cookies.get(settings.cookie_name))
    if user is None:
        raise HTTPException(401, LOGIN_REQUIRED, NO_STORE)
    return {"auth_enabled": True, "user": {"name": user.name, "email": user.email}}


@router.get("/check", status_code=204)
def check_session(request: Request, db: DB) -> Response:
    """For Caddy's forward_auth: 204 only with a valid session, 401 in every other case.

    Unlike /session this refuses also when the login is off, so a switch of the Caddyfile
    before the login is on closes /measure instead of opening it.
    """

    settings = auth_settings()
    user = (
        user_for_token(db, settings, request.cookies.get(settings.cookie_name))
        if settings.enabled
        else None
    )
    if user is None:
        raise HTTPException(401, LOGIN_REQUIRED, NO_STORE)
    return Response(status_code=204, headers=NO_STORE)


@router.get("/google/login")
def google_login(db: DB, next_path: Annotated[str, Query(alias="next")] = "/") -> RedirectResponse:
    settings = auth_settings()
    if not settings.enabled:
        raise HTTPException(404, LOGIN_OFF)
    address, browser = start_login(db, settings, next_path)
    response = RedirectResponse(address, status_code=302, headers=NO_STORE)
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
        raise HTTPException(404, LOGIN_OFF)
    if error or not code or not state:
        return failed_login()
    try:
        token, next_path = finish_login(
            db, settings, code, state, request.cookies.get(LOGIN_COOKIE)
        )
    except LoginError:
        return failed_login()
    response = RedirectResponse(next_path, status_code=302, headers=NO_STORE)
    response.delete_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
    response.set_cookie(
        settings.cookie_name,
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
        end_session(db, request.cookies.get(settings.cookie_name))
    response = Response(status_code=204, headers=NO_STORE)
    response.delete_cookie(
        settings.cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
