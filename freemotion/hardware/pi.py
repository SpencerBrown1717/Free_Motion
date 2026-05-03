"""Raspberry Pi hardware controller (M4 Phase 1).

Bench-safe GPIO-backed implementation of the `HardwareController`
Protocol. Each state transition flips a real output pin so `state()`
reflects hardware-backed state, not just an in-memory counter:

- ``armed_pin``: HIGH while armed, LOW otherwise.
- ``moving_pin``: pulsed HIGH for ``move_pulse_s`` seconds on each
  successful ``move()``, then back to LOW.

This is the bench rig contract per the M4 plan: a real, observable
hardware-state change driven by the runtime, with no motor drivers and
no propellers. The "motion primitive" is intentionally bench-safe;
real actuation lands in M5+ behind explicit safety modes.

The ``RPi.GPIO`` module is imported lazily inside ``__init__`` so this
file imports cleanly on non-Pi hosts (CI, dev laptops). Tests inject a
``FakeGPIO`` adapter via the ``gpio`` arg; the contract is anything
exposing ``setmode``, ``setup``, ``output``, ``cleanup`` and the
``BCM`` / ``OUT`` / ``HIGH`` / ``LOW`` constants.

Hardware exceptions are caught and logged: ``arm()`` and ``move()``
return ``False`` on failure, ``stop()`` always swallows. The agent
loop never sees a hardware-induced crash from this controller — that's
ADR-0004 territory ("stop must always work").
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

LOG = logging.getLogger("freemotion.hardware.pi")


class PiHardwareController:
    """Bench-safe Pi GPIO controller."""

    name = "pi"

    DEFAULT_ARMED_PIN = 27
    DEFAULT_MOVING_PIN = 22
    DEFAULT_MOVE_PULSE_S = 0.1

    def __init__(
        self,
        *,
        armed_pin: Optional[int] = None,
        moving_pin: Optional[int] = None,
        move_pulse_s: float = DEFAULT_MOVE_PULSE_S,
        gpio: Optional[Any] = None,
    ) -> None:
        self._armed_pin: int = (
            armed_pin if armed_pin is not None else self.DEFAULT_ARMED_PIN
        )
        self._moving_pin: int = (
            moving_pin if moving_pin is not None else self.DEFAULT_MOVING_PIN
        )
        self._move_pulse_s: float = max(0.0, float(move_pulse_s))

        self._armed = False
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._last_move_ts: Optional[float] = None
        self._lock = threading.Lock()

        self._gpio: Optional[Any] = gpio
        self._ready = False

        if self._gpio is None:
            try:
                import RPi.GPIO as RealGPIO  # type: ignore[import-not-found]

                self._gpio = RealGPIO
            except Exception as exc:  # pragma: no cover - non-Pi path
                LOG.warning(
                    "RPi.GPIO unavailable (%s); PiHardwareController is offline",
                    exc,
                )
                self._gpio = None

        if self._gpio is not None:
            try:
                self._gpio.setmode(self._gpio.BCM)
                self._gpio.setup(
                    self._armed_pin,
                    self._gpio.OUT,
                    initial=self._gpio.LOW,
                )
                self._gpio.setup(
                    self._moving_pin,
                    self._gpio.OUT,
                    initial=self._gpio.LOW,
                )
                self._ready = True
            except Exception as exc:
                LOG.warning(
                    "GPIO setup failed: %s; PiHardwareController is offline", exc
                )
                self._ready = False

    @property
    def available(self) -> bool:
        return self._ready

    def _safe_output(self, pin: int, level: Any) -> bool:
        """Best-effort GPIO write. Returns True on success."""
        if not self._ready or self._gpio is None:
            return False
        try:
            self._gpio.output(pin, level)
            return True
        except Exception as exc:
            LOG.warning("GPIO output failed on pin %s: %s", pin, exc)
            return False

    def arm(self) -> bool:
        if not self._ready or self._gpio is None:
            return False
        with self._lock:
            if not self._safe_output(self._armed_pin, self._gpio.HIGH):
                return False
            self._armed = True
            return True

    def disarm(self) -> None:
        with self._lock:
            self._armed = False
            if self._ready and self._gpio is not None:
                self._safe_output(self._armed_pin, self._gpio.LOW)

    def stop(self) -> None:
        """Hard stop: drop both outputs, mark idle. Never raises.

        Deliberately does not acquire ``_lock`` — stop must succeed even
        if a ``move()`` is in flight. Setting ``_armed = False`` is
        atomic for a Python bool, and the GPIO writes are idempotent
        (re-asserting LOW is harmless).
        """
        self._armed = False
        if self._ready and self._gpio is not None:
            try:
                self._gpio.output(self._armed_pin, self._gpio.LOW)
            except Exception as exc:
                LOG.warning("stop: armed_pin LOW failed: %s", exc)
            try:
                self._gpio.output(self._moving_pin, self._gpio.LOW)
            except Exception as exc:
                LOG.warning("stop: moving_pin LOW failed: %s", exc)

    def move(self, dx: float, dy: float, dz: float) -> bool:
        if not self._ready or self._gpio is None:
            return False
        try:
            fdx, fdy, fdz = float(dx), float(dy), float(dz)
        except (TypeError, ValueError):
            return False
        with self._lock:
            if not self._armed:
                return False
            if not self._safe_output(self._moving_pin, self._gpio.HIGH):
                return False
            try:
                if self._move_pulse_s > 0:
                    time.sleep(self._move_pulse_s)
            finally:
                self._safe_output(self._moving_pin, self._gpio.LOW)
            self._x += fdx
            self._y += fdy
            self._z += fdz
            self._last_move_ts = time.time()
            return True

    def state(self) -> Dict[str, Any]:
        return {
            "armed": self._armed,
            "position": [self._x, self._y, self._z],
            "altitude": self._z,
            "connected": self._ready,
            "pins": {
                "armed": self._armed_pin,
                "moving": self._moving_pin,
            },
            "last_move_ts": self._last_move_ts,
        }

    def cleanup(self) -> None:
        """Release GPIO resources. Safe to call multiple times.

        Examples should call this from their shutdown path (mirroring
        ``examples/pipe_check/`` LED cleanup).
        """
        if self._gpio is None:
            return
        try:
            self._gpio.cleanup(self._armed_pin)
        except Exception:  # pragma: no cover - hardware-specific
            pass
        try:
            self._gpio.cleanup(self._moving_pin)
        except Exception:  # pragma: no cover - hardware-specific
            pass
        self._ready = False
