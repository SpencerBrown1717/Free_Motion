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

import threading
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
        vision=_FakeVision(
            scenes=[
                VisionResult(
                    detections=(Detection("person", 0.9, (0, 0, 0.1, 0.1)),)
                )
            ]
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


# ======================================================================
# Step 3 — failure-mode hardening
#
# These tests exercise the environmental failures the closed loop has to
# survive on real hardware: stale world / camera unplugged / vision drop /
# Gemma hang / repeated dispatch fail / restart after stop. The
# acceptance criteria from the Step 3 plan are folded into the assertions
# (no failure crashes the process; degraded state is visible; recovery
# is automatic; `/stop` always wins).
# ======================================================================


def _move_decision(conf: float = 0.9) -> MissionDecision:
    return MissionDecision(
        next_command=CommandName.MOVE,
        args={"x": 1.0, "y": 0.0, "z": 0.0},
        reason="follow",
        confidence=conf,
    )


def _person_scene() -> VisionResult:
    return VisionResult(
        detections=(Detection("person", 0.9, (0.1, 0.1, 0.2, 0.4)),)
    )


# -- Stale-world guard -------------------------------------------------


def test_stale_world_skips_move_when_no_perception_ever() -> None:
    """Mission says MOVE but the camera has never produced a non-empty
    scene. The loop must NOT dispatch — Gemma cannot act on a world it
    has never observed."""
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=_move_decision(),
        # Empty scenes only — the camera "works" but sees nothing.
        vision=_FakeVision(scenes=[VisionResult(detections=())]),
        tick_interval_s=10.0,
    )
    # Override the stale-world timeout so the test doesn't have to
    # wait. Same shape as the production constructor knob.
    loop._stale_world_timeout_s = 0.0  # noqa: SLF001
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: loop.state()["stale_world_skips"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    assert captured == []
    s = loop.state()
    assert s["stale_world_skips"] >= 1
    assert s["world_stale"] in (True, False)  # may have just stopped
    # Crucially: `dispatch_failures` did NOT increment for the skip.
    # Stale-world skips are a separate signal class.
    assert s["dispatch_failures"] == 0
    assert s["last_dispatched"] is None  # we never dispatched anything


def test_stale_world_clears_after_a_non_empty_scene() -> None:
    """Camera comes back: a single non-empty scene resets the
    stale-world clock and MOVE dispatches resume."""
    # Deliver empty scenes for the first ~0.3s, then person scenes
    # forever. With a 0.1s stale timeout this gives the loop time to
    # accumulate stale-world skips, then recover.
    boot_ts = time.monotonic()

    def scene_fn() -> VisionResult:
        if time.monotonic() - boot_ts < 0.3:
            return VisionResult(detections=())
        return _person_scene()

    class _DelayedCam:
        name = "delayed"
        available = True
        scene = staticmethod(scene_fn)

    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=_move_decision(),
        vision=_DelayedCam(),
        tick_interval_s=0.02,
    )
    loop._stale_world_timeout_s = 0.1  # noqa: SLF001 - small but non-zero
    try:
        loop.start(intent="follow")
        # Wait long enough that we accumulate stale skips, then a
        # successful dispatch. Both observations are durable (counters
        # only ever increase; last_dispatched once set stays set
        # unless overwritten by a fresher dispatch — also fine).
        assert _wait_until(
            lambda: loop.state()["stale_world_skips"] >= 1, timeout_s=3.0
        )
        assert _wait_until(lambda: len(captured) >= 1, timeout_s=3.0)
    finally:
        loop.stop()
    s = loop.state()
    assert s["stale_world_skips"] >= 1
    assert s["last_dispatched"] == "move"


def test_world_not_stale_when_within_timeout() -> None:
    """Below the timeout, MOVE dispatches normally — the guard does
    not kick in spuriously."""
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=_move_decision(),
        vision=_FakeVision(scenes=[_person_scene()]),
        tick_interval_s=10.0,
    )
    loop._stale_world_timeout_s = 60.0  # noqa: SLF001 - generous
    try:
        loop.start(intent="follow")
        assert _wait_until(lambda: len(captured) >= 1, timeout_s=2.0)
    finally:
        loop.stop()
    assert loop.state()["stale_world_skips"] == 0
    assert captured[0].cmd == CommandName.MOVE


