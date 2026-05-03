"""Mission loop (Step 2 closed loop + Step 3 failure-mode hardening).

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

Failure model (Step 2 + Step 3):

1. `vision.scene()` raises -> log, count
   (`vision_failures`, `consecutive_vision_failures`), treat as
   empty scene.
2. `mission.plan()` raises -> log, count
   (`mission_failures`, `consecutive_mission_failures`), treat as
   idle decision.
3. `router.dispatch()` raises or replies `ok=False` -> log, count
   (`dispatch_failures`, `consecutive_dispatch_failures`).
4. **Stale world (Step 3).** If perception has not produced a
   non-empty scene within `stale_world_timeout_s`, the loop refuses
   to dispatch MOVE for the affected ticks even when mission says
   MOVE — Gemma must not act on a 30-second-old world. Counts as
   `stale_world_skips`. Recovery is automatic: as soon as a
   non-empty scene comes in, MOVE dispatches resume.
5. Decision's `next_command` is anything other than `MOVE` -> the
   loop ignores it. v1 scope per ADR-0010 — the loop only
   dispatches the one bench-safe primitive. ARM / DISARM / STOP /
   STATUS / CAPABILITIES / etc. remain operator-driven via
   Telegram so an LLM hallucination can never arm or disarm the
   device.
6. **Degraded state (Step 3).** When any per-stage consecutive
   counter crosses `degraded_threshold`, the loop transitions to
   `degraded=True` with a human-readable `degraded_reason`. The
   loop keeps ticking — degradation is a visibility signal, not a
   self-stop; the operator decides whether to `/stop`. The state
   automatically clears when the offending stage stops failing.
7. **Hung-tick stop (Step 3).** A `mission.plan()` call that
   blocks longer than `join_timeout_s` cannot be force-killed from
   another thread (Python has no safe primitive for that). `stop()`
   does the right thing anyway: sets the event, waits up to
   `join_timeout_s`, and if the worker is still alive, leaves
   `_thread` set so a subsequent `start()` refuses (no zombie
   thread leak) and the controller-stop callback still drops the
   pins LOW. When the hung tick eventually returns, `start()` reaps
   the now-dead thread and a fresh mission can be launched.
8. The thread itself catches all exceptions at the outer scope; a
   surprise from anywhere will not kill the loop or the agent.

`/stop` halts the loop the same way SIGINT halts the demo: the
caller invokes `mission_loop.stop()`, which sets a flag, joins the
thread, and the next tick exits cleanly. The hardware `stop()` is
called by the operator's `make_stop_handler(on_stop=...)` callback
— typically a composite that stops the loop AND drives both pins
LOW. ADR-0010 + ADR-0011 record why those concerns stay separate.
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
      Step 3: reaps a dead worker thread (e.g. from a hung tick that
      eventually returned) before checking; this is the supported
      restart-after-stop path.
    - `stop()` — sets the stop flag and joins the thread within
      `join_timeout_s`. Idempotent. Safe to call from any thread
      (notably the agent's stop handler, SIGINT, SIGTERM). If the
      thread fails to join within the timeout the worker is hung
      mid-tick — `_thread` is *not* cleared, so a fresh `start()`
      will refuse rather than spawning a second thread; the
      hardware `stop()` is the caller's job and runs unconditionally
      via `make_stop_handler(on_stop=...)`.
    - `is_running` — property.
    - `state()` — telemetry dict for `/status`. Cheap; safe to call any
      time, including while the loop is mid-tick.
    """

    DEFAULT_TICK_INTERVAL_S: float = 1.0
    DEFAULT_THREAD_JOIN_TIMEOUT_S: float = 2.0
    # Step 3: ADR-0011. Five seconds is short enough to refuse acting
    # on stale perception in the bench scenario (operator-scale moves)
    # and long enough to absorb normal Pi-CPU YOLO latency without
    # false positives. Tunable per-instance.
    DEFAULT_STALE_WORLD_TIMEOUT_S: float = 5.0
    # Step 3: ADR-0011. Five consecutive failures of any one stage
    # is enough signal for "something is wrong right now" without
    # being so trigger-happy that one bad tick flips the badge.
    DEFAULT_DEGRADED_THRESHOLD: int = 5

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
        stale_world_timeout_s: float = DEFAULT_STALE_WORLD_TIMEOUT_S,
        degraded_threshold: int = DEFAULT_DEGRADED_THRESHOLD,
    ) -> None:
        self._vision = vision
        self._mission = mission
        self._world = world
        self._router = router
        self._cfg = cfg
        self._sender = sender
        self._tick_interval_s = max(0.0, float(tick_interval_s))
        self._join_timeout_s = max(0.1, float(join_timeout_s))
        self._stale_world_timeout_s = max(0.0, float(stale_world_timeout_s))
        self._degraded_threshold = max(1, int(degraded_threshold))

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

        # Step 3: per-stage consecutive failure counts and degraded state.
        self._consecutive_vision_failures = 0
        self._consecutive_mission_failures = 0
        self._consecutive_dispatch_failures = 0
        self._degraded = False
        self._degraded_reason = ""

        # Step 3: stale-world tracking. `_last_perception_ts` is set to
        # `time.time()` only when a tick produces a non-empty scene.
        # Empty scenes from a healthy camera are valid (the world really
        # is empty), but the mission must not act on them indefinitely.
        self._last_perception_ts: Optional[float] = None
        self._stale_world_skips = 0

    # ------------------------------------------------------------------
    # public surface
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running_locked()

    @property
    def intent(self) -> Optional[str]:
        with self._lock:
            return self._intent

    def start(self, *, intent: str) -> bool:
        """Kick off the background loop. Returns False if already running.

        Re-issuing `start` while running is a no-op rather than an
        error: an operator who fires `/mission_start` twice in a row
        should not have to think about the race.

        Step 3: reaps a dead worker thread before checking. If a
        previous `stop()` left `_thread` set because the worker was
        hung mid-tick, and that worker has since returned naturally,
        this is the path that recovers — no zombie thread, no manual
        intervention. If the worker is *still* alive, `start()`
        refuses (returns False) so we never have two ticking threads.
        """
        with self._lock:
            if self._thread is not None and not self._thread.is_alive():
                # Dead thread left over from a hung-stop scenario that
                # eventually unstuck itself. Safe to reap.
                LOG.info(
                    "mission_loop: reaping dead worker thread before "
                    "starting fresh mission"
                )
                self._thread = None

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
            self._consecutive_vision_failures = 0
            self._consecutive_mission_failures = 0
            self._consecutive_dispatch_failures = 0
            self._degraded = False
            self._degraded_reason = ""
            self._last_perception_ts = None
            self._stale_world_skips = 0

            thread = threading.Thread(
                target=self._run,
                name="freemotion-mission-loop",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            LOG.info(
                "mission_loop started: intent=%r tick_interval=%.2fs "
                "stale_world_timeout=%.2fs degraded_threshold=%d",
                intent,
                self._tick_interval_s,
                self._stale_world_timeout_s,
                self._degraded_threshold,
            )
            return True

    def stop(self) -> None:
        """Signal the loop to stop and join its thread. Idempotent.

        Safe to call from the stop handler (which may itself fire from
        the agent's main thread, the Telegram thread, or a SIGINT
        handler). The hardware `stop()` is the caller's job; this
        method only stops the loop.

        Step 3: if the worker thread fails to join within
        `join_timeout_s`, the tick is hung (almost always inside
        `mission.plan()`). Python provides no safe primitive to
        force-kill a thread, so the loop:

        1. Logs a clear warning.
        2. Leaves `_thread` set so a subsequent `start()` refuses
           rather than spawning a second thread that races a hung
           one.
        3. Clears `_intent` and `_started_at` so `/status` reads as
           "idle" — the loop is not making decisions even though the
           dead-man-switch tick is still in some `transformers`
           `generate()` call.

        The hardware controller is stopped via the demo's composite
        `on_stop` callback regardless. The hung tick will *not* re-
        dispatch MOVE because `_stop_event` is already set; when it
        finally returns it sees the event and exits.
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
            self._intent = None
            self._started_at = None
            if thread.is_alive():
                LOG.warning(
                    "mission_loop.stop(): worker thread did not join "
                    "within %.1fs (likely mission.plan() blocked). The "
                    "controller has been stopped by the caller; the "
                    "thread reference is preserved so /mission_start "
                    "refuses until the worker exits naturally. The "
                    "thread is daemon=True so process exit will reap it.",
                    self._join_timeout_s,
                )
                # Keep self._thread set so start() refuses while the
                # worker is still alive.
            else:
                self._thread = None
        LOG.info("mission_loop stopped")

    def state(self) -> Dict[str, Any]:
        """Telemetry snapshot for `/status`.

        Locks the `_lock` only briefly to copy primitives; never holds
        it across an I/O call.
        """
        now = time.time()
        with self._lock:
            decision = self._last_decision
            dispatched = self._last_dispatched
            running = self._is_running_locked()
            world_age = self._world_age_s_locked(now)
            world_stale = self._is_world_stale_locked(now)
            return {
                "running": running,
                "stop_requested": self._stop_event.is_set(),
                "intent": self._intent,
                "tick_count": self._tick_count,
                "vision_failures": self._vision_failures,
                "mission_failures": self._mission_failures,
                "dispatch_failures": self._dispatch_failures,
                "consecutive_vision_failures": (
                    self._consecutive_vision_failures
                ),
                "consecutive_mission_failures": (
                    self._consecutive_mission_failures
                ),
                "consecutive_dispatch_failures": (
                    self._consecutive_dispatch_failures
                ),
                "stale_world_skips": self._stale_world_skips,
                "degraded": self._degraded,
                "degraded_reason": self._degraded_reason,
                "world_stale": world_stale,
                "world_age_s": (
                    None if world_age is None else round(world_age, 2)
                ),
                "stale_world_timeout_s": self._stale_world_timeout_s,
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
                    int(now - self._started_at)
                    if self._started_at is not None
                    else 0
                ),
            }

    # ------------------------------------------------------------------
    # internal: locked helpers (callers must hold `self._lock`)
    # ------------------------------------------------------------------

    def _is_running_locked(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def _world_age_s_locked(self, now: float) -> Optional[float]:
        """Seconds since the world was last *meaningfully* updated.

        "Meaningfully" = a non-empty scene. Returns `None` only when
        the loop has never started; once started it falls back to
        `now - _started_at` so the timeout is meaningful from boot
        even before the first detection.
        """
        if self._last_perception_ts is not None:
            return now - self._last_perception_ts
        if self._started_at is not None:
            return now - self._started_at
        return None

    def _is_world_stale_locked(self, now: float) -> bool:
        age = self._world_age_s_locked(now)
        if age is None:
            return False  # never started; not stale, just absent
        return age > self._stale_world_timeout_s

    # ------------------------------------------------------------------
    # internal: tick body
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Outer loop. Catches everything; never crashes the thread."""
        try:
            while not self._stop_event.is_set():
                self._tick()
                # Re-check before sleeping so a fast `stop()` skips the
                # next sleep entirely.
                if self._stop_event.is_set():
                    break
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
            self._recompute_degraded()
            return

        # Step 3: stale-world guard. Refuse to dispatch MOVE on an
        # outdated world model. This is the loop-level "do not act on
        # stale perception" check; a fresh non-empty scene from any
        # subsequent tick clears it automatically.
        if self._is_world_stale():
            self._record_stale_world_skip(decision)
            self._recompute_degraded()
            return

        self._dispatch(decision)
        self._recompute_degraded()

    # ------------------------------------------------------------------
    # internal: per-stage failure isolation
    # ------------------------------------------------------------------

    def _safe_scene(self) -> VisionResult:
        try:
            scene = self._vision.scene()
        except Exception as exc:
            with self._lock:
                self._vision_failures += 1
                self._consecutive_vision_failures += 1
            LOG.warning("mission_loop: vision.scene() raised: %s", exc)
            return VisionResult(detections=())

        if not isinstance(scene, VisionResult):
            # Defensive: a vision backend that violates its contract
            # should not crash the loop. Treat as no-detection plus a
            # vision failure for telemetry.
            with self._lock:
                self._vision_failures += 1
                self._consecutive_vision_failures += 1
            LOG.warning(
                "mission_loop: vision.scene() returned non-VisionResult %r",
                type(scene).__name__,
            )
            return VisionResult(detections=())

        with self._lock:
            self._consecutive_vision_failures = 0
            if scene.detections:
                self._last_perception_ts = time.time()
        return scene

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
                self._consecutive_mission_failures += 1
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
                self._consecutive_mission_failures += 1
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
        with self._lock:
            self._consecutive_mission_failures = 0
        return decision

    def _is_world_stale(self) -> bool:
        with self._lock:
            return self._is_world_stale_locked(time.time())

    def _record_stale_world_skip(self, decision: MissionDecision) -> None:
        with self._lock:
            self._stale_world_skips += 1
            age = self._world_age_s_locked(time.time())
        LOG.info(
            "mission_loop: skipped %s args=%s — world stale (%.1fs > %.1fs)",
            decision.next_command.value if decision.next_command else "?",
            dict(decision.args),
            age if age is not None else -1.0,
            self._stale_world_timeout_s,
        )

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
                self._consecutive_dispatch_failures += 1
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
            if reply.ok:
                self._consecutive_dispatch_failures = 0
            else:
                self._dispatch_failures += 1
                self._consecutive_dispatch_failures += 1

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

    # ------------------------------------------------------------------
    # internal: degraded-state recomputation
    # ------------------------------------------------------------------

    def _recompute_degraded(self) -> None:
        """Set or clear `_degraded` based on per-stage consecutive counters.

        Step 3 contract:

        - Crossing the threshold flips degraded ON with a reason
          identifying the stage that's failing right now.
        - Going below the threshold on every stage flips degraded OFF
          automatically; the loop has recovered.
        - Transitions are logged (info level) so the operator's log
          shows the moment the device degraded and the moment it
          recovered. Mid-degraded ticks are *not* logged loud — the
          counters and `degraded_reason` already carry the signal,
          and we don't want to flood the journal during an outage.
        """
        with self._lock:
            t = self._degraded_threshold
            v = self._consecutive_vision_failures
            m = self._consecutive_mission_failures
            d = self._consecutive_dispatch_failures
            reasons = []
            if v >= t:
                reasons.append(f"vision_failures>={t} ({v})")
            if m >= t:
                reasons.append(f"mission_failures>={t} ({m})")
            if d >= t:
                reasons.append(f"dispatch_failures>={t} ({d})")
            new_degraded = bool(reasons)
            new_reason = "; ".join(reasons)
            transitioned_in = (not self._degraded) and new_degraded
            transitioned_out = self._degraded and (not new_degraded)
            self._degraded = new_degraded
            self._degraded_reason = new_reason
        if transitioned_in:
            LOG.warning(
                "mission_loop: degraded ON — %s",
                new_reason or "(no reason)",
            )
        elif transitioned_out:
            LOG.info("mission_loop: degraded cleared (recovered)")
