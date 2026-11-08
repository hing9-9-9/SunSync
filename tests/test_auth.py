"""Google login: sessions, expiry, protected routes and the checks on what comes back from Google."""

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from app import auth
from app.auth import COOKIE_NAME, LoginError, safe_next
from app.main import create_app
from app.models.auth import AuthSession, AuthUser, LoginAttempt
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine, select

PUBLIC_URL = "https://sunsync.example"
CLIENT_ID = "client-id.apps.googleusercontent.com"
START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def encode_token(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(auth, "utcnow", lambda: clock.now)
    return clock


@pytest.fixture
def login_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUNSYNC_AUTH", "google")
    monkeypatch.setenv("SUNSYNC_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SUNSYNC_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SUNSYNC_PUBLIC_URL", PUBLIC_URL)


@pytest.fixture
def database_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False}
    )
    yield engine
    engine.dispose()


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> dict:
    """What the fake Google returns for the code; tests change `claims`."""

    state: dict = {"claims": {}}
    monkeypatch.setattr(
        auth, "exchange_code", lambda settings, code, verifier: encode_token(state["claims"])
    )
    return state


@pytest.fixture
def client(login_on, database_engine, clock) -> Iterator[TestClient]:
    with TestClient(create_app(database_engine), base_url=PUBLIC_URL) as test_client:
        yield test_client


def good_claims(attempt_nonce: str, **changes: object) -> dict:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int((START + timedelta(hours=1)).timestamp()),
        "nonce": attempt_nonce,
        "email_verified": True,
        "sub": "google-user-1",
        "email": "reader@example.com",
        "name": "Reader",
    }
    return claims | changes


def attempt(database_engine: Engine) -> LoginAttempt:
    with Session(database_engine) as db:
        return db.exec(select(LoginAttempt)).one()


def start(client: TestClient, next_path: str = "/") -> str:
    """Press the Google button; return the state that Google sends back."""

    response = client.get(
        "/api/auth/google/login", params={"next": next_path}, follow_redirects=False
    )
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def log_in(client, database_engine, google, next_path="/", **changes):
    state = start(client, next_path)
    google["claims"] = good_claims(attempt(database_engine).nonce, **changes)
    return client.get(
        "/api/auth/google/callback",
        params={"code": "one-time", "state": state},
        follow_redirects=False,
    )


def test_login_is_off_by_default_and_nothing_is_checked(database_engine, monkeypatch):
    monkeypatch.delenv("SUNSYNC_AUTH", raising=False)
    with TestClient(create_app(database_engine)) as client:
        session = client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.json()["auth_enabled"] is False
        assert client.get("/api/ui/spaces").status_code == 200
        assert client.get("/api/auth/google/login").status_code == 404


def test_the_server_does_not_start_when_the_login_is_not_fully_set(monkeypatch, database_engine):
    monkeypatch.setenv("SUNSYNC_AUTH", "google")
    for name in ("SUNSYNC_GOOGLE_CLIENT_ID", "SUNSYNC_GOOGLE_CLIENT_SECRET", "SUNSYNC_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    with (
        pytest.raises(RuntimeError, match="SUNSYNC_GOOGLE_CLIENT_ID"),
        TestClient(create_app(database_engine)),
    ):
        pass


def test_routes_are_closed_without_a_login_but_health_and_login_are_open(client):
    response = client.get("/api/ui/spaces")
    assert response.status_code == 401
    assert response.json() == {"detail": "로그인이 필요합니다."}
    assert client.get("/api/auth/session").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_the_login_sends_the_user_to_google_with_the_safety_values(client):
    response = client.get("/api/auth/google/login", params={"next": "/x"}, follow_redirects=False)
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == auth.GOOGLE_AUTH_URL
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [f"{PUBLIC_URL}/api/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert all(query[key][0] for key in ("state", "nonce", "code_challenge"))


def test_a_login_gives_a_session_cookie_and_goes_back_to_the_page(client, database_engine, google):
    response = log_in(client, database_engine, google, next_path="/?mode=app#/capture")
    assert response.status_code == 302
    assert response.headers["location"] == "/?mode=app#/capture"
    cookie = response.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=" in cookie
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "auth_enabled": True,
        "user": {"name": "Reader", "email": "reader@example.com"},
    }
    assert client.get("/api/ui/spaces").status_code == 200


