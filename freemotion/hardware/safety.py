"""SafetyGate — enforce SafetyMode at the hardware-controller boundary.

The motion handlers in `freemotion.agent.builtins` already check
`cmd.safety` before calling into a controller. `SafetyGate` is a
**second** layer: a `HardwareController` wrapper that enforces a fixed
device-level safety mode regardless of what any handler does. This
means a future handler bug that forgets the safety check can't
actuate hardware, and per-command safety overrides cannot loosen the
device's default — they can only tighten it.

Per ADR-0006:

- ``dry_run``: ``arm()`` and ``move()`` refuse (return ``False``,
  log) — no inner controller call. ``disarm()`` and ``stop()`` pass
  through, because depowering an actuator is always the safer
  direction; refusing to LOWer a pin offers no safety benefit.
- ``bench`` and ``live``: every method passes through to the inner
  controller. Distinguishing what each mode permits is the inner
  controller's job (e.g. a future motor controller can refuse
  motor-driving primitives in ``bench`` while permitting indicator
  pins).

The gate also stamps the active safety mode into ``state()`` so
``/status`` telemetry exposes the runtime's effective safety floor
without requiring callers to wire the config separately.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from freemotion.protocol import SafetyMode

from .interface import HardwareController

LOG = logging.getLogger("freemotion.hardware.safety")


class SafetyGate:
    """`HardwareController` wrapper that enforces a fixed `SafetyMode`.

    Construct once at startup with ``cfg.safety_default``. The gate is
    intentionally immutable — a runtime safety-mode change is a
    process-level event (restart with new config), not a per-command
    knob. Per-command safety stays a handler-layer concern.
    """

    def __init__(self, inner: HardwareController, safety: SafetyMode) -> None:
        self._inner = inner
        self._safety = safety

    @property
    def name(self) -> str:
        inner_name = getattr(self._inner, "name", "controller")
        return f"safety-gated:{inner_name}"

    @property
    def available(self) -> bool:
        return bool(self._inner.available)

    @property
    def safety(self) -> SafetyMode:
        return self._safety

    @property
    def inner(self) -> HardwareController:
        """The underlying controller. Useful for example wiring (e.g.
        connecting `controller.cleanup()` to a shutdown hook) without
        going through the gate."""
        return self._inner

    def arm(self) -> bool:
        if self._safety == SafetyMode.DRY_RUN:
            LOG.info("dry_run: refusing arm at SafetyGate")
            return False
        return bool(self._inner.arm())

    def disarm(self) -> None:
        # Depowering is always safe; passes through in every mode.
        self._inner.disarm()

    def stop(self) -> None:
        # Per ADR-0004, stop is unconditional. Bypasses every safety
        # gate on its way to the inner controller.
        self._inner.stop()

    def move(self, dx: float, dy: float, dz: float) -> bool:
        if self._safety == SafetyMode.DRY_RUN:
            LOG.info(
                "dry_run: refusing move(%s, %s, %s) at SafetyGate", dx, dy, dz
            )
            return False
        return bool(self._inner.move(dx, dy, dz))

    def state(self) -> Dict[str, Any]:
        s: Dict[str, Any] = dict(self._inner.state())
        s["safety"] = self._safety.value
        return s
