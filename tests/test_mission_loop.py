"""Tests for freemotion.agent.mission_loop.MissionLoop.

Covers Step 2's hard contracts directly:

- The loop ticks: vision -> world -> mission -> dispatch.
- World state gets updated with detections each tick.
- Only `MOVE` is dispatched from the loop. Anything else the policy
  returns (STOP / ARM / DISARM / etc.) is logged and ignored.
- Vision exceptions, mission exceptions, mission returning a
  non-MissionDecision, dispatch exceptions: none of them crash the
  loop; each increments the corresponding failure counter.
- `start(intent=...)` is idempotent (re-issuing while running is a
  no-op).
- `stop()` is idempotent and joins the thread within the timeout.
- `state()` is safe to call any time (including before start) and
  returns a JSON-able dict with all the telemetry `/status` needs.
- The loop dispatches with `cmd.safety = cfg.safety_default`, so the
  device-level safety floor is preserved.
"""

from __future__ import annotations

import time
from typing import Any, Callable, List, Optional

import pytest

from freemotion.agent import MissionLoop
from freemotion.config import Config
from freemotion.mission_control import MissionDecision
from freemotion.protocol import Command, CommandName, Reply, SafetyMode
from freemotion.router import Router
from freemotion.vision import Detection, VisionResult
from freemotion.world import WorldState


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _FakeVision:
    """Stand-in for `VisionBackend`. Returns scripted scenes; can raise."""

    name = "fake"

    def __init__(
        self,
        scenes: Optional[List[VisionResult]] = None,
        *,
        raises: Optional[Exception] = None,
    ) -> None:
        self._scenes = list(scenes or [])
        self._idx = 0
        self.calls = 0
        self.raises = raises

    @property
    def available(self) -> bool:
        return True

    def scene(self) -> VisionResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        if not self._scenes:
            return VisionResult(detections=())
        out = self._scenes[self._idx % len(self._scenes)]
        self._idx += 1
        return out


class _FakeMission:
    """Stand-in for `MissionPolicy`. Lets tests script the decision."""

    name = "fake"

    def __init__(
        self,
        *,
        decision: Optional[MissionDecision] = None,
        raises: Optional[Exception] = None,
        bad_return: bool = False,
    ) -> None:
        self.decision = decision or MissionDecision(
            next_command=None, args={}, reason="idle", confidence=0.0
        )
        self.raises = raises
        self.bad_return = bad_return
        self.calls: List[dict] = []

    @property
    def available(self) -> bool:
        return True

    def plan(
        self, *, intent: str, scene: VisionResult, world: Any
    ) -> Any:
        self.calls.append({"intent": intent, "scene": scene, "world": world})
        if self.raises is not None:
            raise self.raises
        if self.bad_return:
            return "not a MissionDecision"
        return self.decision


def _cfg(safety: SafetyMode = SafetyMode.BENCH) -> Config:
    return Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_DEVICE_ID": "test-pi",
            "FREEMOTION_SAFETY_DEFAULT": safety.value,
        }
    )