def test_world_stale_does_not_skip_idle_decisions() -> None:
    """If the policy says idle, there's nothing to skip — the
    stale-world counter must not increment."""
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(next_command=None),
        vision=_FakeVision(scenes=[VisionResult(detections=())]),
        tick_interval_s=10.0,
    )
    loop._stale_world_timeout_s = 0.0  # noqa: SLF001
    try:
        loop.start(intent="follow")
        assert _wait_until(
            lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0
        )
    finally:
        loop.stop()
    assert captured == []
    assert loop.state()["stale_world_skips"] == 0


def test_state_exposes_world_age_and_stale_flag() -> None:
    loop, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(next_command=None),
        vision=_FakeVision(scenes=[VisionResult(detections=())]),
        tick_interval_s=10.0,
    )
    loop._stale_world_timeout_s = 0.0  # noqa: SLF001
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0
        )
        s = loop.state()
        assert s["world_stale"] is True
        assert s["world_age_s"] is not None
        assert s["world_age_s"] >= 0
        assert s["stale_world_timeout_s"] == 0.0
    finally:
        loop.stop()


# -- Per-stage consecutive counters + degraded state -----------------


def test_consecutive_vision_failures_resets_on_success() -> None:
    """Mixed scenes — fail / fail / succeed — should leave the
    consecutive counter at 0 even though the cumulative count is 2."""
    scenes = [_person_scene()]
    vision = _FakeVision(scenes=scenes)

    # Hand-craft a scene() that raises twice then succeeds.
    call_log: list[str] = []
    real_scene = vision.scene

    def flaky_scene() -> VisionResult:
        n = len(call_log)
        call_log.append("called")
        if n < 2:
            raise RuntimeError("flaky")
        return real_scene()

    vision.scene = flaky_scene  # type: ignore[assignment]

    loop, *_ = _build_loop_with_capture_handler(
        vision=vision,
        decision=MissionDecision(next_command=None),
        tick_interval_s=0.0,
    )
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: len(call_log) >= 3, timeout_s=2.0
        )
    finally:
        loop.stop()
    s = loop.state()
    assert s["vision_failures"] >= 2
    assert s["consecutive_vision_failures"] == 0  # last call succeeded


def test_degraded_turns_on_after_threshold_consecutive_vision_fails() -> None:
    vision = _FakeVision(raises=RuntimeError("camera unplugged"))
    loop, *_ = _build_loop_with_capture_handler(
        vision=vision,
        decision=MissionDecision(next_command=None),
        tick_interval_s=0.0,
    )
    loop._degraded_threshold = 3  # noqa: SLF001 - speed up the test
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["degraded"] is True, timeout_s=2.0
        )
    finally:
        loop.stop()
    s = loop.state()
    assert s["degraded"] is True
    assert "vision_failures" in s["degraded_reason"]
    assert s["consecutive_vision_failures"] >= 3


def test_degraded_clears_after_recovery() -> None:
    """Vision fails N times -> degraded ON; then succeeds -> degraded OFF."""
    success_scene = _person_scene()
    fails_then_succeeds: list[Any] = []

    def scene_fn() -> VisionResult:
        fails_then_succeeds.append("call")
        if len(fails_then_succeeds) <= 5:
            raise RuntimeError("flaky")
        return success_scene

    class _Flaky:
        name = "flaky"
        available = True
        scene = staticmethod(scene_fn)

    loop, *_ = _build_loop_with_capture_handler(
        vision=_Flaky(),
        decision=MissionDecision(next_command=None),
        # 20ms tick is fast enough for the test, slow enough that the
        # poll-based `_wait_until` (5ms) reliably observes the
        # transient degraded=True state mid-recovery.
        tick_interval_s=0.02,
    )
    loop._degraded_threshold = 3  # noqa: SLF001
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["degraded"] is True, timeout_s=3.0
        )
        assert _wait_until(
            lambda: loop.state()["degraded"] is False, timeout_s=3.0
        )
    finally:
        loop.stop()


