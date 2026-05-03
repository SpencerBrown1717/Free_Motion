"""Smoke tests for examples/pi_closed_loop_demo.

Verifies the end-to-end Step 2 wiring without spinning up a Telegram
client, a real Pi camera, a real YOLO model, or a real Gemma model.
The demo's `build_router_without_loop` and `attach_mission_loop` are
the seams that get unit-tested here; the running-loop behavior is
already covered by `tests/test_mission_loop.py`.

What we assert:

- The example imports cleanly on a non-Pi host.
- `build_router_without_loop` registers exactly the operator-facing
  command set (no `mission_start`, no `status` — those need the loop).
- `attach_mission_loop` adds `mission_start` and `status` (with loop
  telemetry) to the existing router.
- `/stop` halts the mission loop AND drops controller pins LOW —
  unconditional master-kill, even with the deny list and the
  SafetyGate active.
- `/status` carries both `controller` telemetry and `mission_loop`
  telemetry in one reply.
- `/mission_start` is refused in `dry_run` (the loop never starts).
- The README and systemd unit exist and have the right shape.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "examples", "pi_closed_loop_demo")
)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pi_closed_loop_demo  # noqa: E402

from freemotion.agent import MissionLoop  # noqa: E402
from freemotion.config import Config  # noqa: E402
from freemotion.hardware import (  # noqa: E402
    MockHardwareController,
    SafetyGate,
)
from freemotion.mission_control import (  # noqa: E402
    MissionDecision,
    MockMissionControl,
)
from freemotion.protocol import (  # noqa: E402
    Command,
    CommandName,
    SafetyMode,
)
from freemotion.vision import MockVision, VisionResult  # noqa: E402
from freemotion.world import WorldState  # noqa: E402


def _cfg(safety: SafetyMode = SafetyMode.BENCH, **kw) -> Config:
    base = dict(
        token="abc",
        device_id="pi-closed-loop-test",
        safety_default=safety,
    )
    base.update(kw)
    return Config(**base)


def _build(
    *,
    cfg: Optional[Config] = None,
    safety: SafetyMode = SafetyMode.BENCH,
    decision: Optional[MissionDecision] = None,
):
    cfg = cfg or _cfg(safety=safety)
    inner = MockHardwareController()
    controller = SafetyGate(inner, cfg.safety_default)

    stop_calls: list[str] = []

    def _stop_everything() -> None:
        stop_calls.append("loop")
        loop.stop()
        stop_calls.append("controller")
        controller.stop()

    router = pi_closed_loop_demo.build_router_without_loop(
        cfg, controller=controller, on_stop=_stop_everything
    )

    vision = MockVision(
        scripted=[
            VisionResult(detections=()),
        ]
    )
    mission = MockMissionControl()
    loop = MissionLoop(
        vision=vision,
        mission=mission,
        world=WorldState(),
        router=router,
        cfg=cfg,
        tick_interval_s=10.0,  # avoid live ticking; tests drive the loop directly
    )
    pi_closed_loop_demo.attach_mission_loop(
        router,
        cfg=cfg,
        controller=controller,
        mission_loop=loop,
        default_intent="follow person",
    )
    return cfg, inner, controller, router, loop, stop_calls


# ----------------------------------------------------------------------
# import + structure
# ----------------------------------------------------------------------


def test_demo_imports() -> None:
    assert hasattr(pi_closed_loop_demo, "main")
    assert hasattr(pi_closed_loop_demo, "build_router_without_loop")
    assert hasattr(pi_closed_loop_demo, "attach_mission_loop")


def test_readme_and_systemd_unit_exist() -> None:
    root = Path(_DEMO_DIR)
    assert (root / "README.md").is_file()
    unit = root / "systemd" / "freemotion-pi-closed-loop-demo.service"
    assert unit.is_file()
    text = unit.read_text()
    # The unit must reference the closed-loop demo entry point, not
    # the bench demo's — copy/paste regression guard.
    assert "pi_closed_loop_demo/pi_closed_loop_demo.py" in text


# ----------------------------------------------------------------------
# router shape
# ----------------------------------------------------------------------


def test_router_without_loop_excludes_status_and_mission_start() -> None:
    """Status and mission_start need the loop, so they must not be
    registered until `attach_mission_loop` runs. This guards against
    a refactor that accidentally double-registers either."""
    cfg = _cfg()
    controller = SafetyGate(MockHardwareController(), cfg.safety_default)
    router = pi_closed_loop_demo.build_router_without_loop(
        cfg, controller=controller, on_stop=lambda: None
    )
    known = set(router.known)
    assert "status" not in known
    assert "mission_start" not in known
    assert known == {"ping", "capabilities", "stop", "arm", "disarm", "move"}


def test_attach_mission_loop_adds_status_and_mission_start() -> None:
    _, _, _, router, _, _ = _build()
    known = set(router.known)
    assert "status" in known
    assert "mission_start" in known
    expected = {
        "ping",
        "capabilities",
        "stop",
        "arm",
        "disarm",
        "move",
        "status",
        "mission_start",
    }
    assert known == expected


# ----------------------------------------------------------------------
# /stop is the master kill
# ----------------------------------------------------------------------


def test_stop_halts_loop_and_drops_pins() -> None:
    """`/stop` must (a) stop the mission loop, (b) drop the controller
    pins LOW. Even if the loop was running and the controller was
    armed, both must end at idle/false."""
    cfg, inner, controller, router, loop, stop_calls = _build()

    # Arm and start the loop.
    arm = Command(cmd=CommandName.ARM, sender="t", safety=SafetyMode.BENCH)
    router.dispatch(arm)
    assert inner.state()["armed"] is True
    loop.start(intent="follow")
    assert loop.is_running is True

    stop = Command(cmd=CommandName.STOP, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(stop)
    assert reply.ok is True
    assert reply.message == "stopped"
    assert loop.is_running is False
    assert inner.state()["armed"] is False
    # Loop is stopped FIRST so no tick can race a fresh dispatch.
    assert stop_calls == ["loop", "controller"]


def test_stop_remains_unconditional_with_deny_list() -> None:
    """Even if `stop` were listed in `denied_commands`, the router
    drops it from the deny set (Config + Router both enforce this).
    The mission loop's stop and the controller's stop both still
    fire."""
    cfg = _cfg(denied_commands=frozenset({"stop", "move", "arm"}))
    _, inner, _, router, loop, stop_calls = _build(cfg=cfg)
    inner.arm()
    loop.start(intent="follow")

    stop = Command(cmd=CommandName.STOP, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(stop)
    assert reply.ok is True
    assert loop.is_running is False
    assert inner.state()["armed"] is False


def test_stop_in_dry_run_still_drops_pins() -> None:
    """Per ADR-0006, `stop` passes through the SafetyGate. The closed
    loop's master-kill must work in every safety mode."""
    cfg, inner, _, router, loop, _ = _build(safety=SafetyMode.DRY_RUN)
    inner.arm()  # bypass the gate to set up the precondition
    assert inner.state()["armed"] is True

    stop = Command(cmd=CommandName.STOP, sender="t", safety=SafetyMode.DRY_RUN)
    reply = router.dispatch(stop)
    assert reply.ok is True
    assert inner.state()["armed"] is False
    assert loop.is_running is False