def _build_loop_with_capture_handler(
    *,
    decision: Optional[MissionDecision] = None,
    safety: SafetyMode = SafetyMode.BENCH,
    vision: Optional[_FakeVision] = None,
    mission: Optional[_FakeMission] = None,
    handler_reply_ok: bool = True,
    handler_raises: Optional[Exception] = None,
    tick_interval_s: float = 0.0,
) -> tuple[MissionLoop, list[Command], _FakeVision, _FakeMission, Router, WorldState, Config]:
    """Build a loop + a capturing MOVE handler in one shot."""
    cfg = _cfg(safety)
    vision = vision or _FakeVision()
    mission = mission or _FakeMission(decision=decision)
    world = WorldState()
    router = Router(device_id=cfg.device_id)

    captured: list[Command] = []

    def handler(cmd: Command) -> Reply:
        captured.append(cmd)
        if handler_raises is not None:
            raise handler_raises
        if handler_reply_ok:
            return Reply(
                sender=cfg.device_id,
                state="moving",
                ok=True,
                error=None,
                telemetry={"position": [1.0, 0.0, 0.0]},
                message=f"moved {cmd.args}",
                correlation_id=cmd.correlation_id,
            )
        from freemotion.protocol import Error, ErrorCode

        return Reply(
            sender=cfg.device_id,
            state="error",
            ok=False,
            error=Error(code=ErrorCode.UNSAFE_IN_MODE, message="refused"),
            telemetry={},
            message="refused",
            correlation_id=cmd.correlation_id,
        )

    router.register(CommandName.MOVE, handler)

    loop = MissionLoop(
        vision=vision,
        mission=mission,
        world=world,
        router=router,
        cfg=cfg,
        tick_interval_s=tick_interval_s,
    )
    return loop, captured, vision, mission, router, world, cfg


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float = 2.0,
    poll_s: float = 0.005,
) -> bool:
    """Spin-wait helper. Returns True if the predicate became true in time."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


# ----------------------------------------------------------------------
# Lifecycle: start / stop / state
# ----------------------------------------------------------------------


def test_state_before_start_is_idle() -> None:
    loop, *_ = _build_loop_with_capture_handler()
    state = loop.state()
    assert state["running"] is False
    assert state["intent"] is None
    assert state["tick_count"] == 0


def test_start_returns_true_then_false_when_already_running() -> None:
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(
            next_command=CommandName.MOVE,
            args={"x": 1.0, "y": 0.0, "z": 0.0},
            reason="follow",
            confidence=0.9,
        ),
        tick_interval_s=10.0,  # long interval; lock the loop on its first wait
    )
    try:
        assert loop.start(intent="follow person") is True
        # Wait for the first tick to land so we know the thread is up.
        assert _wait_until(lambda: loop.state()["tick_count"] >= 1)
        assert loop.start(intent="ignored") is False
        # Intent should not have been overwritten by the second start.
        assert loop.intent == "follow person"
    finally:
        loop.stop()


def test_stop_is_idempotent_when_never_started() -> None:
    loop, *_ = _build_loop_with_capture_handler()
    loop.stop()
    loop.stop()
    assert loop.is_running is False


def test_stop_joins_thread_quickly() -> None:
    loop, *_ = _build_loop_with_capture_handler(tick_interval_s=10.0)
    loop.start(intent="test")
    assert _wait_until(lambda: loop.state()["tick_count"] >= 1)
    t0 = time.monotonic()
    loop.stop()
    elapsed = time.monotonic() - t0
    # 2.0s default join_timeout; in practice the stop_event makes this
    # effectively immediate.
    assert elapsed < 1.0
    assert loop.is_running is False


# ----------------------------------------------------------------------
# Happy path: capture -> world -> plan -> dispatch
# ----------------------------------------------------------------------


def test_loop_dispatches_move_when_decision_says_move() -> None:
    loop, captured, vision, mission, _, world, cfg = (
        _build_loop_with_capture_handler(
            decision=MissionDecision(
                next_command=CommandName.MOVE,
                args={"x": 1.0, "y": 0.0, "z": 0.0},
                reason="follow",
                confidence=0.9,
            ),
            tick_interval_s=10.0,
        )
    )
    try:
        loop.start(intent="follow person")
        assert _wait_until(lambda: len(captured) >= 1, timeout_s=2.0)
    finally:
        loop.stop()
    cmd = captured[0]
    assert cmd.cmd == CommandName.MOVE
    assert cmd.args == {"x": 1.0, "y": 0.0, "z": 0.0}
    # ADR-0006: the loop hands the device default safety to the router so
    # the gate / handler safety check uses the floor, not a per-command
    # override.
    assert cmd.safety == cfg.safety_default
    assert cmd.sender == "mission_loop"


def test_loop_updates_world_with_detections() -> None:
    scenes = [
        VisionResult(
            detections=(
                Detection("person", 0.9, (0.1, 0.1, 0.2, 0.4)),
                Detection("dog", 0.4, (0.0, 0.0, 0.3, 0.3)),
            )
        )
    ]
    loop, captured, vision, mission, _, world, _ = (
        _build_loop_with_capture_handler(
            decision=MissionDecision(next_command=None),
            vision=_FakeVision(scenes=scenes),
            tick_interval_s=10.0,
        )
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: world.snapshot().target == "person", timeout_s=2.0
        )
    finally:
        loop.stop()
    snap = world.snapshot()
    # Highest-confidence label wins; dog still gets a `last_seen` entry.
    assert snap.target == "person"
    assert "person" in snap.last_seen
    assert "dog" in snap.last_seen
    assert snap.confidence == pytest.approx(0.9)


def test_loop_records_decision_in_world_next_action() -> None:
    loop, *_, world, _ = _build_loop_with_capture_handler(
        decision=MissionDecision(
            next_command=CommandName.MOVE,
            args={"x": 1.0, "y": 0.0, "z": 0.0},
            reason="follow",
            confidence=0.8,
        ),
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: world.snapshot().next_action == "move", timeout_s=2.0
        )
    finally:
        loop.stop()


def test_loop_passes_intent_scene_world_to_mission() -> None:
    scene = VisionResult(
        detections=(Detection("person", 0.7, (0.1, 0.1, 0.2, 0.4)),)
    )
    mission = _FakeMission(
        decision=MissionDecision(next_command=None, reason="thinking")
    )
    loop, *_ = _build_loop_with_capture_handler(
        vision=_FakeVision(scenes=[scene]),
        mission=mission,
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow person")
        assert _wait_until(lambda: len(mission.calls) >= 1, timeout_s=2.0)
    finally:
        loop.stop()
    call = mission.calls[0]
    assert call["intent"] == "follow person"
    assert call["scene"].detections[0].label == "person"
    # World state should already reflect the most recent see() before the
    # mission was called.
    assert call["world"].target == "person"


# ----------------------------------------------------------------------
# v1 scope: only MOVE is dispatched
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "out_of_scope",
    [
        CommandName.STOP,
        CommandName.ARM,
        CommandName.DISARM,
        CommandName.STATUS,
        CommandName.CAPABILITIES,
        CommandName.PING,
    ],
)
def test_loop_does_not_dispatch_non_move_decisions(
    out_of_scope: CommandName,
) -> None:
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(
            next_command=out_of_scope,
            reason="LLM hallucinated this",
            confidence=1.0,
        ),
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow")
        # Wait for at least one tick to complete.
        assert _wait_until(
            lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    assert captured == []
    state = loop.state()
    assert state["last_decision"]["next_command"] == out_of_scope.value


def test_loop_does_not_dispatch_when_decision_is_idle() -> None:
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(next_command=None, reason="idle"),
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    assert captured == []


# ----------------------------------------------------------------------
# Failure isolation: vision / mission / dispatch
# ----------------------------------------------------------------------


def test_vision_exception_does_not_crash_the_loop() -> None:
    vision = _FakeVision(raises=RuntimeError("camera died"))
    loop, captured, _, mission, *_ = _build_loop_with_capture_handler(
        vision=vision,
        decision=MissionDecision(next_command=None),
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: loop.state()["vision_failures"] >= 1, timeout_s=2.0
        )
        # Mission should still run with an empty scene.
        assert _wait_until(lambda: len(mission.calls) >= 1, timeout_s=2.0)
        assert loop.is_running is True
    finally:
        loop.stop()


def test_mission_exception_does_not_crash_the_loop() -> None:
    mission = _FakeMission(raises=RuntimeError("CUDA OOM"))
    loop, captured, *_ = _build_loop_with_capture_handler(
        mission=mission, tick_interval_s=10.0
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: loop.state()["mission_failures"] >= 1, timeout_s=2.0
        )
        assert loop.is_running is True
    finally:
        loop.stop()
    assert captured == []


def test_mission_non_decision_return_is_treated_as_idle() -> None:
    mission = _FakeMission(bad_return=True)
    loop, captured, *_ = _build_loop_with_capture_handler(
        mission=mission, tick_interval_s=10.0
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: loop.state()["mission_failures"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    # No dispatch because the normalized "decision" has next_command=None.
    assert captured == []


def test_handler_failed_reply_increments_dispatch_failures() -> None:
    """Router returning ok=False (e.g. dry_run refusing move) is a
    real-world signal, not a crash. The loop counts it and keeps going."""
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(
            next_command=CommandName.MOVE,
            args={"x": 1.0, "y": 0.0, "z": 0.0},
            reason="follow",
            confidence=0.9,
        ),
        handler_reply_ok=False,
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(lambda: len(captured) >= 1, timeout_s=2.0)
        assert _wait_until(
            lambda: loop.state()["dispatch_failures"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    state = loop.state()
    assert state["last_dispatched"] == "move"
    assert state["last_dispatch_ok"] is False
    assert "refused" in state["last_dispatch_message"]


def test_handler_exception_does_not_crash_the_loop() -> None:
    """Router catches handler exceptions and returns ok=False with
    INTERNAL. The loop should record the failure and keep going."""
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(
            next_command=CommandName.MOVE,
            args={"x": 1.0, "y": 0.0, "z": 0.0},
            reason="follow",
            confidence=0.9,
        ),
        handler_raises=RuntimeError("handler exploded"),
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow")
        assert _wait_until(lambda: len(captured) >= 1, timeout_s=2.0)
        assert _wait_until(
            lambda: loop.state()["dispatch_failures"] >= 1, timeout_s=2.0
        )
        assert loop.is_running is True
    finally:
        loop.stop()


# ----------------------------------------------------------------------
# /stop interrupts the loop, even mid loop
# ----------------------------------------------------------------------


def test_stop_interrupts_the_loop_even_with_long_tick_interval() -> None:
    """The loop sleeps on the stop event, not `time.sleep`, so `stop()`
    interrupts a long sleep cleanly."""
    loop, *_ = _build_loop_with_capture_handler(
        tick_interval_s=60.0,  # would be a 60s sleep without the event
    )
    loop.start(intent="x")
    assert _wait_until(
        lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0
    )
    t0 = time.monotonic()
    loop.stop()
    elapsed = time.monotonic() - t0
    # If the loop slept on time.sleep(60), this would be ~60s. The
    # stop_event-based sleep returns immediately.
    assert elapsed < 1.0
    assert loop.is_running is False


# ----------------------------------------------------------------------
# state() telemetry contract
# ----------------------------------------------------------------------


def test_state_telemetry_is_complete_after_first_tick() -> None:
    loop, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(
            next_command=CommandName.MOVE,
            args={"x": 1.0, "y": 0.0, "z": 0.0},
            reason="follow",
            confidence=0.85,
        ),
        tick_interval_s=10.0,
    )
    try:
        loop.start(intent="follow person")
        assert _wait_until(
            lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    s = loop.state()
    assert s["running"] is False  # we just stopped it
    assert s["tick_count"] >= 1
    assert s["last_decision"]["next_command"] == "move"
    assert s["last_decision"]["confidence"] == pytest.approx(0.85)
    assert s["last_dispatched"] == "move"
    assert s["last_dispatch_ok"] is True
    assert s["vision_failures"] == 0
    assert s["mission_failures"] == 0
    assert s["dispatch_failures"] == 0