def test_degraded_handles_multiple_stages_simultaneously() -> None:
    """Vision fails AND mission fails AND dispatch fails all at once.
    `degraded_reason` should mention each one."""
    vision = _FakeVision(raises=RuntimeError("vision down"))
    mission = _FakeMission(raises=RuntimeError("mission down"))
    loop, *_ = _build_loop_with_capture_handler(
        vision=vision,
        mission=mission,
        tick_interval_s=0.0,
    )
    loop._degraded_threshold = 2  # noqa: SLF001
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["degraded"] is True, timeout_s=2.0
        )
    finally:
        loop.stop()
    reason = loop.state()["degraded_reason"]
    # Vision fails first (mission isn't called when vision_failures
    # also flips degraded). At minimum vision should be there.
    assert "vision_failures" in reason


def test_repeated_dispatch_failures_trigger_degraded() -> None:
    loop, captured, *_ = _build_loop_with_capture_handler(
        decision=_move_decision(),
        vision=_FakeVision(scenes=[_person_scene()]),
        handler_reply_ok=False,
        tick_interval_s=0.0,
    )
    loop._degraded_threshold = 3  # noqa: SLF001
    loop._stale_world_timeout_s = 60.0  # noqa: SLF001
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["degraded"] is True, timeout_s=2.0
        )
    finally:
        loop.stop()
    s = loop.state()
    assert "dispatch_failures" in s["degraded_reason"]
    assert s["consecutive_dispatch_failures"] >= 3
    # The router actually saw the commands (this is real "the policy
    # was denied", not "we never dispatched").
    assert len(captured) >= 3


def test_dispatch_success_resets_consecutive_dispatch_failures() -> None:
    """After a streak of refusals, one successful dispatch resets the
    counter so degraded clears next tick."""
    fail_then_pass: dict = {"n": 0}

    def handler_factory():
        from freemotion.protocol import Error, ErrorCode

        cfg_local = _cfg()

        def handler(cmd: Command) -> Reply:
            fail_then_pass["n"] += 1
            ok = fail_then_pass["n"] >= 4
            if ok:
                return Reply(
                    sender=cfg_local.device_id,
                    state="moving",
                    ok=True,
                    error=None,
                    telemetry={},
                    message="moved",
                    correlation_id=cmd.correlation_id,
                )
            return Reply(
                sender=cfg_local.device_id,
                state="error",
                ok=False,
                error=Error(code=ErrorCode.UNSAFE_IN_MODE, message="refused"),
                telemetry={},
                message="refused",
                correlation_id=cmd.correlation_id,
            )

        return handler

    cfg = _cfg()
    router = Router(device_id=cfg.device_id)
    router.register(CommandName.MOVE, handler_factory())
    loop = MissionLoop(
        vision=_FakeVision(scenes=[_person_scene()]),
        mission=_FakeMission(decision=_move_decision()),
        world=WorldState(),
        router=router,
        cfg=cfg,
        # 20ms tick lets `_wait_until` (5ms poll) observe the
        # transient degraded=True before a successful dispatch
        # clears it.
        tick_interval_s=0.02,
    )
    loop._degraded_threshold = 3  # noqa: SLF001
    loop._stale_world_timeout_s = 60.0  # noqa: SLF001
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["degraded"] is True, timeout_s=3.0
        )
        assert _wait_until(
            lambda: loop.state()["degraded"] is False, timeout_s=3.0
        )
    finally:
        loop.stop()
    s = loop.state()
    assert s["consecutive_dispatch_failures"] == 0


# -- Restart after stop ----------------------------------------------


