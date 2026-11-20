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
