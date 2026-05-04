"""Pi follow-bench benchmark — runner core (Step 5).

The named, repeatable Pi benchmark. Drives the locked Pi reference
architecture (`docs/pi-reference.md`) through a fixed 10-step command
sequence, applies fixed pass/fail criteria, and emits a stable JSON
artifact (schema v1) that operators and CI can compare across runs.

Two design constraints make this module the right size:

1. **Direct router dispatch, not Telegram.** The benchmark is a
   reproducible test of the locked contract — wall-clock-coupled
   round-trips through Telegram add latency, retry behavior, and a
   network dependency that none of the criteria here are about.
   The router is the same path Telegram drives, so dispatching
   `Command` objects through it exercises the safety floor, the
   deny list, the SafetyGate, the controller, and the mission loop
   exactly the same way.

2. **No new safety primitives.** The benchmark composes
   `pi_closed_loop_demo.build_router_without_loop` and
   `pi_closed_loop_demo.attach_mission_loop` to wire the same
   stack the canonical demo wires. Everything safety-relevant
   (loop-only-MOVE, stale-world refusal, hung-tick handling,
   `/stop` ordering) is inherited from the closed-loop demo and
   verified by reading `state()` after each step.

The 10-step sequence is the **frozen benchmark protocol** (see
`docs/pi-benchmark.md`). It is intentionally deterministic:

    1. /ping                          round-trip liveness
    2. /capabilities                  verify the locked 8-command surface
    3. /status                        initial telemetry snapshot
    4. /arm                           drive armed_pin HIGH
    5. /mission_start <intent>        start the background loop
    6. observe (sleep hold_s)         let the loop tick
    7. /status                        mid-mission telemetry snapshot
    8. /stop                          master kill
    9. /disarm                        explicit disarm (idempotent after /stop)
   10. /status                        final telemetry snapshot

Failure injection (`inject="camera_offline"|"mission_offline"|
"vision_drop_after_n"`) does **not** change the sequence — the
benchmark always runs all 10 steps. What changes is the *expected
outcome*: under injection, the universal safety contracts must still
hold (no crash, /stop returns ok, pins LOW at end, loop reads idle
after stop, capabilities match locked surface) but the loop is
allowed (and expected) to record vision/mission failures or to skip
MOVEs because the world is stale.

The benchmark never sees `Config.from_env`. Callers (the CLI;
tests) construct the `Config`, the controller, the vision, the
mission, the world, the router, and the mission loop, then hand
everything to `run_benchmark(...)`. Same seam `pi_closed_loop_demo`
uses for `attach_mission_loop`. Nothing here imports `os.environ`.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from freemotion.config import Config
from freemotion.hardware import HardwareController
from freemotion.protocol import (
    Command,
    CommandName,
    Reply,
    now_iso,
)
from freemotion.router import Router

# -----------------------------------------------------------------------------
# Frozen benchmark protocol — see docs/pi-benchmark.md.
# -----------------------------------------------------------------------------

#: Schema version for the JSON artifact. Bump only on a breaking change to
#: the artifact shape. New fields are additive and do not bump.
SCHEMA_VERSION: int = 1

#: The locked Pi reference command surface (Step 4, ADR-0012). The benchmark
#: refuses to pass when `/capabilities` reports a different set.
LOCKED_PI_SURFACE: Tuple[str, ...] = (
    "arm",
    "capabilities",
    "disarm",
    "mission_start",
    "move",
    "ping",
    "status",
    "stop",
)

#: Default observation window between `/mission_start` and the mid-mission
#: `/status`. Long enough to absorb several ticks at the default
#: `tick_interval_s=1.0`; short enough to keep a benchmark run < 30s.
DEFAULT_HOLD_S: float = 5.0

#: Minimum mission-loop ticks observed in the mid-mission `/status` for the
#: benchmark to count `loop_reached_running` as truly successful. Default 1
#: keeps the threshold honest on slow CPUs while still requiring real work.
DEFAULT_MIN_LOOP_TICKS: int = 1

#: Inject names. `None` is the clean (no-injection) run.
INJECT_CAMERA_OFFLINE: str = "camera_offline"
INJECT_MISSION_OFFLINE: str = "mission_offline"
INJECT_VISION_DROP_AFTER_N: str = "vision_drop_after_n"
KNOWN_INJECTS: Tuple[str, ...] = (
    INJECT_CAMERA_OFFLINE,
    INJECT_MISSION_OFFLINE,
    INJECT_VISION_DROP_AFTER_N,
)


# -----------------------------------------------------------------------------
# Result dataclasses — the artifact shape is the public contract.
# -----------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BenchmarkStep:
    """One row in the benchmark's command sequence."""

    step: int
    name: str
    """Wire command name for `kind="command"` steps; `"observe"` for the
    sleep step."""
    kind: str
    """`"command"` | `"observe"`."""
    started_at: str
    """ISO 8601 UTC timestamp captured before dispatch / sleep."""
    duration_s: float
    ok: bool
    state: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    message: str
    telemetry_snapshot: Optional[Dict[str, Any]]
    """Filtered subset of `reply.telemetry` carrying the keys the
    criteria depend on (`controller`, `mission_loop`, `capabilities`).
    `None` when the reply has none of those, or for `kind="observe"`
    steps."""