def test_only_the_hash_of_the_cookie_is_stored(client, database_engine, google):
    log_in(client, database_engine, google)
    token = client.cookies.get(COOKIE_NAME)
    with Session(database_engine) as db:
        rows = db.exec(select(AuthSession)).all()
    assert len(rows) == 1 and token not in rows[0].token_hash
    assert rows[0].token_hash == auth.hash_token(token)


def test_the_same_google_account_is_one_user(client, database_engine, google):
    log_in(client, database_engine, google)
    log_in(client, database_engine, google, name="New name")
    with Session(database_engine) as db:
        users = db.exec(select(AuthUser)).all()
    assert [(user.google_sub, user.name) for user in users] == [("google-user-1", "New name")]


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.example"},
        {"aud": "another-client"},
        {"exp": int((START - timedelta(minutes=1)).timestamp())},
        {"nonce": "another-nonce"},
        {"email_verified": False},
        {"sub": None},
        {"email": None},
    ],
    ids=["issuer", "audience", "expired", "nonce", "email_not_verified", "no_id", "no_email"],
)
def test_a_login_with_wrong_claims_is_refused(client, database_engine, google, changes):
    response = log_in(client, database_engine, google, **changes)
    assert response.headers["location"] == "/?login=failed"
    assert COOKIE_NAME not in response.headers.get("set-cookie", "")
    assert client.get("/api/auth/session").status_code == 401


def test_the_audience_may_be_a_list(client, database_engine, google):
    response = log_in(client, database_engine, google, aud=["other", CLIENT_ID])
    assert response.headers["location"] == "/"
    assert client.get("/api/auth/session").status_code == 200


def test_a_login_can_be_used_only_once(client, database_engine, google):
    state = start(client)
    google["claims"] = good_claims(attempt(database_engine).nonce)
    params = {"code": "one-time", "state": state}
    first = client.get("/api/auth/google/callback", params=params, follow_redirects=False)
    second = client.get("/api/auth/google/callback", params=params, follow_redirects=False)
    assert first.headers["location"] == "/"
    assert second.headers["location"] == "/?login=failed"


def test_an_unknown_state_or_a_denied_login_is_refused(client):
    for params in ({"code": "c", "state": "unknown"}, {"error": "access_denied", "state": "s"}, {}):
        response = client.get("/api/auth/google/callback", params=params, follow_redirects=False)
        assert response.headers["location"] == "/?login=failed"


