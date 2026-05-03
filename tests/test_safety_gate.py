"""Tests for freemotion.hardware.safety.SafetyGate.

The gate is the M4 Phase 3 safety floor: in `dry_run`, no controller
call can actuate `arm` or `move`, regardless of what any handler does.
`disarm` and `stop` always pass through (depowering is always safe).
In `bench` / `live`, every call passes through to the inner controller.

These tests use a `_CountingController` so we can assert what the
inner controller saw, not just what the gate replied. That's the
single testable invariant the gate exists to provide.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from freemotion.hardware import (
    HardwareController,
    MockHardwareController,
    SafetyGate,
)
from freemotion.protocol import SafetyMode


class _CountingController:
    """Records every method call. Wraps a `MockHardwareController` for
    realistic responses to `arm` / `move` / `state`."""

    name = "counting"

    def __init__(self) -> None:
        self._inner = MockHardwareController()
        self.calls: List[Tuple[str, tuple]] = []

    @property
    def available(self) -> bool:
        return True

    def arm(self) -> bool:
        self.calls.append(("arm", ()))
        return self._inner.arm()

    def disarm(self) -> None:
        self.calls.append(("disarm", ()))
        self._inner.disarm()

    def stop(self) -> None:
        self.calls.append(("stop", ()))
        self._inner.stop()

    def move(self, dx: float, dy: float, dz: float) -> bool:
        self.calls.append(("move", (dx, dy, dz)))
        return self._inner.move(dx, dy, dz)

    def state(self) -> Dict[str, Any]:
        self.calls.append(("state", ()))
        return self._inner.state()


def _names(calls: List[Tuple[str, tuple]]) -> List[str]:
    return [name for name, _ in calls]


def test_gate_satisfies_protocol() -> None:
    gate = SafetyGate(MockHardwareController(), SafetyMode.BENCH)
    assert isinstance(gate, HardwareController)


def test_gate_exposes_inner_and_safety() -> None:
    inner = MockHardwareController()
    gate = SafetyGate(inner, SafetyMode.BENCH)
    assert gate.inner is inner
    assert gate.safety == SafetyMode.BENCH
    assert "safety-gated" in gate.name


def test_gate_state_carries_safety_field() -> None:
    gate = SafetyGate(MockHardwareController(), SafetyMode.BENCH)
    s = gate.state()
    assert s["safety"] == "bench"
    assert s["armed"] is False
    assert s["position"] == [0.0, 0.0, 0.0]


def test_dry_run_refuses_arm_without_calling_inner() -> None:
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.DRY_RUN)
    assert gate.arm() is False
    assert "arm" not in _names(inner.calls)
    assert gate.state()["armed"] is False


def test_dry_run_refuses_move_without_calling_inner() -> None:
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.DRY_RUN)
    assert gate.move(1.0, 0.0, 0.0) is False
    assert "move" not in _names(inner.calls)
    assert gate.state()["position"] == [0.0, 0.0, 0.0]


def test_dry_run_passes_disarm_through() -> None:
    """Depowering is always safe; gate must not block disarm."""
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.DRY_RUN)
    gate.disarm()
    assert "disarm" in _names(inner.calls)


def test_dry_run_passes_stop_through() -> None:
    """ADR-0004: stop is unconditional. Must reach the inner controller."""
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.DRY_RUN)
    gate.stop()
    assert "stop" in _names(inner.calls)


def test_bench_passes_arm_through() -> None:
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.BENCH)
    assert gate.arm() is True
    assert ("arm", ()) in inner.calls


def test_bench_passes_move_through_with_args() -> None:
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.BENCH)
    gate.arm()
    assert gate.move(1.0, 2.0, 3.0) is True
    assert ("move", (1.0, 2.0, 3.0)) in inner.calls


def test_live_passes_everything_through() -> None:
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.LIVE)
    gate.arm()
    gate.move(1.0, 0.0, 0.0)
    gate.disarm()
    gate.stop()
    assert _names(inner.calls) == ["arm", "move", "disarm", "stop"]


def test_dry_run_arm_returns_false_even_if_inner_would_succeed() -> None:
    """The gate is the floor: a fully-charged inner mock would arm
    happily, but the gate still refuses in dry_run."""
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.DRY_RUN)
    assert gate.arm() is False
    # Confirm via inner: it's still idle, never called.
    assert inner._inner.state()["armed"] is False


def test_per_command_override_cannot_actuate_in_dry_run_device() -> None:
    """The gate models the integration: handler accepts a per-command
    `safety=bench` override; gate (configured `dry_run`) refuses;
    handler then surfaces an `unsafe_in_mode` reply via its existing
    `controller.arm() == False` path."""
    from freemotion.agent import make_arm_handler
    from freemotion.config import Config
    from freemotion.protocol import Command, CommandName

    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.DRY_RUN)
    cfg = Config(token="abc", device_id="dev", safety_default=SafetyMode.DRY_RUN)
    handler = make_arm_handler(cfg, gate)

    cmd = Command(cmd=CommandName.ARM, sender="t", safety=SafetyMode.BENCH)
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code.value == "unsafe_in_mode"
    assert "arm" not in _names(inner.calls)


def test_inner_state_passthrough_on_state_call() -> None:
    """`state()` reads from the inner controller, then layers `safety`."""
    inner = _CountingController()
    gate = SafetyGate(inner, SafetyMode.BENCH)
    gate.arm()
    s = gate.state()
    assert s["armed"] is True
    assert s["safety"] == "bench"
    assert "state" in _names(inner.calls)


def test_state_has_independent_dict_per_call() -> None:
    """Mutating the returned dict must not corrupt the inner state."""
    gate = SafetyGate(MockHardwareController(), SafetyMode.BENCH)
    s = gate.state()
    s["armed"] = "tampered"
    s2 = gate.state()
    assert s2["armed"] is False