@dataclasses.dataclass(frozen=True)
class BenchmarkCriteria:
    """Pass/fail roll-up for one run."""

    expected_outcome: str
    """`"clean"` for an injection-free run; matches `inject` otherwise."""
    all_commands_ok: bool
    capabilities_match_locked_surface: bool
    loop_reached_running: bool
    min_loop_ticks_required: int
    loop_ticks_observed: int
    loop_ticks_met: bool
    loop_stopped_clean: bool
    pins_low_at_end: bool
    move_dispatches_observed: bool
    """`last_dispatched=="move"` and `last_dispatch_ok=True` at step 7.
    `False` is allowed under injection but expected for `expected_outcome="clean"`."""
    min_move_dispatches_required: bool
    """Whether the configured `min_move_dispatches` threshold (0 or 1) was met."""
    no_unexpected_failures: bool
    vision_failures: int
    mission_failures: int
    dispatch_failures: int
    stale_world_skips: int
    notes: List[str]


@dataclasses.dataclass(frozen=True)
class BenchmarkResult:
    """The artifact. Serialized as JSON via `result_to_dict`."""

    schema_version: int
    run_id: str
    started_at: str
    completed_at: str
    duration_s: float
    mode: str
    """`"bench"` | `"ci"` — operator-supplied label, no semantic effect
    in this module. The CLI uses `"bench"` for real-hardware runs and
    `"ci"` for the all-mock harness path."""
    inject: Optional[str]
    intent: str
    hold_s: float
    config_summary: Dict[str, Any]
    command_sequence: List[BenchmarkStep]
    criteria: BenchmarkCriteria
    success: bool


# -----------------------------------------------------------------------------
# The runner.
# -----------------------------------------------------------------------------


