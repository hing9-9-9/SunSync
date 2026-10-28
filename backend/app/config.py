"""Local-development environment loading without exposing secret values."""

from pathlib import Path

from dotenv import load_dotenv


def load_local_environment() -> None:
    """Load the repository `.env` when present; process environment wins."""

    repository_root = Path(__file__).resolve().parents[2]
    load_dotenv(repository_root / ".env", override=False)
