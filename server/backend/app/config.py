"""Local-development environment loading without exposing secret values."""

import os
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv


def load_local_environment() -> None:
    """Load the repository `.env` when present; process environment wins."""

    repository_root = Path(__file__).resolve().parents[3]
    load_dotenv(repository_root / ".env", override=False)


VISION_AI_MODES = ("auto", "on", "off")


def vision_ai_mode() -> Literal["auto", "on", "off"]:
    """`SUNSYNC_VISION_AI`: `auto` uses the real models when they are installed and falls back to
    the contour heuristics quietly (default), `on` requires them and reports a failure, `off`
    never uses them."""

    value = os.environ.get("SUNSYNC_VISION_AI", "").strip().lower() or "auto"
    if value not in VISION_AI_MODES:
        raise ValueError("SUNSYNC_VISION_AI must be auto, on or off")
    return cast(Literal["auto", "on", "off"], value)


def hub_base_url() -> str:
    """Return the configured Film & ThinQ Hub base URL, or empty string for local virtual mode."""

    return os.environ.get("SUNSYNC_HUB_BASE_URL", "").strip().rstrip("/")


def hub_shared_secret() -> str:
    """Return the shared HMAC-SHA256 secret used to sign backend-to-hub requests."""

    return os.environ.get("SUNSYNC_HUB_SHARED_SECRET", "").strip()


def hub_key_id() -> str:
    """Return the active HMAC signing key identifier (`SUNSYNC_HUB_KEY_ID`, default `v1`)."""

    return os.environ.get("SUNSYNC_HUB_KEY_ID", "").strip() or "v1"


def hub_timeout_seconds() -> float:
    """Return the HTTP timeout in seconds for backend-to-hub calls (default 2.5s)."""

    raw = os.environ.get("SUNSYNC_HUB_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return 2.5
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError("SUNSYNC_HUB_TIMEOUT_SECONDS must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("SUNSYNC_HUB_TIMEOUT_SECONDS must be a positive number")
    return timeout