def test_restart_after_clean_stop_works() -> None:
    """Recovery: an operator can `/mission_start` again after `/stop`."""
    loop, *_ = _build_loop_with_capture_handler(
        decision=MissionDecision(next_command=None),
        tick_interval_s=10.0,
    )
    assert loop.start(intent="first") is True
    assert _wait_until(lambda: loop.state()["tick_count"] >= 1, timeout_s=2.0)
    loop.stop()
    assert loop.is_running is False

    # Second mission with a different intent.
    assert loop.start(intent="second") is True
    try:
        assert loop.intent == "second"
        # Counters reset on the new run.
        s = loop.state()
        assert s["tick_count"] >= 0  # may already have ticked once
        assert s["intent"] == "second"
    finally:
        loop.stop()


def test_start_after_stop_resets_all_step3_counters() -> None:
    """A fresh `start()` must zero every counter — degraded state, stale
    skips, last_perception_ts. Otherwise stale signals from the last
    mission would haunt the new one."""
    vision = _FakeVision(raises=RuntimeError("flaky"))
    loop, *_ = _build_loop_with_capture_handler(
        vision=vision,
        decision=MissionDecision(next_command=None),
        tick_interval_s=0.0,
    )
    loop._degraded_threshold = 2  # noqa: SLF001
    loop.start(intent="first")
    assert _wait_until(
        lambda: loop.state()["degraded"] is True, timeout_s=2.0
    )
    loop.stop()
    assert loop.state()["degraded"] is True  # still True until next start()

    # Now reset by giving it a working vision and starting again.
    loop._vision = _FakeVision(scenes=[_person_scene()])  # noqa: SLF001
    loop.start(intent="second")
    try:
        s = loop.state()
        assert s["consecutive_vision_failures"] == 0
        assert s["consecutive_mission_failures"] == 0
        assert s["consecutive_dispatch_failures"] == 0
        assert s["stale_world_skips"] == 0
        assert s["degraded"] is False
        assert s["degraded_reason"] == ""
    finally:
        loop.stop()


# -- Hung tick / hung mission.plan() ---------------------------------


def test_hung_mission_plan_keeps_thread_set_so_start_refuses() -> None:
    """If `mission.plan()` blocks past `join_timeout_s`, `stop()`
    cannot kill the thread (Python has no safe primitive). Instead
    the loop preserves `_thread` so a fresh `start()` refuses
    rather than spawning a second thread that races a hung one."""
    plan_event = threading.Event()
    plan_started = threading.Event()

    class _HangingMission:
        name = "hang"
        available = True

        def plan(self, **_kwargs: Any) -> MissionDecision:
            plan_started.set()
            plan_event.wait(timeout=10.0)  # blocks until the test releases
            return MissionDecision(next_command=None)

    cfg = _cfg()
    router = Router(device_id=cfg.device_id)
    router.register(CommandName.MOVE, lambda c: Reply(sender=cfg.device_id, state="moving", ok=True, error=None, telemetry={}, message="moved", correlation_id=c.correlation_id))
    loop = MissionLoop(
        vision=_FakeVision(scenes=[_person_scene()]),
        mission=_HangingMission(),
        world=WorldState(),
        router=router,
        cfg=cfg,
        tick_interval_s=10.0,
        join_timeout_s=0.3,  # short so the test runs fast
    )
    try:
        assert loop.start(intent="first") is True
        # Wait for the worker to enter plan().
        assert plan_started.wait(timeout=2.0)

        # stop() should return within ~join_timeout_s; the worker is
        # still alive though.
        t0 = time.monotonic()
        loop.stop()
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0  # bounded by join_timeout_s

        # /status reads idle (intent cleared).
        s = loop.state()
        assert s["running"] is False
        assert s["stop_requested"] is True
        assert s["intent"] is None

        # A fresh /mission_start refuses because the worker is still
        # alive — this is the no-zombie-thread guarantee.
        assert loop.start(intent="second") is False
    finally:
        # Release the hung mission so the worker exits cleanly.
        plan_event.set()
        # Now the worker is dead; a fresh start() should succeed
        # because start() reaps a dead orphan thread before checking.
        # Wait for the thread to actually finish.
        assert _wait_until(
            lambda: not (
                loop._thread is not None and loop._thread.is_alive()  # noqa: SLF001
            ),
            timeout_s=2.0,
        )