def run_benchmark(
    *,
    cfg: Config,
    controller: HardwareController,
    mission_loop: Any,
    router: Router,
    mode: str = "bench",
    inject: Optional[str] = None,
    intent: str = "follow person",
    hold_s: float = DEFAULT_HOLD_S,
    min_loop_ticks: int = DEFAULT_MIN_LOOP_TICKS,
    min_move_dispatches: int = 0,
    sender: str = "pi_follow_bench",
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> BenchmarkResult:
    """Run the 10-step benchmark sequence end-to-end and return a result.

    The caller wires the full stack (controller, vision, mission, world,
    mission_loop, router) — the benchmark does not import factories.
    This keeps the harness (a) usable in CI without picamera2 / RPi.GPIO
    / ultralytics / transformers, and (b) an exact mirror of how
    `pi_closed_loop_demo.main()` wires the same stack.

    `inject` is recorded in the artifact and steers the criteria's
    `expected_outcome` field, but does not change the command sequence
    — the benchmark runs the same 10 steps under any injection.
    """
    if mode not in {"bench", "ci"}:
        raise ValueError(f"unknown mode: {mode!r} (expected 'bench' or 'ci')")
    if inject is not None and inject not in KNOWN_INJECTS:
        raise ValueError(
            f"unknown inject: {inject!r} (expected one of {KNOWN_INJECTS} or None)"
        )
    if hold_s < 0.0:
        raise ValueError(f"hold_s must be non-negative; got {hold_s}")
    if min_loop_ticks < 0:
        raise ValueError(f"min_loop_ticks must be >= 0; got {min_loop_ticks}")
    if min_move_dispatches not in (0, 1):
        raise ValueError(
            f"min_move_dispatches must be 0 or 1; got {min_move_dispatches}"
        )

    started_iso = now_iso()
    run_started_t = monotonic()
    run_id = str(uuid.uuid4())

    sequence: List[BenchmarkStep] = []
    notes: List[str] = []

    def _dispatch(step: int, name: str, args: Optional[Dict[str, Any]] = None) -> BenchmarkStep:
        """Dispatch one command through the router and capture the reply."""
        cmd = Command(
            cmd=CommandName(name),
            sender=sender,
            args=dict(args or {}),
            safety=cfg.safety_default,
        )
        ts_iso = now_iso()
        t0 = monotonic()
        reply: Reply = router.dispatch(cmd)
        d = monotonic() - t0
        return BenchmarkStep(
            step=step,
            name=name,
            kind="command",
            started_at=ts_iso,
            duration_s=round(d, 4),
            ok=bool(reply.ok),
            state=reply.state,
            error_code=(reply.error.code.value if reply.error else None),
            error_message=(reply.error.message if reply.error else None),
            message=str(reply.message or ""),
            telemetry_snapshot=_extract_telemetry(reply.telemetry or {}),
        )

    # 1. /ping
    sequence.append(_dispatch(1, "ping"))

    # 2. /capabilities
    sequence.append(_dispatch(2, "capabilities"))

    # 3. /status (initial)
    sequence.append(_dispatch(3, "status"))

    # 4. /arm
    sequence.append(_dispatch(4, "arm"))

    # 5. /mission_start <intent>
    sequence.append(_dispatch(5, "mission_start", {"intent": intent}))

    # 6. observe (sleep)
    obs_iso = now_iso()
    obs_t0 = monotonic()
    if hold_s > 0:
        try:
            sleeper(hold_s)
        except Exception as exc:  # pragma: no cover - defensive
            notes.append(f"observe sleep raised: {exc}")
    obs_d = monotonic() - obs_t0
    sequence.append(
        BenchmarkStep(
            step=6,
            name="observe",
            kind="observe",
            started_at=obs_iso,
            duration_s=round(obs_d, 4),
            ok=True,
            state=None,
            error_code=None,
            error_message=None,
            message=f"slept {obs_d:.2f}s",
            telemetry_snapshot=None,
        )
    )

    # 7. /status (mid-mission) — the criterion-bearing snapshot
    sequence.append(_dispatch(7, "status"))

    # 8. /stop — must succeed unconditionally
    sequence.append(_dispatch(8, "stop"))

    # 9. /disarm — idempotent after /stop, must still ack
    sequence.append(_dispatch(9, "disarm"))

    # 10. /status (final)
    sequence.append(_dispatch(10, "status"))

    completed_iso = now_iso()
    duration = monotonic() - run_started_t

    criteria = _evaluate_criteria(
        sequence=sequence,
        inject=inject,
        min_loop_ticks=min_loop_ticks,
        min_move_dispatches=min_move_dispatches,
        notes=notes,
    )
    success = _is_successful(criteria)

    result = BenchmarkResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        started_at=started_iso,
        completed_at=completed_iso,
        duration_s=round(duration, 4),
        mode=mode,
        inject=inject,
        intent=intent,
        hold_s=hold_s,
        config_summary=_summarize_config(cfg),
        command_sequence=sequence,
        criteria=criteria,
        success=success,
    )
    return result