# ----------------------------------------------------------------------
# /status reflects mission loop state
# ----------------------------------------------------------------------


def test_status_carries_controller_and_mission_loop_telemetry() -> None:
    _, _, _, router, _, _ = _build()
    cmd = Command(cmd=CommandName.STATUS, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(cmd)
    assert reply.ok is True
    assert "controller" in reply.telemetry
    assert "mission_loop" in reply.telemetry
    assert reply.telemetry["mission_loop"]["running"] is False


def test_status_after_mission_start_reports_running() -> None:
    cfg, _, _, router, loop, _ = _build()
    start = Command(
        cmd=CommandName.MISSION_START,
        sender="t",
        args={"intent": "follow"},
        safety=SafetyMode.BENCH,
    )
    reply = router.dispatch(start)
    assert reply.ok is True
    try:
        cmd = Command(
            cmd=CommandName.STATUS, sender="t", safety=SafetyMode.BENCH
        )
        reply = router.dispatch(cmd)
        assert reply.telemetry["mission_loop"]["running"] is True
        assert reply.telemetry["mission_loop"]["intent"] == "follow"
        assert "mission: running" in reply.message
    finally:
        loop.stop()


# ----------------------------------------------------------------------
# /mission_start respects safety modes
# ----------------------------------------------------------------------


def test_mission_start_refused_in_dry_run() -> None:
    cfg, _, _, router, loop, _ = _build(safety=SafetyMode.DRY_RUN)
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="t",
        args={"intent": "follow"},
        safety=SafetyMode.DRY_RUN,
    )
    reply = router.dispatch(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code.value == "unsafe_in_mode"
    assert loop.is_running is False


def test_mission_start_uses_default_intent_when_args_empty() -> None:
    cfg, _, _, router, loop, _ = _build()
    cmd = Command(
        cmd=CommandName.MISSION_START,
        sender="t",
        args={},
        safety=SafetyMode.BENCH,
    )
    reply = router.dispatch(cmd)
    try:
        assert reply.ok is True
        assert "follow person" in reply.message  # the configured default
    finally:
        loop.stop()


def test_mission_start_idempotent_when_already_running() -> None:
    cfg, _, _, router, loop, _ = _build()
    start = Command(
        cmd=CommandName.MISSION_START,
        sender="t",
        args={"intent": "first"},
        safety=SafetyMode.BENCH,
    )
    reply1 = router.dispatch(start)
    reply2 = router.dispatch(start)
    try:
        assert reply1.ok is True
        assert reply2.ok is True
        assert "already running" in reply2.message
        assert loop.intent == "first"  # second start did not overwrite
    finally:
        loop.stop()


# ----------------------------------------------------------------------
# main() exit codes for missing dependencies
# ----------------------------------------------------------------------


class _OfflineCam:
    """Cheap stand-in that mimics PiCameraSource()'s offline state."""

    available = False

    def close(self) -> None:
        pass

    def __call__(self) -> None:
        return None


def test_main_returns_2_when_camera_offline(monkeypatch) -> None:
    """If `picamera2` isn't installed (or the camera won't start), the
    demo logs and exits with code 2 instead of starting a useless loop."""
    monkeypatch.setattr(
        pi_closed_loop_demo,
        "PiCameraSource",
        lambda *a, **kw: _OfflineCam(),
    )
    monkeypatch.setattr(sys, "argv", ["pi_closed_loop_demo"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("FREEMOTION_HARDWARE", "host")  # off-Pi mock controller
    rc = pi_closed_loop_demo.main()
    assert rc == 2


class _OfflineVision:
    name = "stub"
    available = False

    def scene(self) -> VisionResult:
        return VisionResult(detections=())


class _OnlineCam:
    available = True

    def close(self) -> None:
        pass

    def __call__(self) -> None:
        return None


def test_main_returns_3_when_vision_offline(monkeypatch) -> None:
    """Camera available but the vision backend is unusable -> exit 3.
    Refusing to start a perception-blind loop is the safer default."""
    monkeypatch.setattr(
        pi_closed_loop_demo,
        "PiCameraSource",
        lambda *a, **kw: _OnlineCam(),
    )
    monkeypatch.setattr(
        pi_closed_loop_demo,
        "make_vision_from_config",
        lambda *a, **kw: _OfflineVision(),
    )
    monkeypatch.setattr(sys, "argv", ["pi_closed_loop_demo"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("FREEMOTION_HARDWARE", "host")
    rc = pi_closed_loop_demo.main()
    assert rc == 3


# ----------------------------------------------------------------------
# Step 3 — graceful_shutdown helper
# ----------------------------------------------------------------------


class _Trace:
    """Records call order across the demo's teardown sequence."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def make(self, name: str, *, raises: bool = False):
        def fn(*_a, **_kw) -> None:
            self.calls.append(name)
            if raises:
                raise RuntimeError(f"{name} raised")

        return fn


class _StubLoop:
    def __init__(self, trace: _Trace, *, raises: bool = False) -> None:
        self._trace = trace
        self._raises = raises

    def stop(self) -> None:
        self._trace.calls.append("loop")
        if self._raises:
            raise RuntimeError("loop.stop raised")


class _StubController:
    def __init__(self, trace: _Trace, *, raises: bool = False) -> None:
        self._trace = trace
        self._raises = raises

    def stop(self) -> None:
        self._trace.calls.append("controller")
        if self._raises:
            raise RuntimeError("controller.stop raised")


class _StubCam:
    def __init__(self, trace: _Trace, *, raises: bool = False) -> None:
        self._trace = trace
        self._raises = raises

    def close(self) -> None:
        self._trace.calls.append("cam")
        if self._raises:
            raise RuntimeError("cam.close raised")


class _StubInner:
    def __init__(self, trace: _Trace, *, has_cleanup: bool = True) -> None:
        if has_cleanup:
            self.cleanup = self._cleanup_impl
        self._trace = trace

    def _cleanup_impl(self) -> None:
        self._trace.calls.append("inner_cleanup")


def test_graceful_shutdown_runs_in_order() -> None:
    """Loop FIRST, then controller, then cam, then inner.cleanup.

    This order is the survivability contract from Step 3:
    - loop.stop() before controller.stop() so no in-flight tick can
      dispatch a fresh MOVE *after* the pins are dropped LOW.
    - cam.close() after controller.stop() so a hung tick releasing
      the camera does not race a /stop trying to drop the pins.
    - inner_cleanup last, after everyone is done with GPIO.
    """
    trace = _Trace()
    pi_closed_loop_demo.graceful_shutdown(
        mission_loop=_StubLoop(trace),
        controller=_StubController(trace),
        cam=_StubCam(trace),
        inner=_StubInner(trace),
    )
    assert trace.calls == ["loop", "controller", "cam", "inner_cleanup"]


def test_graceful_shutdown_continues_when_loop_stop_raises() -> None:
    """A broken layer cannot block the rest of the teardown."""
    trace = _Trace()
    pi_closed_loop_demo.graceful_shutdown(
        mission_loop=_StubLoop(trace, raises=True),
        controller=_StubController(trace),
        cam=_StubCam(trace),
        inner=_StubInner(trace),
    )
    assert trace.calls == ["loop", "controller", "cam", "inner_cleanup"]


def test_graceful_shutdown_continues_when_controller_stop_raises() -> None:
    trace = _Trace()
    pi_closed_loop_demo.graceful_shutdown(
        mission_loop=_StubLoop(trace),
        controller=_StubController(trace, raises=True),
        cam=_StubCam(trace),
        inner=_StubInner(trace),
    )
    assert trace.calls == ["loop", "controller", "cam", "inner_cleanup"]


def test_graceful_shutdown_continues_when_cam_close_raises() -> None:
    trace = _Trace()
    pi_closed_loop_demo.graceful_shutdown(
        mission_loop=_StubLoop(trace),
        controller=_StubController(trace),
        cam=_StubCam(trace, raises=True),
        inner=_StubInner(trace),
    )
    assert trace.calls == ["loop", "controller", "cam", "inner_cleanup"]


def test_graceful_shutdown_works_when_inner_has_no_cleanup() -> None:
    """`MockHardwareController` doesn't implement `cleanup()`. The
    helper must stay polymorphic across mock and Pi controllers."""
    trace = _Trace()
    pi_closed_loop_demo.graceful_shutdown(
        mission_loop=_StubLoop(trace),
        controller=_StubController(trace),
        cam=_StubCam(trace),
        inner=_StubInner(trace, has_cleanup=False),
    )
    assert trace.calls == ["loop", "controller", "cam"]


def test_graceful_shutdown_idempotent_against_double_invocation() -> None:
    """SIGTERM after `/stop` already ran the teardown should be a no-op
    on the second pass — by virtue of every underlying primitive being
    idempotent. The helper itself doesn't dedup because each underlying
    `stop`/`close`/`cleanup` is already idempotent in the v1 contract
    (ADRs 0006, 0009, 0010)."""
    trace = _Trace()
    args = dict(
        mission_loop=_StubLoop(trace),
        controller=_StubController(trace),
        cam=_StubCam(trace),
        inner=_StubInner(trace),
    )
    pi_closed_loop_demo.graceful_shutdown(**args)
    pi_closed_loop_demo.graceful_shutdown(**args)
    # Eight calls, four per pass, no exceptions.
    assert trace.calls == [
        "loop", "controller", "cam", "inner_cleanup",
        "loop", "controller", "cam", "inner_cleanup",
    ]


# ----------------------------------------------------------------------
# Step 3 — degraded summary surfaces in /status (smoke; full coverage
# in tests/test_builtins.py)
# ----------------------------------------------------------------------


def test_status_message_includes_degraded_summary_when_loop_is_degraded() -> None:
    """End-to-end: build the demo router with a loop whose state()
    reports `degraded=True` and verify the operator's `/status` reply
    contains the `[DEGRADED: ...]` summary line."""
    cfg = _cfg()

    class _DegradedLoop:
        intent = None

        def state(self) -> dict:
            return {
                "running": True,
                "intent": "follow person",
                "tick_count": 12,
                "degraded": True,
                "degraded_reason": "vision_failures>=5 (7)",
                "world_stale": True,
                "world_age_s": 8.3,
            }

    inner = MockHardwareController()
    controller = SafetyGate(inner, cfg.safety_default)
    router = pi_closed_loop_demo.build_router_without_loop(
        cfg, controller=controller, on_stop=lambda: None
    )
    pi_closed_loop_demo.attach_mission_loop(
        router,
        cfg=cfg,
        controller=controller,
        mission_loop=_DegradedLoop(),
        default_intent="follow person",
    )

    cmd = Command(cmd=CommandName.STATUS, sender="t", safety=SafetyMode.BENCH)
    reply = router.dispatch(cmd)
    assert reply.ok is True
    assert "[DEGRADED: vision_failures>=5 (7)]" in reply.message
    assert "[stale world: 8.3s]" in reply.message
    assert reply.telemetry["mission_loop"]["degraded"] is True
    assert reply.telemetry["mission_loop"]["world_stale"] is True
