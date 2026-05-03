"""Mock hardware controller.

A deterministic in-memory `HardwareController` for tests, demos, and
contributors with no real hardware. State changes only in response to
commands — there is no time-based simulation, no physics, no noise.
That's deliberate: the mock is for proving the runtime, not for
training autopilots.
"""

from __future__ import annotations

from typing import Dict


class MockHardwareController:
    """In-memory mock controller."""

    name = "mock"

    def __init__(
        self,
        *,
        battery_start: float = 100.0,
        battery_arm_cost: float = 1.0,
        battery_move_cost_per_unit: float = 0.1,
        min_battery_to_arm: float = 10.0,
    ) -> None:
        self._armed = False
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._battery = battery_start
        self._connected = True
        self._battery_arm_cost = battery_arm_cost
        self._battery_move_cost_per_unit = battery_move_cost_per_unit
        self._min_battery_to_arm = min_battery_to_arm

    @property
    def available(self) -> bool:
        return True

    def arm(self) -> bool:
        if self._battery < self._min_battery_to_arm:
            return False
        if self._battery < self._battery_arm_cost:
            return False
        self._battery = max(0.0, self._battery - self._battery_arm_cost)
        self._armed = True
        return True

    def disarm(self) -> None:
        self._armed = False

    def stop(self) -> None:
        self._armed = False

    def move(self, dx: float, dy: float, dz: float) -> bool:
        if not self._armed:
            return False
        magnitude = (dx * dx + dy * dy + dz * dz) ** 0.5
        cost = magnitude * self._battery_move_cost_per_unit
        if self._battery < cost:
            return False
        self._x += dx
        self._y += dy
        self._z += dz
        self._battery = max(0.0, self._battery - cost)
        return True

    def state(self) -> Dict[str, object]:
        return {
            "armed": self._armed,
            "position": [self._x, self._y, self._z],
            "altitude": self._z,
            "battery": round(self._battery, 2),
            "connected": self._connected,
        }
