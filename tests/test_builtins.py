"""Tests for freemotion.agent built-in handlers."""

from __future__ import annotations

from freemotion.agent import (
    make_arm_handler,
    make_capabilities_handler,
    make_disarm_handler,
    make_move_handler,
    make_ping_handler,
    make_status_handler,
    make_stop_handler,
)
from freemotion.config import Config
from freemotion.hardware import MockHardwareController
from freemotion.protocol import Command, CommandName, ErrorCode, SafetyMode
from freemotion.router import Router


def _cfg() -> Config:
    return Config(
        token="abc",
        device_id="dev-test",
        safety_default=SafetyMode.BENCH,
        hardware_profile="host",
    )


def test_ping_handler_returns_pong() -> None:
    handler = make_ping_handler(_cfg())
    cmd = Command(cmd=CommandName.PING, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.message == "pong"
    assert reply.sender == "dev-test"
    assert reply.correlation_id == cmd.correlation_id


def test_stop_handler_invokes_callback() -> None:
    called: list[bool] = []
    handler = make_stop_handler(
        _cfg(), on_stop=lambda: called.append(True)
    )
    cmd = Command(cmd=CommandName.STOP, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.message == "stopped"
    assert called == [True]


def test_stop_handler_swallows_callback_exceptions() -> None:
    def bad() -> None:
        raise RuntimeError("nope")

    handler = make_stop_handler(_cfg(), on_stop=bad)
    cmd = Command(cmd=CommandName.STOP, sender="x")
    reply = handler(cmd)
    assert reply.ok is True


def test_stop_handler_without_callback_still_acks() -> None:
    handler = make_stop_handler(_cfg())
    cmd = Command(cmd=CommandName.STOP, sender="x")
    reply = handler(cmd)
    assert reply.ok is True


def test_status_handler_includes_telemetry() -> None:
    handler = make_status_handler(_cfg(), gpio_available=True)
    cmd = Command(cmd=CommandName.STATUS, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.telemetry["device_id"] == "dev-test"
    assert reply.telemetry["hardware"] == "host"
    assert reply.telemetry["safety_default"] == "bench"
    assert reply.telemetry["gpio_available"] is True
    assert "uptime_s" in reply.telemetry
    assert "freemotion:" in reply.message


def test_status_handler_with_controller_includes_state() -> None:
    controller = MockHardwareController()
    controller.arm()
    handler = make_status_handler(_cfg(), controller=controller)
    cmd = Command(cmd=CommandName.STATUS, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert "controller" in reply.telemetry
    assert reply.telemetry["controller"]["armed"] is True
    assert "armed: yes" in reply.message


def test_capabilities_handler_lists_router_commands() -> None:
    cfg = _cfg()
    router = Router(device_id=cfg.device_id)
    router.register(CommandName.PING, make_ping_handler(cfg))
    router.register(CommandName.STATUS, make_status_handler(cfg))
    cap = make_capabilities_handler(cfg, router)
    router.register(CommandName.CAPABILITIES, cap)

    cmd = Command(cmd=CommandName.CAPABILITIES, sender="x")
    reply = cap(cmd)
    assert reply.ok is True
    caps = reply.telemetry["capabilities"]
    assert set(caps) >= {"ping", "status", "capabilities"}
    assert reply.telemetry["device_id"] == "dev-test"


# --- motion handlers (HardwareController) ---


def test_arm_handler_accepts_in_bench_mode() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    handler = make_arm_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.ARM, sender="x", safety=SafetyMode.BENCH
    )
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.state == "armed"
    assert reply.message == "armed"
    assert controller.state()["armed"] is True


def test_arm_handler_refuses_in_dry_run() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    handler = make_arm_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.ARM, sender="x", safety=SafetyMode.DRY_RUN
    )
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.UNSAFE_IN_MODE
    assert controller.state()["armed"] is False


def test_arm_handler_surfaces_controller_refusal() -> None:
    cfg = _cfg()
    controller = MockHardwareController(battery_start=5.0)
    handler = make_arm_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.ARM, sender="x", safety=SafetyMode.BENCH
    )
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.UNSAFE_IN_MODE


def test_disarm_handler_idempotent() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    handler = make_disarm_handler(cfg, controller)
    cmd = Command(cmd=CommandName.DISARM, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.state == "idle"


def test_move_handler_dry_run_logs_but_does_not_apply() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    controller.arm()
    handler = make_move_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.MOVE,
        sender="x",
        args={"x": 1, "y": 2, "z": 3},
        safety=SafetyMode.DRY_RUN,
    )
    reply = handler(cmd)
    assert reply.ok is True
    assert "dry_run" in reply.message
    assert controller.state()["position"] == [0.0, 0.0, 0.0]


def test_move_handler_applies_in_bench() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    controller.arm()
    handler = make_move_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.MOVE,
        sender="x",
        args={"x": 1, "y": 2, "z": 3},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.state == "moving"
    assert controller.state()["position"] == [1.0, 2.0, 3.0]


def test_move_handler_rejects_non_numeric_args() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    controller.arm()
    handler = make_move_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.MOVE,
        sender="x",
        args={"x": "nope"},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.BAD_ARGS


def test_move_handler_unarmed_returns_error() -> None:
    cfg = _cfg()
    controller = MockHardwareController()
    handler = make_move_handler(cfg, controller)
    cmd = Command(
        cmd=CommandName.MOVE,
        sender="x",
        args={"x": 1, "y": 0, "z": 0},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.UNSAFE_IN_MODE