def test_a_login_that_took_too_long_is_refused(client, database_engine, google, clock):
    state = start(client)
    google["claims"] = good_claims(attempt(database_engine).nonce)
    clock.advance(minutes=11)
    response = client.get(
        "/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert response.headers["location"] == "/?login=failed"


def test_a_failed_exchange_with_google_is_refused(client, database_engine, monkeypatch):
    def broken(settings, code, verifier):
        raise LoginError("down")

    monkeypatch.setattr(auth, "exchange_code", broken)
    state = start(client)
    response = client.get(
        "/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert response.headers["location"] == "/?login=failed"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/?mode=app#/capture", "/?mode=app#/capture"),
        ("/spaces", "/spaces"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("/a\r\nSet-Cookie: x=1", "/"),
        ("/" + "a" * 600, "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_the_page_after_the_login_must_be_inside_the_site(value, expected):
    assert safe_next(value) == expected


def test_the_session_ends_after_30_minutes_without_use(client, database_engine, google, clock):
    log_in(client, database_engine, google)
    clock.advance(minutes=29)
    assert client.get("/api/ui/spaces").status_code == 200  # this use starts 30 minutes again
    clock.advance(minutes=29)
    assert client.get("/api/ui/spaces").status_code == 200
    clock.advance(minutes=31)
    assert client.get("/api/ui/spaces").status_code == 401
    assert client.get("/api/auth/session").status_code == 401


def test_the_session_ends_after_12_hours_even_when_it_is_used(
    client, database_engine, google, clock
):
    log_in(client, database_engine, google)
    for _ in range(35):  # 35 x 20 minutes = 11 hours 40 minutes
        clock.advance(minutes=20)
        assert client.get("/api/ui/spaces").status_code == 200
    clock.advance(minutes=21)  # past 12 hours
    assert client.get("/api/ui/spaces").status_code == 401


def test_the_times_can_be_set(monkeypatch, client, database_engine, google, clock):
    monkeypatch.setenv("SUNSYNC_SESSION_IDLE_MINUTES", "5")
    log_in(client, database_engine, google)
    clock.advance(minutes=6)
    assert client.get("/api/ui/spaces").status_code == 401


def test_a_logout_ends_the_session(client, database_engine, google):
    log_in(client, database_engine, google)
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert client.get("/api/auth/session").status_code == 401
    with Session(database_engine) as db:
        assert db.exec(select(AuthSession)).all() == []


def test_a_cookie_that_was_logged_out_cannot_be_used_again(client, database_engine, google):
    log_in(client, database_engine, google)
    token = client.cookies.get(COOKIE_NAME)
    client.post("/api/auth/logout")
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/api/auth/session").status_code == 401


def test_a_change_from_another_site_is_refused(client, database_engine, google):
    log_in(client, database_engine, google)
    other = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert other.status_code == 403
    assert client.get("/api/auth/session").status_code == 200
    same = client.post("/api/auth/logout", headers={"Origin": PUBLIC_URL})
    assert same.status_code == 204


def test_a_change_from_another_site_is_refused_on_protected_routes(client, database_engine, google):
    log_in(client, database_engine, google)
    response = client.delete("/api/ui/spaces/none", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_a_callback_from_another_browser_is_refused(client, database_engine, google):
    state = start(client)
    google["claims"] = good_claims(attempt(database_engine).nonce)
    other_browser = TestClient(client.app, base_url=PUBLIC_URL)  # no login cookie
    response = other_browser.get(
        "/api/auth/google/callback",
        params={"code": "one-time", "state": state},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?login=failed"
    assert other_browser.get("/api/auth/session").status_code == 401
    with Session(database_engine) as db:
        assert db.exec(select(AuthSession)).all() == []


def test_a_callback_with_the_wrong_login_cookie_is_refused(client, database_engine, google):
    state = start(client)
    google["claims"] = good_claims(attempt(database_engine).nonce)
    client.cookies.set(auth.LOGIN_COOKIE, "another-value", path=auth.LOGIN_COOKIE_PATH)
    response = client.get(
        "/api/auth/google/callback",
        params={"code": "one-time", "state": state},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?login=failed"
    assert client.get("/api/auth/session").status_code == 401


def test_the_login_cookie_is_private_and_stored_only_as_a_hash(client, database_engine):
    response = client.get("/api/auth/google/login", follow_redirects=False)
    cookie = response.headers["set-cookie"]
    lowered = cookie.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered
    assert f"path={auth.LOGIN_COOKIE_PATH}" in lowered
    value = cookie.split(";")[0].split("=", 1)[1]
    assert attempt(database_engine).browser_hash == auth.hash_token(value)
    assert value not in attempt(database_engine).model_dump_json()


def test_the_login_cookie_is_removed_after_the_callback(client, database_engine, google):
    response = log_in(client, database_engine, google)
    removed = [
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(f"{auth.LOGIN_COOKIE}=")
    ]
    assert removed and "max-age=0" in removed[0].lower()


def test_a_route_added_later_is_closed_without_a_login(client):
    client.app.add_api_route("/api/added-later", lambda: {"ok": True})
    assert client.get("/api/added-later").status_code == 401
