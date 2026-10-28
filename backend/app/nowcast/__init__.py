"""Persistence primitives shared by FR-01.4 future-DNI source adapters and workers."""

from app.nowcast.adapters import (
    Gk2aDsrAdapter,
    MockSnapshotAdapter,
    OpenMeteoDniAdapter,
    RawSnapshot,
)
from app.nowcast.repository import SnapshotRepository

__all__ = ["Gk2aDsrAdapter", "MockSnapshotAdapter", "OpenMeteoDniAdapter", "RawSnapshot", "SnapshotRepository"]
