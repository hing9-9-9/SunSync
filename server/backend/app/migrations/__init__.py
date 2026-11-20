"""Small, dependency-free SQLite migrations for application-owned tables."""

from app.migrations.runner import upgrade_database

__all__ = ["upgrade_database"]
