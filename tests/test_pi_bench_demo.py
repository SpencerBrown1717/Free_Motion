"""Smoke tests for examples/pi_bench_demo.

Verifies the example imports cleanly on a non-Pi host, ``build_router``
registers exactly the M4 Phase 2 command set, the deny policy carries
through (and ``stop`` is exempt), and ``stop``'s ``on_stop`` hook is
wired through to the controller.

The tests use a `MockHardwareController` so CI never needs ``RPi.GPIO``.
The Pi-specific path is covered in ``tests/test_pi.py``.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "examples", "pi_bench_demo")
)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pi_bench_demo  # noqa: E402

from freemotion.config import Config  # noqa: E402
from freemotion.hardware import MockHardwareController  # noqa: E402
from freemotion.protocol import (  # noqa: E402
    Command,
    CommandName,
    SafetyMode,
)


def _cfg(**overrides) -> Config:
    base = dict(token="abc", device_id="pi-bench-test", safety_default=SafetyMode.BENCH)
    base.update(overrides)
    return Config(**base)


def test_pi_bench_demo_imports() -> None:
    assert hasattr(pi_bench_demo, "main")
    assert hasattr(pi_bench_demo, "build_router")


def test_build_router_registers_phase2_command_set() -> None:
    """Phase 2 plan is brutally narrow: ping + capabilities + status +
    arm + move + stop + disarm. Nothing else."""
    cfg = _cfg()
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)
    expected = {
        CommandName.PING.value,
        CommandName.STATUS.value,
        CommandName.CAPABILITIES.value,
        CommandName.ARM.value,
        CommandName.DISARM.value,
        CommandName.MOVE.value,
        CommandName.STOP.value,
    }
    assert set(router.known) == expected


def test_build_router_passes_denied_commands_through() -> None:
    cfg = _cfg(denied_commands=frozenset({"arm", "move"}))
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)
    assert router.denied == frozenset({"arm", "move"})


def test_stop_remains_exempt_from_deny_list() -> None:
    """`stop` must remain dispatchable even if listed (matches ADR-0004
    and Config's strip-on-load behavior)."""
    cfg = _cfg(denied_commands=frozenset({"arm", "move"}))
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)
    assert "stop" not in router.denied
    cmd = Command(cmd=CommandName.STOP, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(cmd)
    assert reply.ok is True
    assert reply.message == "stopped"


def test_arm_when_denied_returns_denied_by_policy() -> None:
    cfg = _cfg(denied_commands=frozenset({"arm"}))
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)
    cmd = Command(cmd=CommandName.ARM, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code.value == "denied_by_policy"
    assert controller.state()["armed"] is False


def test_stop_handler_calls_controller_stop() -> None:
    """`/stop` must drive the controller back to idle, not just ack."""
    cfg = _cfg()
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)

    arm_cmd = Command(cmd=CommandName.ARM, sender="t", safety=SafetyMode.BENCH)
    router.dispatch(arm_cmd)
    assert controller.state()["armed"] is True

    stop_cmd = Command(cmd=CommandName.STOP, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(stop_cmd)
    assert reply.ok is True
    assert controller.state()["armed"] is False


def test_status_carries_controller_telemetry() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)
    cmd = Command(cmd=CommandName.STATUS, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(cmd)
    assert reply.ok is True
    assert "controller" in reply.telemetry
    ctl = reply.telemetry["controller"]
    assert ctl["armed"] is False
    assert ctl["position"] == [0.0, 0.0, 0.0]


def test_move_in_dry_run_does_not_change_position() -> None:
    cfg = _cfg(safety_default=SafetyMode.DRY_RUN)
    controller = MockHardwareController()
    router = pi_bench_demo.build_router(cfg, controller)
    arm_cmd = Command(cmd=CommandName.ARM, sender="t", safety=SafetyMode.BENCH)
    router.dispatch(arm_cmd)
    move_cmd = Command(
        cmd=CommandName.MOVE,
        sender="t",
        args={"x": 1, "y": 0, "z": 0},
        safety=SafetyMode.DRY_RUN,
    )
    reply = router.dispatch(move_cmd)
    assert reply.ok is True
    assert "dry_run" in reply.message
    assert controller.state()["position"] == [0.0, 0.0, 0.0]
