"""Free Motion hardware abstraction.

Today: a `HardwareController` Protocol, a `MockHardwareController` for
tests/demos, and a `PiHardwareController` (M4) for real Raspberry Pi
bench rigs.

Keep this package small. Devices that only manage peripherals (an LED,
a buzzer) don't need a controller — they live with the example.
Controllers exist so handlers for `arm`, `disarm`, `move`, and `stop`
can stay device-agnostic.

`make_controller_from_config` is the runtime factory: given a `Config`,
it returns the controller matching `config.hardware_profile`. The Pi
controller is imported lazily so this package stays importable on
non-Pi hosts (CI, dev laptops) regardless of `RPi.GPIO` availability.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .interface import HardwareController
from .mock import MockHardwareController
from .safety import SafetyGate

if TYPE_CHECKING:  # pragma: no cover
    from freemotion.config import Config

LOG = logging.getLogger("freemotion.hardware")

__all__ = [
    "HardwareController",
    "MockHardwareController",
    "SafetyGate",
    "make_controller_from_config",
]


def make_controller_from_config(config: "Config") -> HardwareController:
    """Pick a `HardwareController` for `config.hardware_profile`.

    - ``"pi"``: lazy-imports `PiHardwareController`. Pin numbers come
      from `config.pi_armed_pin` / `config.pi_moving_pin` if set,
      otherwise the controller's defaults.
    - everything else (``"mock"``, ``"host"``, unset, unknown): returns
      `MockHardwareController()`. Unknown profiles log a warning so the
      misconfiguration is visible without crashing the runtime.
    """
    profile = (config.hardware_profile or "").strip().lower()
    if profile == "pi":
        from .pi import PiHardwareController

        return PiHardwareController(
            armed_pin=config.pi_armed_pin,
            moving_pin=config.pi_moving_pin,
        )
    if profile not in {"", "host", "mock"}:
        LOG.warning(
            "unknown FREEMOTION_HARDWARE=%r; falling back to MockHardwareController",
            profile,
        )
    return MockHardwareController()
