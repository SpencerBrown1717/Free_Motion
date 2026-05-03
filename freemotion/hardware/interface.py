"""HardwareController contract.

A minimal Protocol that lets command handlers operate on a device
without caring whether it's mock, Pi, Jetson, etc. Intentionally small;
only methods every plausible controller can implement live here.
"""

from __future__ import annotations

from typing import Dict, Protocol, runtime_checkable


@runtime_checkable
class HardwareController(Protocol):
    """Protocol for anything that can be armed and moved.

    Implementations:

    - MUST be safe to call `stop()` at any time, in any state.
    - SHOULD return `False` from `arm()` / `move()` when refusing
      (low battery, missing config, watchdog tripped) rather than raising.
    - SHOULD make `state()` cheap (no I/O round-trips).
    """

    @property
    def name(self) -> str:
        """Short identifier, e.g. `"mock"`, `"pi"`."""

    @property
    def available(self) -> bool:
        """Whether the controller is ready to accept commands."""

    def arm(self) -> bool:
        """Transition to armed. Returns False if refused."""

    def disarm(self) -> None:
        """Transition to idle. Always succeeds."""

    def stop(self) -> None:
        """Hard stop. Idempotent. Always succeeds."""

    def move(self, dx: float, dy: float, dz: float) -> bool:
        """Apply a relative offset. Returns False if refused (e.g. not armed)."""

    def state(self) -> Dict[str, object]:
        """Snapshot of telemetry: armed, position, altitude, battery, connected."""
