"""Mission loop (Step 2 — full Pi closed loop).

A background tick loop that wires the existing pieces together:

    PiCameraSource (frame_source on YoloVision)
        -> YoloVision.scene()            -> Detection[]
            -> WorldState.see(label, conf)
                -> WorldStateSnapshot
                    -> MissionPolicy.plan(intent, scene, world)
                        -> MissionDecision (one CommandName + args)
                            -> Router.dispatch(Command)
                                -> SafetyGate -> HardwareController.move()
                                    -> Reply

The loop owns one thread. `start(intent=...)` spins it up; `stop()`
joins it. Telegram commands are still served by the existing agent
on the main thread; the loop dispatches through the same Router so
the deny policy, the SafetyGate, and `make_move_handler`'s safety
checks all still apply. **No path in this module bypasses the
SafetyGate.**

Failure model, in order:

1. `vision.scene()` raises -> log, count, treat as empty scene.
2. `mission.plan()` raises -> log, count, treat as idle decision.
3. `router.dispatch()` raises -> log, count, continue. (`Router`
   already catches handler exceptions and returns `internal`
   replies; this is belt-and-suspenders for any future router
   subclass.)
4. Decision's `next_command` is anything other than `MOVE` -> the
   loop ignores it. v1 scope per ADR-0010 — the loop only
   dispatches the one bench-safe primitive. ARM / DISARM / STOP /
   STATUS / CAPABILITIES / etc. remain operator-driven via
   Telegram so an LLM hallucination can never arm or disarm the
   device.
5. The thread itself catches all exceptions at the outer scope; a
   surprise from anywhere will not kill the loop or the agent.

`/stop` halts the loop the same way SIGINT halts the demo: the
caller invokes `mission_loop.stop()`, which sets a flag, joins the
thread, and the next tick exits cleanly. The hardware `stop()` is
called by the operator's `make_stop_handler(on_stop=...)` callback
— typically a composite that stops the loop AND drives both pins
LOW. ADR-0010 records why those concerns stay separate.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from freemotion.config import Config
from freemotion.mission_control import MissionDecision, MissionPolicy
from freemotion.protocol import (
    Command,
    CommandName,
    SafetyMode,
    new_id,
)
from freemotion.router import Router
from freemotion.vision import VisionBackend, VisionResult
from freemotion.world import WorldState, WorldStateSnapshot

LOG = logging.getLogger("freemotion.agent.mission_loop")

# v1 scope (ADR-0010): the loop dispatches exactly this command. Anything
# else the policy returns is logged and ignored. Operator-driven commands
# (ARM, DISARM, STOP) stay outside the loop on purpose: they must not be
# triggerable by an LLM hallucination.
_LOOP_DISPATCHABLE: frozenset[CommandName] = frozenset({CommandName.MOVE})

# Per-tick we record the top-confidence detections in the world. Keeping
# this small avoids ballooning `last_seen` with every bench/chair the
# YOLO model sees.
_MAX_WORLD_DETECTIONS_PER_TICK = 3


class MissionLoop:
    """Thread-managed perceive -> decide -> act loop.

    Public surface intentionally small:

    - `start(intent=...)` -> bool — kicks off the loop. Returns False
      if a loop is already running (idempotent: re-running `/mission_start`
      while a mission is active won't spawn a second thread).
    - `stop()` — sets the stop flag and joins the thread. Idempotent.
      Safe to call from any thread (notably the agent's stop handler).
    - `is_running` — property.
    - `state()` — telemetry dict for `/status`. Cheap; safe to call any
      time, including while the loop is mid-tick.
    """

    DEFAULT_TICK_INTERVAL_S: float = 1.0
    DEFAULT_THREAD_JOIN_TIMEOUT_S: float = 2.0

    def __init__(
        self,
        *,
        vision: VisionBackend,
        mission: MissionPolicy,
        world: WorldState,
        router: Router,
        cfg: Config,
        tick_interval_s: float = DEFAULT_TICK_INTERVAL_S,
        sender: str = "mission_loop",
        join_timeout_s: float = DEFAULT_THREAD_JOIN_TIMEOUT_S,
    ) -> None:
        self._vision = vision
        self._mission = mission
        self._world = world
        self._router = router
        self._cfg = cfg
        self._sender = sender
        self._tick_interval_s = max(0.0, float(tick_interval_s))
        self._join_timeout_s = max(0.1, float(join_timeout_s))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._intent: Optional[str] = None
        self._tick_count = 0
        self._last_decision: Optional[MissionDecision] = None
        self._last_dispatched: Optional[CommandName] = None
        self._last_dispatch_ok: Optional[bool] = None
        self._last_dispatch_message: str = ""
        self._vision_failures = 0
        self._mission_failures = 0
        self._dispatch_failures = 0
        self._started_at: Optional[float] = None

    # ------------------------------------------------------------------
    # public surface
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        with self._lock:
            return (
                self._thread is not None
                and self._thread.is_alive()
                and not self._stop_event.is_set()
            )

    @property
    def intent(self) -> Optional[str]:
        with self._lock:
            return self._intent

    def start(self, *, intent: str) -> bool:
        """Kick off the background loop. Returns False if already running.

        Re-issuing `start` while running is a no-op rather than an
        error: an operator who fires `/mission_start` twice in a row
        should not have to think about the race.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                LOG.info(
                    "mission_loop already running with intent=%r; "
                    "ignoring start(intent=%r)",
                    self._intent,
                    intent,
                )
                return False
            self._stop_event.clear()
            self._intent = intent
            self._started_at = time.time()
            self._tick_count = 0
            self._last_decision = None
            self._last_dispatched = None
            self._last_dispatch_ok = None
            self._last_dispatch_message = ""
            self._vision_failures = 0
            self._mission_failures = 0
            self._dispatch_failures = 0

            thread = threading.Thread(
                target=self._run,
                name="freemotion-mission-loop",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            LOG.info(
                "mission_loop started: intent=%r tick_interval=%.2fs",
                intent,
                self._tick_interval_s,
            )
            return True

    def stop(self) -> None:
        """Signal the loop to stop and join its thread. Idempotent.

        Safe to call from the stop handler (which may itself fire from
        the agent's main thread, the Telegram thread, or a SIGINT
        handler). The hardware `stop()` is the caller's job; this
        method only stops the loop.
        """
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()

        # Join outside the lock so a tick currently waiting on the
        # mission policy can't deadlock against `state()` or
        # `is_running` readers.
        try:
            thread.join(timeout=self._join_timeout_s)
        except Exception:  # pragma: no cover - defensive
            pass
        with self._lock:
            self._thread = None
            self._intent = None
            self._started_at = None
        LOG.info("mission_loop stopped")

    def state(self) -> Dict[str, Any]:
        """Telemetry snapshot for `/status`.

        Locks the `_lock` only briefly to copy primitives; never holds
        it across an I/O call.
        """
        with self._lock:
            decision = self._last_decision
            dispatched = self._last_dispatched
            return {
                "running": (
                    self._thread is not None
                    and self._thread.is_alive()
                    and not self._stop_event.is_set()
                ),
                "intent": self._intent,
                "tick_count": self._tick_count,
                "vision_failures": self._vision_failures,
                "mission_failures": self._mission_failures,
                "dispatch_failures": self._dispatch_failures,
                "last_decision": (
                    {
                        "next_command": (
                            decision.next_command.value
                            if decision is not None
                            and decision.next_command is not None
                            else None
                        ),
                        "reason": decision.reason if decision else "",
                        "confidence": (
                            decision.confidence if decision else 0.0
                        ),
                    }
                    if decision is not None
                    else None
                ),
                "last_dispatched": (
                    dispatched.value if dispatched is not None else None
                ),
                "last_dispatch_ok": self._last_dispatch_ok,
                "last_dispatch_message": self._last_dispatch_message,
                "started_at": self._started_at,
                "uptime_s": (
                    int(time.time() - self._started_at)
                    if self._started_at is not None
                    else 0
                ),
            }

    # ------------------------------------------------------------------
    # internal: tick body
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Outer loop. Catches everything; never crashes the thread."""
        try:
            while not self._stop_event.is_set():
                self._tick()
                # Use the stop event as the sleep so `stop()` interrupts
                # mid-tick rather than waiting out the full interval.
                if self._tick_interval_s > 0.0:
                    self._stop_event.wait(self._tick_interval_s)
        except Exception as exc:  # pragma: no cover - belt-and-suspenders
            LOG.warning(
                "mission_loop thread died unexpectedly: %s", exc, exc_info=True
            )

    def _tick(self) -> None:
        intent = self.intent or ""

        scene = self._safe_scene()
        self._update_world(scene)

        snapshot = self._world.snapshot()
        decision = self._safe_plan(intent, scene, snapshot)

        # Record the decision unconditionally, even if we don't
        # dispatch it — it's useful telemetry ("the model wanted to
        # ARM, we refused").
        with self._lock:
            self._last_decision = decision
            self._tick_count += 1
            self._world.update(
                next_action=(
                    decision.next_command.value
                    if decision.next_command is not None
                    else None
                )
            )

        if (
            decision.next_command is None
            or decision.next_command not in _LOOP_DISPATCHABLE
        ):
            if decision.next_command is not None:
                LOG.info(
                    "mission_loop: ignoring out-of-scope next_command=%r "
                    "(only %s is dispatched from the loop)",
                    decision.next_command.value,
                    sorted(c.value for c in _LOOP_DISPATCHABLE),
                )
            return

        self._dispatch(decision)

    # ------------------------------------------------------------------
    # internal: per-stage failure isolation
    # ------------------------------------------------------------------

    def _safe_scene(self) -> VisionResult:
        try:
            return self._vision.scene()
        except Exception as exc:
            with self._lock:
                self._vision_failures += 1
            LOG.warning("mission_loop: vision.scene() raised: %s", exc)
            return VisionResult(detections=())

    def _update_world(self, scene: VisionResult) -> None:
        if not scene.detections:
            return
        # `world.see(label)` overwrites `target` on every call (that's
        # the semantic from M3 — see() is "I just saw this and it's
        # what I'm tracking now"). Process the top-N detections in
        # ascending confidence order so the **highest-confidence**
        # detection is the last `see()` call and therefore wins as
        # `target`. `last_seen` accumulates regardless of order.
        ordered = sorted(
            scene.detections,
            key=lambda d: float(d.confidence),
            reverse=True,
        )[:_MAX_WORLD_DETECTIONS_PER_TICK]
        for det in reversed(ordered):
            try:
                self._world.see(det.label, confidence=float(det.confidence))
            except Exception as exc:
                LOG.warning(
                    "mission_loop: world.see(%r) raised: %s", det.label, exc
                )

    def _safe_plan(
        self,
        intent: str,
        scene: VisionResult,
        world: WorldStateSnapshot,
    ) -> MissionDecision:
        try:
            decision = self._mission.plan(
                intent=intent, scene=scene, world=world
            )
        except Exception as exc:
            with self._lock:
                self._mission_failures += 1
            LOG.warning(
                "mission_loop: mission.plan() raised: %s", exc, exc_info=True
            )
            return MissionDecision(
                next_command=None,
                args={},
                reason=f"mission.plan raised: {exc}",
                confidence=0.0,
            )
        if not isinstance(decision, MissionDecision):
            with self._lock:
                self._mission_failures += 1
            LOG.warning(
                "mission_loop: mission.plan() returned non-MissionDecision %r",
                decision,
            )
            return MissionDecision(
                next_command=None,
                args={},
                reason="mission.plan returned a non-MissionDecision",
                confidence=0.0,
            )
        return decision

    def _dispatch(self, decision: MissionDecision) -> None:
        # Per ADR-0006, the SafetyGate decides whether actuation
        # happens — the loop simply hands the command to the router
        # with the device's configured safety_default. The handler
        # already refuses MOVE in dry_run, so no action sneaks
        # through.
        cmd = Command(
            cmd=decision.next_command,  # already filtered to MOVE above
            sender=self._sender,
            args=dict(decision.args),
            safety=self._cfg.safety_default,
            correlation_id=new_id(),
        )
        try:
            reply = self._router.dispatch(cmd)
        except Exception as exc:
            with self._lock:
                self._dispatch_failures += 1
                self._last_dispatched = decision.next_command
                self._last_dispatch_ok = False
                self._last_dispatch_message = f"dispatch raised: {exc}"
            LOG.warning(
                "mission_loop: router.dispatch raised: %s", exc, exc_info=True
            )
            return

        with self._lock:
            self._last_dispatched = decision.next_command
            self._last_dispatch_ok = bool(reply.ok)
            self._last_dispatch_message = reply.message or (
                reply.error.message if reply.error is not None else ""
            )
            if not reply.ok:
                self._dispatch_failures += 1

        if reply.ok:
            LOG.info(
                "mission_loop: dispatched %s args=%s -> %s",
                decision.next_command.value,
                dict(decision.args),
                reply.message,
            )
        else:
            err_code = reply.error.code.value if reply.error else "unknown"
            LOG.info(
                "mission_loop: dispatch refused %s args=%s -> %s (%s)",
                decision.next_command.value,
                dict(decision.args),
                reply.message,
                err_code,
            )
