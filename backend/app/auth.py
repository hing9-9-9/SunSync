"""Google login: settings, sessions and the check that every protected request goes through.

The login is switched on with SUNSYNC_AUTH=google. Without it nothing is checked, so local
development and the existing tests behave as before.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request as HttpRequest
from urllib.request import urlopen

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models.auth import AuthSession, AuthUser, LoginAttempt

COOKIE_NAME = "sunsync_session"
LOGIN_REQUIRED = "로그인이 필요합니다."
LOCAL_USER = {"name": "개발용 사용자", "email": "dev@localhost"}

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

LOGIN_ATTEMPT_LIFETIME = timedelta(minutes=10)
LAST_SEEN_STEP = timedelta(minutes=1)  # the last-seen time is written at most once a minute
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class LoginError(Exception):
    """The login did not come back from Google in a way that can be trusted."""


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool
    client_id: str
    client_secret: str
    public_url: str
    idle: timedelta
    max_age: timedelta

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/api/auth/google/callback"

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    def check(self) -> None:
        """Stop the server at start-up when the login is on but not fully set."""

        missing = [
            name
            for name, value in (
                ("SUNSYNC_GOOGLE_CLIENT_ID", self.client_id),
                ("SUNSYNC_GOOGLE_CLIENT_SECRET", self.client_secret),
                ("SUNSYNC_PUBLIC_URL", self.public_url),
            )
            if self.enabled and not value
        ]
        if missing:
            raise RuntimeError(f"SUNSYNC_AUTH=google needs {', '.join(missing)}")


def auth_settings() -> AuthSettings:
    env = os.environ
    return AuthSettings(
        enabled=env.get("SUNSYNC_AUTH", "") == "google",
        client_id=env.get("SUNSYNC_GOOGLE_CLIENT_ID", ""),
        client_secret=env.get("SUNSYNC_GOOGLE_CLIENT_SECRET", ""),
        public_url=env.get("SUNSYNC_PUBLIC_URL", ""),
        idle=timedelta(minutes=int(env.get("SUNSYNC_SESSION_IDLE_MINUTES") or 30)),
        max_age=timedelta(hours=int(env.get("SUNSYNC_SESSION_MAX_HOURS") or 12)),
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def safe_next(value: str | None) -> str:
    """Only an address inside this site is allowed as the place to go after the login."""

    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and len(value) <= 500
        and not any(char in value for char in "\\\r\n")
    ):
        return value
    return "/"


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(settings: AuthSettings, state: str, nonce: str, verifier: str) -> str:
    query = urlencode(
        {
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}"


def exchange_code(settings: AuthSettings, code: str, verifier: str) -> str:
    """Give the one-time code to Google and get the ID token back."""

    body = urlencode(
        {
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode()
    request = HttpRequest(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310
            payload = json.load(response)
    except (URLError, OSError, ValueError) as error:
        raise LoginError("the code could not be exchanged") from error
    token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(token, str):
        raise LoginError("Google sent no ID token")
    return token


def id_token_claims(id_token: str) -> dict:
    """Read the claims of an ID token that came straight from Google over TLS.

    OpenID Connect allows the signature check to be skipped for a token received directly from
    the token endpoint; the claims are still checked in `check_claims`.
    """

    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as error:
        raise LoginError("the ID token cannot be read") from error
    if not isinstance(claims, dict):
        raise LoginError("the ID token cannot be read")
    return claims


def check_claims(claims: dict, settings: AuthSettings, nonce: str, now: datetime) -> None:
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    expires = claims.get("exp")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise LoginError("wrong issuer")
    if settings.client_id not in audiences:
        raise LoginError("wrong audience")
    if not isinstance(expires, int | float) or expires <= now.timestamp():
        raise LoginError("the ID token has expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise LoginError("wrong nonce")
    if claims.get("email_verified") not in (True, "true"):
        raise LoginError("the e-mail address is not verified")
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("email"), str):
        raise LoginError("the account has no id or e-mail")


def start_login(db: Session, settings: AuthSettings, next_path: str | None) -> str:
    """Remember the login that is sent to Google and return the address to send the user to."""

    now = utcnow()
    for old in db.exec(select(LoginAttempt)).all():
        if now - datetime.fromisoformat(old.created_at) > LOGIN_ATTEMPT_LIFETIME:
            db.delete(old)
    state, nonce, verifier = new_token(), new_token(), secrets.token_urlsafe(48)
    db.add(
        LoginAttempt(
            state_hash=hash_token(state),
            nonce=nonce,
            code_verifier=verifier,
            next_path=safe_next(next_path),
            created_at=now.isoformat(),
        )
    )
    db.commit()
    return authorization_url(settings, state, nonce, verifier)


def finish_login(db: Session, settings: AuthSettings, code: str, state: str) -> tuple[str, str]:
    """Check what came back from Google. Return the new session token and where to go next."""

    attempt = db.get(LoginAttempt, hash_token(state))
    if attempt is None:
        raise LoginError("unknown login")
    db.delete(attempt)  # used once, even when the rest fails
    db.commit()
    now = utcnow()
    if now - datetime.fromisoformat(attempt.created_at) > LOGIN_ATTEMPT_LIFETIME:
        raise LoginError("the login took too long")
    claims = id_token_claims(exchange_code(settings, code, attempt.code_verifier))
    check_claims(claims, settings, attempt.nonce, now)
    user = save_user(db, claims, now)
    return create_session(db, settings, user, now), attempt.next_path


def save_user(db: Session, claims: dict, now: datetime) -> AuthUser:
    user = db.exec(select(AuthUser).where(AuthUser.google_sub == claims["sub"])).first()
    name = claims.get("name") or claims["email"].split("@")[0]
    if user is None:
        user = AuthUser(
            id=uuid.uuid4().hex,
            google_sub=claims["sub"],
            email=claims["email"],
            name=name,
            created_at=now.isoformat(),
            last_login_at=now.isoformat(),
        )
    else:
        user.email, user.name, user.last_login_at = claims["email"], name, now.isoformat()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_session(db: Session, settings: AuthSettings, user: AuthUser, now: datetime) -> str:
    for old in db.exec(select(AuthSession)).all():
        if is_expired(settings, old, now):
            db.delete(old)
    token = new_token()
    db.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user.id,
            created_at=now.isoformat(),
            last_seen_at=now.isoformat(),
        )
    )
    db.commit()
    return token


def is_expired(settings: AuthSettings, row: AuthSession, now: datetime) -> bool:
    """A session ends after the idle time, and in any case after the maximum time."""

    idle = now - datetime.fromisoformat(row.last_seen_at) > settings.idle
    too_old = now - datetime.fromisoformat(row.created_at) > settings.max_age
    return idle or too_old


def user_for_token(db: Session, settings: AuthSettings, token: str | None) -> AuthUser | None:
    """Return the logged-in user of a cookie value and count the request as activity."""

    if not token:
        return None
    row = db.get(AuthSession, hash_token(token))
    if row is None:
        return None
    now = utcnow()
    if is_expired(settings, row, now):
        db.delete(row)
        db.commit()
        return None
    if now - datetime.fromisoformat(row.last_seen_at) >= LAST_SEEN_STEP:
        row.last_seen_at = now.isoformat()
        db.add(row)
        db.commit()
    return db.get(AuthUser, row.user_id)


def end_session(db: Session, token: str | None) -> None:
    row = db.get(AuthSession, hash_token(token)) if token else None
    if row is not None:
        db.delete(row)
        db.commit()


def check_origin(request: Request, settings: AuthSettings) -> None:
    """A browser request that changes data must come from this site."""

    if request.method in SAFE_METHODS or not settings.public_host:
        return
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != settings.public_host:
        raise HTTPException(403, "허용되지 않은 요청입니다.")


def current_user(request: Request, db: Annotated[Session, Depends(get_session)]) -> AuthUser | None:
    """Dependency of every protected route: the logged-in user, or None when login is off."""

    settings = auth_settings()
    if not settings.enabled:
        return None
    user = user_for_token(db, settings, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(401, LOGIN_REQUIRED)
    check_origin(request, settings)
    return user