# -----------------------------------------------------------------------------
# Internal: telemetry extraction.
# -----------------------------------------------------------------------------


_TELEMETRY_KEYS_OF_INTEREST = ("controller", "mission_loop", "capabilities")


def _extract_telemetry(tel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Filter `reply.telemetry` down to keys the criteria depend on.

    Reduces artifact bloat (the full `controller.state()` is fine, but
    adding device hostname / boot timestamp / etc. for every step makes
    diffs noisy). Returns `None` when no key of interest is present so
    the artifact can omit the field rather than carry an empty dict.
    """
    out: Dict[str, Any] = {}
    for key in _TELEMETRY_KEYS_OF_INTEREST:
        if key in tel:
            out[key] = tel[key]
    return out or None


def _summarize_config(cfg: Config) -> Dict[str, Any]:
    """Strip secrets and noise from `Config` for the artifact."""
    return {
        "device_id": cfg.device_id,
        "hardware_profile": cfg.hardware_profile,
        "safety_default": cfg.safety_default.value,
        "vision_backend": cfg.vision_backend,
        "mission_backend": cfg.mission_backend,
        "denied_commands": sorted(cfg.denied_commands),
        "pi_armed_pin": cfg.pi_armed_pin,
        "pi_moving_pin": cfg.pi_moving_pin,
    }


# -----------------------------------------------------------------------------
# Internal: criteria evaluation.
# -----------------------------------------------------------------------------


def _evaluate_criteria(
    *,
    sequence: List[BenchmarkStep],
    inject: Optional[str],
    min_loop_ticks: int,
    min_move_dispatches: int,
    notes: List[str],
) -> BenchmarkCriteria:
    """Apply the frozen pass/fail rules to a completed sequence."""
    expected_outcome = "clean" if inject is None else inject

    by_step = {s.step: s for s in sequence}
    cap_step = by_step.get(2)
    init_status = by_step.get(3)
    mid_status = by_step.get(7)
    stop_step = by_step.get(8)
    final_status = by_step.get(10)

    # --- universal contract checks (must hold for every mode + every inject) ---

    all_commands_ok = all(
        s.ok for s in sequence if s.kind == "command"
    )

    capabilities_match_locked_surface = _capabilities_match(cap_step)

    loop_reached_running, loop_ticks_observed = _running_and_ticks(mid_status)
    loop_ticks_met = loop_ticks_observed >= min_loop_ticks

    move_dispatches_observed = _last_dispatched_was_successful_move(mid_status)
    min_move_dispatches_required = (
        min_move_dispatches == 0 or move_dispatches_observed
    )

    loop_stopped_clean = _loop_stopped_clean(stop_step, final_status)

    pins_low_at_end = _pins_low_at_end(final_status)

    # Failure counters from the FINAL status — they're cumulative across
    # the loop's lifetime, so the final snapshot is authoritative. Fall
    # back to the mid snapshot if the final reading was somehow malformed.
    fc = _failure_counters(final_status) or _failure_counters(mid_status) or (0, 0, 0)
    vision_failures, mission_failures, dispatch_failures = fc
    stale_world_skips = _stale_world_skips(final_status) or _stale_world_skips(
        mid_status
    ) or 0

    no_unexpected_failures = _no_unexpected_failures(
        inject=inject,
        vision_failures=vision_failures,
        mission_failures=mission_failures,
        dispatch_failures=dispatch_failures,
    )

    return BenchmarkCriteria(
        expected_outcome=expected_outcome,
        all_commands_ok=all_commands_ok,
        capabilities_match_locked_surface=capabilities_match_locked_surface,
        loop_reached_running=loop_reached_running,
        min_loop_ticks_required=min_loop_ticks,
        loop_ticks_observed=loop_ticks_observed,
        loop_ticks_met=loop_ticks_met,
        loop_stopped_clean=loop_stopped_clean,
        pins_low_at_end=pins_low_at_end,
        move_dispatches_observed=move_dispatches_observed,
        min_move_dispatches_required=min_move_dispatches_required,
        no_unexpected_failures=no_unexpected_failures,
        vision_failures=vision_failures,
        mission_failures=mission_failures,
        dispatch_failures=dispatch_failures,
        stale_world_skips=stale_world_skips,
        notes=list(notes),
    )


def _is_successful(c: BenchmarkCriteria) -> bool:
    """Roll up the criteria into one pass/fail bit.

    Universal contracts (every mode):

    - `all_commands_ok` — every dispatch produced `ok=True`.
    - `capabilities_match_locked_surface` — exact match against
      `LOCKED_PI_SURFACE`.
    - `loop_reached_running` AND `loop_ticks_met` — the loop did real
      work during the observation window.
    - `loop_stopped_clean` AND `pins_low_at_end` — `/stop` and
      `/disarm` brought the system to idle.
    - `no_unexpected_failures` — failure counters are within the
      mode's allowed bounds (0 for `clean`; non-zero allowed for
      injected modes that document failure as the expected behavior).
    - `min_move_dispatches_required` — when the operator asked for
      MOVE evidence, step 7 must record a successful MOVE dispatch.

    The benchmark passes only when every flag above is True. Adding
    a flag is additive (existing artifacts stay readable; the rollup
    just gets stricter); removing one is a breaking change.
    """
    return (
        c.all_commands_ok
        and c.capabilities_match_locked_surface
        and c.loop_reached_running
        and c.loop_ticks_met
        and c.loop_stopped_clean
        and c.pins_low_at_end
        and c.no_unexpected_failures
        and c.min_move_dispatches_required
    )


# -----------------------------------------------------------------------------
# Criterion helpers — each returns a primitive so the rollup is auditable.
# -----------------------------------------------------------------------------


def _capabilities_match(cap_step: Optional[BenchmarkStep]) -> bool:
    if cap_step is None or not cap_step.ok or cap_step.telemetry_snapshot is None:
        return False
    caps = cap_step.telemetry_snapshot.get("capabilities")
    if not isinstance(caps, list):
        return False
    return tuple(sorted(str(c) for c in caps)) == LOCKED_PI_SURFACE


def _running_and_ticks(status_step: Optional[BenchmarkStep]) -> Tuple[bool, int]:
    if status_step is None or status_step.telemetry_snapshot is None:
        return False, 0
    loop = status_step.telemetry_snapshot.get("mission_loop")
    if not isinstance(loop, dict):
        return False, 0
    running = bool(loop.get("running"))
    raw_ticks = loop.get("tick_count", 0)
    try:
        ticks = int(raw_ticks)
    except (TypeError, ValueError):
        ticks = 0
    return running, ticks


def _last_dispatched_was_successful_move(
    status_step: Optional[BenchmarkStep],
) -> bool:
    if status_step is None or status_step.telemetry_snapshot is None:
        return False
    loop = status_step.telemetry_snapshot.get("mission_loop")
    if not isinstance(loop, dict):
        return False
    return loop.get("last_dispatched") == "move" and bool(
        loop.get("last_dispatch_ok")
    )


def _loop_stopped_clean(
    stop_step: Optional[BenchmarkStep],
    final_status: Optional[BenchmarkStep],
) -> bool:
    if stop_step is None or not stop_step.ok:
        return False
    if final_status is None or final_status.telemetry_snapshot is None:
        return False
    loop = final_status.telemetry_snapshot.get("mission_loop")
    if isinstance(loop, dict) and loop.get("running"):
        return False
    return True


def _pins_low_at_end(final_status: Optional[BenchmarkStep]) -> bool:
    if final_status is None or final_status.telemetry_snapshot is None:
        return False
    ctl = final_status.telemetry_snapshot.get("controller")
    if not isinstance(ctl, dict):
        return False
    armed = ctl.get("armed")
    if armed is None:
        return False
    return not bool(armed)


def _failure_counters(
    status_step: Optional[BenchmarkStep],
) -> Optional[Tuple[int, int, int]]:
    if status_step is None or status_step.telemetry_snapshot is None:
        return None
    loop = status_step.telemetry_snapshot.get("mission_loop")
    if not isinstance(loop, dict):
        return None
    try:
        return (
            int(loop.get("vision_failures", 0)),
            int(loop.get("mission_failures", 0)),
            int(loop.get("dispatch_failures", 0)),
        )
    except (TypeError, ValueError):
        return None


def _stale_world_skips(status_step: Optional[BenchmarkStep]) -> Optional[int]:
    if status_step is None or status_step.telemetry_snapshot is None:
        return None
    loop = status_step.telemetry_snapshot.get("mission_loop")
    if not isinstance(loop, dict):
        return None
    raw = loop.get("stale_world_skips")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _no_unexpected_failures(
    *,
    inject: Optional[str],
    vision_failures: int,
    mission_failures: int,
    dispatch_failures: int,
) -> bool:
    """Inject-aware failure-count check.

    For a clean run, all three counters must be zero — any failure is a
    regression in the contract. For each injected mode, failures *in
    the injected stage* are allowed (and expected); failures in
    untouched stages are still regressions. This keeps the benchmark
    honest about which stages are covered by which inject without
    pretending injection is a free pass for every counter.
    """
    if inject is None:
        return (
            vision_failures == 0
            and mission_failures == 0
            and dispatch_failures == 0
        )
    if inject == INJECT_CAMERA_OFFLINE:
        # Camera-offline inject leaves vision returning empty results;
        # the loop sees no detections rather than raises. So all three
        # counters should still be zero — the visibility for this
        # inject is `stale_world_skips`, not failures.
        return (
            mission_failures == 0
            and dispatch_failures == 0
        )
    if inject == INJECT_MISSION_OFFLINE:
        # mission_offline inject means the policy returns idle; the
        # loop sees `next_command=None` rather than raises. No counters
        # should climb.
        return (
            vision_failures == 0
            and mission_failures == 0
            and dispatch_failures == 0
        )
    if inject == INJECT_VISION_DROP_AFTER_N:
        # Vision raises after N successful scenes; vision_failures is
        # expected to grow. Other counters must stay clean.
        return (
            mission_failures == 0
            and dispatch_failures == 0
        )
    return False  # unknown inject; fail closed


# -----------------------------------------------------------------------------
# Serialization — keep the artifact stable across runs.
# -----------------------------------------------------------------------------


def result_to_dict(result: BenchmarkResult) -> Dict[str, Any]:
    """Serialize a `BenchmarkResult` to a JSON-ready dict.

    The output shape is the artifact contract; `docs/pi-benchmark.md`
    documents every field. Field order is preserved as a courtesy to
    diff tools (Python 3.7+ dicts are insertion-ordered).
    """
    return {
        "schema_version": result.schema_version,
        "run_id": result.run_id,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "duration_s": result.duration_s,
        "mode": result.mode,
        "inject": result.inject,
        "intent": result.intent,
        "hold_s": result.hold_s,
        "config_summary": dict(result.config_summary),
        "command_sequence": [_step_to_dict(s) for s in result.command_sequence],
        "criteria": _criteria_to_dict(result.criteria),
        "success": result.success,
    }


def _step_to_dict(s: BenchmarkStep) -> Dict[str, Any]:
    return {
        "step": s.step,
        "name": s.name,
        "kind": s.kind,
        "started_at": s.started_at,
        "duration_s": s.duration_s,
        "ok": s.ok,
        "state": s.state,
        "error_code": s.error_code,
        "error_message": s.error_message,
        "message": s.message,
        "telemetry_snapshot": (
            dict(s.telemetry_snapshot) if s.telemetry_snapshot is not None else None
        ),
    }


def _criteria_to_dict(c: BenchmarkCriteria) -> Dict[str, Any]:
    return {
        "expected_outcome": c.expected_outcome,
        "all_commands_ok": c.all_commands_ok,
        "capabilities_match_locked_surface": c.capabilities_match_locked_surface,
        "loop_reached_running": c.loop_reached_running,
        "min_loop_ticks_required": c.min_loop_ticks_required,
        "loop_ticks_observed": c.loop_ticks_observed,
        "loop_ticks_met": c.loop_ticks_met,
        "loop_stopped_clean": c.loop_stopped_clean,
        "pins_low_at_end": c.pins_low_at_end,
        "move_dispatches_observed": c.move_dispatches_observed,
        "min_move_dispatches_required": c.min_move_dispatches_required,
        "no_unexpected_failures": c.no_unexpected_failures,
        "vision_failures": c.vision_failures,
        "mission_failures": c.mission_failures,
        "dispatch_failures": c.dispatch_failures,
        "stale_world_skips": c.stale_world_skips,
        "notes": list(c.notes),
    }


# -----------------------------------------------------------------------------
# Human-readable view.
# -----------------------------------------------------------------------------


def format_result_human(result: BenchmarkResult) -> str:
    """Pretty-print a `BenchmarkResult` for an operator.

    Mirrors the layout the runbook describes: header, config summary,
    per-step table, criteria roll-up, final verdict. Stable across
    runs so an operator can scan two consecutive runs side-by-side.
    """
    out: List[str] = []
    verdict = "PASS" if result.success else "FAIL"
    out.append(f"pi_follow_bench — {verdict}")
    out.append(f"  run_id:       {result.run_id}")
    out.append(f"  mode:         {result.mode}")
    out.append(f"  inject:       {result.inject or '(none)'}")
    out.append(f"  intent:       {result.intent!r}")
    out.append(f"  hold_s:       {result.hold_s}")
    out.append(f"  started_at:   {result.started_at}")
    out.append(f"  duration_s:   {result.duration_s}")
    out.append("")
    out.append("config:")
    for key, val in result.config_summary.items():
        out.append(f"  {key}: {val}")
    out.append("")
    out.append("sequence:")
    for s in result.command_sequence:
        flag = "ok" if s.ok else f"FAIL ({s.error_code})"
        out.append(
            f"  {s.step:>2}. {s.name:<14} {s.kind:<8} {s.duration_s:>6.3f}s  {flag}"
        )
    out.append("")
    out.append("criteria:")
    c = result.criteria
    out.append(f"  expected_outcome:                  {c.expected_outcome}")
    out.append(f"  all_commands_ok:                   {c.all_commands_ok}")
    out.append(f"  capabilities_match_locked_surface: {c.capabilities_match_locked_surface}")
    out.append(
        f"  loop_reached_running:              {c.loop_reached_running} "
        f"(ticks={c.loop_ticks_observed} / required={c.min_loop_ticks_required})"
    )
    out.append(
        f"  move_dispatches_observed:          {c.move_dispatches_observed} "
        f"(required: {'yes' if not c.min_move_dispatches_required or c.move_dispatches_observed else 'no'})"
    )
    out.append(f"  loop_stopped_clean:                {c.loop_stopped_clean}")
    out.append(f"  pins_low_at_end:                   {c.pins_low_at_end}")
    out.append(
        f"  no_unexpected_failures:            {c.no_unexpected_failures} "
        f"(vision={c.vision_failures} mission={c.mission_failures} "
        f"dispatch={c.dispatch_failures} stale_world_skips={c.stale_world_skips})"
    )
    if c.notes:
        out.append("  notes:")
        for n in c.notes:
            out.append(f"    - {n}")
    out.append("")
    out.append(f"verdict: {verdict}")
    return "\n".join(out)
