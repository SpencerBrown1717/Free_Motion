"""Free Motion hardware abstraction (M2 / M5 transition).

Today: a `HardwareController` Protocol and a `MockHardwareController`
implementation. A `PiHardwareController` is on the roadmap.

Keep this package small. Devices that only manage peripherals (an LED,
a buzzer) don't need a controller — they live with the example.
Controllers exist so handlers for `arm`, `disarm`, `move`, and `stop`
can stay device-agnostic.
"""

from .interface import HardwareController
from .mock import MockHardwareController

__all__ = ["HardwareController", "MockHardwareController"]