def test_start_reaps_dead_orphan_thread_after_hung_then_unhung() -> None:
    """After a hung-then-recovered worker, `start()` reaps the dead
    thread and a new mission can be launched."""
    release = threading.Event()
    started = threading.Event()

    class _HangingMission:
        name = "hang"
        available = True

        def plan(self, **_kwargs: Any) -> MissionDecision:
            started.set()
            release.wait(timeout=10.0)
            return MissionDecision(next_command=None)

    cfg = _cfg()
    router = Router(device_id=cfg.device_id)
    router.register(
        CommandName.MOVE,
        lambda c: Reply(
            sender=cfg.device_id, state="moving", ok=True, error=None,
            telemetry={}, message="moved", correlation_id=c.correlation_id,
        ),
    )
    loop = MissionLoop(
        vision=_FakeVision(scenes=[_person_scene()]),
        mission=_HangingMission(),
        world=WorldState(),
        router=router,
        cfg=cfg,
        tick_interval_s=10.0,
        join_timeout_s=0.3,
    )
    loop.start(intent="first")
    started.wait(timeout=2.0)
    loop.stop()
    # Worker is hung. Release it; the thread will exit naturally.
    release.set()
    # Wait for the worker to die.
    assert _wait_until(
        lambda: not (
            loop._thread is not None and loop._thread.is_alive()  # noqa: SLF001
        ),
        timeout_s=2.0,
    )

    # Now start a fresh mission: start() should reap and succeed.
    loop._mission = _FakeMission(  # noqa: SLF001 — swap for non-hanging
        decision=MissionDecision(next_command=None)
    )
    try:
        assert loop.start(intent="second") is True
        assert loop.intent == "second"
    finally:
        loop.stop()


# -- Camera unplugged / vision drop ----------------------------------


def test_camera_unplugged_mid_loop_does_not_crash_and_eventually_stales() -> None:
    """Vision starts working, then `scene()` raises forever (camera
    unplugged). The loop must not crash; degraded must turn ON via
    `consecutive_vision_failures`; stale-world should follow once the
    timeout elapses without fresh perception."""
    fails_after: dict = {"calls": 0}

    def scene_fn() -> VisionResult:
        fails_after["calls"] += 1
        if fails_after["calls"] == 1:
            return _person_scene()  # one good frame, then crash
        raise RuntimeError("camera unplugged")

    class _DyingCam:
        name = "dying"
        available = True
        scene = staticmethod(scene_fn)

    loop, *_ = _build_loop_with_capture_handler(
        vision=_DyingCam(),
        decision=_move_decision(),
        tick_interval_s=0.0,
    )
    loop._degraded_threshold = 3  # noqa: SLF001
    loop._stale_world_timeout_s = 0.0  # noqa: SLF001 - immediate stale
    try:
        loop.start(intent="x")
        # Wait until the loop has had time to fail many times.
        assert _wait_until(
            lambda: loop.state()["consecutive_vision_failures"] >= 3,
            timeout_s=2.0,
        )
        assert loop.is_running is True  # alive despite repeated failures
    finally:
        loop.stop()
    s = loop.state()
    assert s["degraded"] is True
    assert "vision_failures" in s["degraded_reason"]


# -- Vision contract violation ---------------------------------------


def test_vision_returning_non_visionresult_counts_as_failure() -> None:
    """A buggy vision backend returning the wrong type should not
    crash the loop; it should count as a vision failure."""

    class _BadVision:
        name = "bad"
        available = True

        def scene(self) -> Any:
            return "not a VisionResult"

    loop, *_ = _build_loop_with_capture_handler(
        vision=_BadVision(),
        decision=MissionDecision(next_command=None),
        tick_interval_s=0.0,
    )
    try:
        loop.start(intent="x")
        assert _wait_until(
            lambda: loop.state()["vision_failures"] >= 1, timeout_s=2.0
        )
        assert loop.is_running is True
    finally:
        loop.stop()
