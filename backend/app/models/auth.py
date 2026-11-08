"""Tables of the Google login: users, logins in progress and sessions."""

from sqlmodel import Field, SQLModel


class AuthUser(SQLModel, table=True):
    id: str = Field(primary_key=True)
    google_sub: str = Field(index=True, unique=True)
    email: str
    name: str
    created_at: str
    last_login_at: str


class AuthSession(SQLModel, table=True):
    """One logged-in browser. Only the hash of the cookie value is stored."""

    token_hash: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    created_at: str
    last_seen_at: str


class LoginAttempt(SQLModel, table=True):
    """A login that was sent to Google and has not come back yet; it is used once."""

    state_hash: str = Field(primary_key=True)
    nonce: str
    code_verifier: str
    next_path: str
    created_at: str
