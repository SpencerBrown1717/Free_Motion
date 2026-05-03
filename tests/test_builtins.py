"""Tests for freemotion.agent built-in handlers."""

from __future__ import annotations

from typing import Any, Dict

from freemotion.agent import (
    make_arm_handler,
    make_capabilities_handler,
    make_disarm_handler,
    make_mission_start_handler,
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


# --- mission_start handler + status with mission_loop ---


class _FakeLoop:
    """Stand-in for `MissionLoop`. Only what the handlers and status
    handler actually call: `start(intent=...)`, `state() -> dict`."""

    def __init__(
        self,
        *,
        already_running: bool = False,
        start_raises: bool = False,
        state_raises: bool = False,
        state_returns_non_dict: bool = False,
    ) -> None:
        self._running = already_running
        self.start_calls: list[str] = []
        self.start_raises = start_raises
        self.state_raises = state_raises
        self.state_returns_non_dict = state_returns_non_dict

    def start(self, *, intent: str) -> bool:
        self.start_calls.append(intent)
        if self.start_raises:
            raise RuntimeError("start failed")
        if self._running:
            return False
        self._running = True
        return True

    def state(self) -> Dict[str, Any]:
        if self.state_raises:
            raise RuntimeError("state failed")
        if self.state_returns_non_dict:
            return "not a dict"  # type: ignore[return-value]
        return {
            "running": self._running,
            "intent": self.start_calls[-1] if self.start_calls else None,
            "tick_count": 1 if self._running else 0,
        }


def test_mission_start_handler_starts_loop_in_bench() -> None:
    loop = _FakeLoop()
    handler = make_mission_start_handler(_cfg(), mission_loop=loop)
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="x",
        args={"intent": "follow person"},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.state == "running"
    assert "follow person" in reply.message
    assert reply.telemetry["running"] is True
    assert loop.start_calls == ["follow person"]


def test_mission_start_handler_refuses_in_dry_run() -> None:
    loop = _FakeLoop()
    handler = make_mission_start_handler(_cfg(), mission_loop=loop)
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="x",
        args={"intent": "follow"},
        safety=SafetyMode.DRY_RUN,
    )
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.UNSAFE_IN_MODE
    # Loop was never asked to start.
    assert loop.start_calls == []


def test_mission_start_handler_falls_back_to_default_intent() -> None:
    loop = _FakeLoop()
    handler = make_mission_start_handler(
        _cfg(), mission_loop=loop, default_intent="follow person"
    )
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="x",
        args={"intent": ""},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is True
    assert loop.start_calls == ["follow person"]


def test_mission_start_handler_idempotent_when_already_running() -> None:
    loop = _FakeLoop(already_running=True)
    handler = make_mission_start_handler(_cfg(), mission_loop=loop)
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="x",
        args={"intent": "follow"},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is True
    assert "already running" in reply.message


def test_mission_start_handler_surfaces_internal_error() -> None:
    loop = _FakeLoop(start_raises=True)
    handler = make_mission_start_handler(_cfg(), mission_loop=loop)
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="x",
        args={"intent": "x"},
        safety=SafetyMode.BENCH,
    )
    reply = handler(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.INTERNAL


def test_status_handler_includes_mission_loop_state() -> None:
    loop = _FakeLoop(already_running=True)
    handler = make_status_handler(_cfg(), mission_loop=loop)
    cmd = Command(cmd=CommandName.STATUS, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert "mission_loop" in reply.telemetry
    assert reply.telemetry["mission_loop"]["running"] is True
    # Human-readable summary too.
    assert "mission: running" in reply.message


def test_status_handler_tolerates_loop_state_exception() -> None:
    loop = _FakeLoop(state_raises=True)
    handler = make_status_handler(_cfg(), mission_loop=loop)
    cmd = Command(cmd=CommandName.STATUS, sender="x")
    reply = handler(cmd)
    assert reply.ok is True
    assert reply.telemetry["mission_loop"]["running"] is False
    assert "loop.state() raised" in reply.telemetry["mission_loop"]["error"]


# --- Step 3: status formatting for degraded + stale-world signals ---


def _format_line(loop_state: dict) -> str:
    """Run a status handler with a stub loop returning `loop_state` and
    return the human-readable line for the mission loop."""

    class _Loop:
        def state(self) -> dict:
            return loop_state

    handler = make_status_handler(_cfg(), mission_loop=_Loop())
    cmd = Command(cmd=CommandName.STATUS, sender="x")
    reply = handler(cmd)
    for line in reply.message.split("\n"):
        if line.startswith("mission:"):
            return line
    raise AssertionError("no mission line in status message")


def test_status_summary_idle_when_loop_idle() -> None:
    line = _format_line({"running": False, "intent": None})
    assert line == "mission: idle"


def test_status_summary_running_includes_intent() -> None:
    line = _format_line({"running": True, "intent": "follow person"})
    assert line == "mission: running (intent='follow person')"


def test_status_summary_includes_degraded_with_reason() -> None:
    line = _format_line(
        {
            "running": True,
            "intent": "follow",
            "degraded": True,
            "degraded_reason": "dispatch_failures>=5 (6)",
        }
    )
    assert "mission: running" in line
    assert "[DEGRADED: dispatch_failures>=5 (6)]" in line


def test_status_summary_degraded_when_idle_still_visible() -> None:
    """A degraded device that's been /stop'd should still surface the
    last-known degraded reason — operator's ground truth post-mortem."""
    line = _format_line(
        {
            "running": False,
            "intent": None,
            "degraded": True,
            "degraded_reason": "vision_failures>=5 (12)",
        }
    )
    assert "mission: idle" in line
    assert "[DEGRADED: vision_failures>=5 (12)]" in line


def test_status_summary_includes_stale_world_when_running() -> None:
    line = _format_line(
        {
            "running": True,
            "intent": "follow",
            "world_stale": True,
            "world_age_s": 7.342,
        }
    )
    assert "[stale world: 7.3s]" in line


def test_status_summary_omits_stale_world_when_not_running() -> None:
    """A stopped loop is not actively stale — the `[stale world]`
    badge would be misleading. Only show it during an active mission."""
    line = _format_line(
        {
            "running": False,
            "intent": None,
            "world_stale": True,
            "world_age_s": 7.0,
        }
    )
    assert "[stale world" not in line
    assert line == "mission: idle"


def test_status_summary_handles_missing_world_age() -> None:
    line = _format_line(
        {
            "running": True,
            "intent": "follow",
            "world_stale": True,
            "world_age_s": None,
        }
    )
    assert "[stale world]" in line
