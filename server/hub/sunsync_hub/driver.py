"""Pluggable hardware actuator driver protocol and deterministic simulator driver."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

HardwareMode = Literal["PDLC", "LG_SGF", "SPD"]
FaultMode = Literal["none", "delay", "timeout", "reject", "offline", "drift"]

VALID_HARDWARE_MODES: frozenset[str] = frozenset({"PDLC", "LG_SGF", "SPD"})
VALID_FAULT_MODES: frozenset[str] = frozenset(
    {"none", "delay", "timeout", "reject", "offline", "drift"}
)


def compute_matter_level_pct_x100(effective_cells: list[list[float]]) -> int:
    """Compute CSA Matter Window Covering Cluster (0x0102) level in 0..10000."""
    flat = [float(v) for row in effective_cells for v in row]
    if not flat:
        return 0
    mean_val = sum(flat) / len(flat)
    return max(0, min(10000, round(mean_val * 10000)))


@dataclass(frozen=True)
class FaultConfig:
    """Active fault injection configuration."""

    mode: FaultMode = "none"
    delay_ms: int = 0
    scene_id: str | None = None
    window_id: str | None = None

    def matches(self, scene_id: str | None, window_id: str | None) -> bool:
        """Return True if this fault configuration applies to the target window."""
        if self.mode == "none":
            return False
        if self.scene_id is not None and scene_id != self.scene_id:
            return False
        return not (self.window_id is not None and window_id != self.window_id)


@dataclass(frozen=True)
class ActuationResult:
    """Result returned by a hardware driver after validating and applying cells."""

    accepted: bool
    matter_level_pct_x100: int
    latency_ms: int
    rejection_reason: str | None = None


@runtime_checkable
class HardwareDriverProtocol(Protocol):
    """Pluggable boundary for physical ThinQ ON / Matter / ESP32 or simulated drivers."""

    def get_fault_config(self) -> FaultConfig: ...

    def configure_fault(
        self,
        mode: FaultMode,
        *,
        delay_ms: int = 0,
        scene_id: str | None = None,
        window_id: str | None = None,
    ) -> FaultConfig: ...

    def validate_cells(
        self,
        hardware_mode: str,
        effective_cells: list[list[float]],
    ) -> str | None: ...

    def actuate(
        self,
        scene_id: str,
        window_id: str,
        hardware_mode: str,
        effective_cells: list[list[float]],
    ) -> ActuationResult: ...

    def perturb_for_drift(
        self,
        hardware_mode: str,
        reported_base_cells: list[list[float]],
        reported_marker: dict[str, Any],
    ) -> tuple[list[list[float]], dict[str, Any]]: ...


class SimulatedActuatorDriver:
    """Deterministic simulated PDLC / SPD / LG_SGF hardware actuator driver."""

    def __init__(self) -> None:
        self._fault = FaultConfig()

    def get_fault_config(self) -> FaultConfig:
        return self._fault

    def configure_fault(
        self,
        mode: FaultMode,
        *,
        delay_ms: int = 0,
        scene_id: str | None = None,
        window_id: str | None = None,
    ) -> FaultConfig:
        if mode not in VALID_FAULT_MODES:
            raise ValueError(f"Unsupported fault mode: {mode!r}")
        clamped_delay = max(0, min(30_000, int(delay_ms)))
        self._fault = FaultConfig(
            mode=mode,
            delay_ms=clamped_delay,
            scene_id=scene_id,
            window_id=window_id,
        )
        return self._fault

    def validate_cells(
        self,
        hardware_mode: str,
        effective_cells: list[list[float]],
    ) -> str | None:
        """Validate cell opacity values against hardware_mode constraints."""
        if hardware_mode not in VALID_HARDWARE_MODES:
            return f"Unsupported hardware_mode '{hardware_mode}'"

        for r, row in enumerate(effective_cells):
            for c, val in enumerate(row):
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    return f"Invalid non-numeric opacity {val!r} at ({r}, {c})"
                fv = float(val)
                if math.isnan(fv) or math.isinf(fv) or not (0.0 <= fv <= 1.0):
                    return f"Opacity {fv} at ({r}, {c}) out of range [0.0, 1.0]"
                if hardware_mode == "PDLC" and abs(fv - 0.0) > 1e-6 and abs(fv - 1.0) > 1e-6:
                    return (
                        f"PDLC hardware mode only supports binary opacity values "
                        f"(0.0 or 1.0), got {fv} at ({r}, {c})"
                    )
        return None

    def actuate(
        self,
        scene_id: str,
        window_id: str,
        hardware_mode: str,
        effective_cells: list[list[float]],
    ) -> ActuationResult:
        """Validate and simulate physical actuation latency, faults, and Matter telemetry."""
        start = time.perf_counter()
        matter_level = compute_matter_level_pct_x100(effective_cells)

        if self._fault.matches(scene_id, window_id):
            if self._fault.mode == "delay" and self._fault.delay_ms > 0:
                time.sleep(self._fault.delay_ms / 1000.0)
            elif self._fault.mode == "reject":
                elapsed_ms = max(1, round((time.perf_counter() - start) * 1000))
                return ActuationResult(
                    accepted=False,
                    matter_level_pct_x100=matter_level,
                    latency_ms=elapsed_ms,
                    rejection_reason="Simulated actuator rejection (fault_mode=reject)",
                )

        validation_error = self.validate_cells(hardware_mode, effective_cells)
        elapsed_ms = max(1, round((time.perf_counter() - start) * 1000))
        if validation_error is not None:
            return ActuationResult(
                accepted=False,
                matter_level_pct_x100=matter_level,
                latency_ms=elapsed_ms,
                rejection_reason=validation_error,
            )

        return ActuationResult(
            accepted=True,
            matter_level_pct_x100=matter_level,
            latency_ms=elapsed_ms,
            rejection_reason=None,
        )

    def perturb_for_drift(
        self,
        hardware_mode: str,
        reported_base_cells: list[list[float]],
        reported_marker: dict[str, Any],
    ) -> tuple[list[list[float]], dict[str, Any]]:
        """Deterministically perturb reported cells to simulate hardware drift."""
        perturbed_base = [row[:] for row in reported_base_cells]
        perturbed_marker = dict(reported_marker)
        rows = len(perturbed_base)
        cols = len(perturbed_base[0]) if rows > 0 else 0
        if rows == 0 or cols == 0:
            return perturbed_base, perturbed_marker

        marker_active = bool(perturbed_marker.get("enabled"))
        m_row = perturbed_marker.get("start_row")
        m_col = perturbed_marker.get("start_col")

        target_cell: tuple[int, int] | None = None
        for r in range(rows):
            for c in range(cols):
                in_marker = (
                    marker_active
                    and m_row is not None
                    and m_col is not None
                    and m_row <= r < m_row + 6
                    and m_col <= c < m_col + 6
                )
                if not in_marker:
                    target_cell = (r, c)
                    break
            if target_cell is not None:
                break

        if target_cell is None:
            # Entire grid is covered by the 6x6 marker; disable reported marker to force drift
            perturbed_marker = {
                "enabled": False,
                "code_id": None,
                "start_row": None,
                "start_col": None,
            }
            target_cell = (0, 0)

        tr, tc = target_cell
        old_val = float(perturbed_base[tr][tc])
        if hardware_mode == "PDLC":
            new_val = 0.0 if old_val >= 0.5 else 1.0
        else:
            new_val = round(old_val + 0.25, 3) if old_val <= 0.5 else round(old_val - 0.25, 3)
        perturbed_base[tr][tc] = new_val
        return perturbed_base, perturbed_marker
