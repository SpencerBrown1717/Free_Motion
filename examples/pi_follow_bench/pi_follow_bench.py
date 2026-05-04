#!/usr/bin/env python3
"""Free Motion pi_follow_bench (Step 5 — the named Pi benchmark demo).

The single repeatable Pi benchmark task. Drives the locked Pi reference
architecture (`docs/pi-reference.md`) through a fixed command sequence,
applies fixed pass/fail criteria, and emits a stable JSON artifact for
each run. The runner core is `benchmark.py` in this directory; this
file is the operator-facing CLI plus the build/inject/output/view path.

Two modes:

- ``--mode=bench`` (default) — wires the same stack
  `pi_closed_loop_demo` wires (`Config.from_env` → `PiCameraSource` →
  `YoloVision` → `WorldState` → `GemmaMissionControl` → `SafetyGate`
  → `PiHardwareController`) and runs the benchmark against it.
  Designed to be run on a real Pi bench rig with an operator standing
  in front of the camera.

- ``--mode=ci`` — wires a deterministic mock chain (`MockHardwareController`
  + `MockVision` with a scripted person scene + `MockMissionControl` +
  `WorldState` + `MissionLoop`) so the same harness, criteria, and
  artifact schema can be exercised in CI without a Pi, a camera, or
  the heavy `[yolo,gemma,picam]` extras. The mock chain is tuned so a
  successful clean run takes ~1s on a CI runner.

Failure injection (``--inject=camera_offline|mission_offline|
vision_drop_after_n``) replaces the relevant adapter with a fault-
injecting variant. The benchmark always runs the full 10-step
sequence; injection changes the *expected outcome*, not the sequence.
Universal contracts (no crash, /stop returns ok, pins LOW at end,
loop reads idle after stop, capabilities match locked surface) must
hold under every inject.

Artifact location defaults to
``~/.cache/freemotion/results/pi_follow_bench-<timestamp>.json``;
override with ``--output``. Pass ``--output -`` to print the JSON to
stdout (useful for piping into ``jq``).

See ``docs/pi-benchmark.md`` for the frozen protocol and
``examples/pi_follow_bench/README.md`` for the operator runbook.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make `benchmark` importable when this script is run directly. Tests
# import via `sys.path.insert`; running the file as a script needs the
# same dance because Python doesn't add the script's dir to sys.path
# when the script lives outside the cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import benchmark  # noqa: E402

# Reuse the same router-wiring helpers the canonical Pi reference
# exposes — keeps the benchmark genuinely the same code path the
# closed-loop demo runs.
_DEMO_DIR = os.path.normpath(os.path.join(_HERE, "..", "pi_closed_loop_demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pi_closed_loop_demo  # noqa: E402

from freemotion.agent import MissionLoop  # noqa: E402
from freemotion.config import Config  # noqa: E402
from freemotion.hardware import (  # noqa: E402
    HardwareController,
    MockHardwareController,
    SafetyGate,
)
from freemotion.mission_control import (  # noqa: E402
    MissionDecision,
    MissionPolicy,
    MockMissionControl,
    make_mission_from_config,
)
from freemotion.protocol import CommandName, SafetyMode  # noqa: E402
from freemotion.router import Router  # noqa: E402
from freemotion.vision import (  # noqa: E402
    Detection,
    MockVision,
    VisionBackend,
    VisionResult,
    make_vision_from_config,
)
from freemotion.world import WorldState, WorldStateSnapshot  # noqa: E402

LOG = logging.getLogger("freemotion.pi_follow_bench")


# -----------------------------------------------------------------------------
# Stack assembly — `--mode=bench` and `--mode=ci`.
# -----------------------------------------------------------------------------


@dataclasses.dataclass
class _Stack:
    """Everything `benchmark.run_benchmark` needs to drive the loop.

    Plus a `cleanup` callback so the CLI can tear down picamera2 / the
    inner Pi controller in `--mode=bench` exactly the way
    `pi_closed_loop_demo.graceful_shutdown` does.
    """

    cfg: Config
    controller: HardwareController
    vision: VisionBackend
    mission: MissionPolicy
    world: WorldState
    mission_loop: MissionLoop
    router: Router
    cleanup: Callable[[], None]


def _build_bench_stack(args: argparse.Namespace) -> _Stack:
    """Build the real-Pi stack, identical to `pi_closed_loop_demo.main`.

    The benchmark uses the **same** `build_router_without_loop` /
    `attach_mission_loop` helpers the canonical demo uses; this is
    what keeps "the benchmark exercises the locked Pi reference"
    truthful. Any drift between the demo's wiring and this function
    is a bug in this function.

    `--mode=bench` requires `TELEGRAM_BOT_TOKEN` to be set (for
    `Config.from_env`); the benchmark itself never speaks Telegram,
    but the operator's `~/.config/freemotion.env` already has the
    token because they ran `pi_closed_loop_demo` first. Reading
    `Config` from env keeps every operational knob (`FREEMOTION_*`)
    consistent with the closed-loop demo; the benchmark is not
    allowed to invent its own config story.
    """
    cfg = Config.from_env()

    # Camera. `_build_picamera_source()` wraps the optional `picamera2`
    # import; failure flips `cam.available=False`. We let the benchmark
    # try to run anyway in bench mode — the criteria will report that
    # the loop never made detections, which is the honest outcome on a
    # camera-less host. The CLI's `--inject=camera_offline` is the
    # supported way to ask for that explicitly.
    cam = pi_closed_loop_demo.PiCameraSource()
    if not cam.available:
        LOG.warning(
            "PiCameraSource is offline. The benchmark will run but the "
            "loop will see no detections; use --mode=ci for a deterministic "
            "harness, or fix the camera (pip install -e .[picam] on a Pi) "
            "and rerun. Continuing."
        )

    vision = make_vision_from_config(cfg, frame_source=cam)
    if not vision.available:
        LOG.warning(
            "VisionBackend %r is offline. The benchmark will run but the "
            "loop will see no detections.",
            getattr(vision, "name", "?"),
        )

    mission = make_mission_from_config(cfg)
    if not getattr(mission, "available", True):
        LOG.warning(
            "MissionPolicy %r is offline (model load failed?). The loop "
            "will tick but no MOVE will be dispatched.",
            getattr(mission, "name", "?"),
        )

    world = WorldState()

    inner = pi_closed_loop_demo.make_controller_from_config(cfg)
    controller = SafetyGate(inner, cfg.safety_default)

    return _wire_stack(cfg, controller, vision, mission, world, args, _bench_cleanup(cam, inner))


def _build_ci_stack(args: argparse.Namespace) -> _Stack:
    """Build the all-mock stack used by `--mode=ci` and the test suite.

    Choices:

    - `cfg.safety_default = bench` — `mission_start` is refused in
      `dry_run` (ADR-0010), so the benchmark cannot run there. `bench`
      is the cheapest mode that exercises the full closed loop.
    - `MockHardwareController()` — the real `arm()`/`disarm()` paths,
      `state()` reports `armed`. Same controller `tests/test_mock.py`
      uses.
    - `MockVision(scripted=[<one person scene>])` — cycles a single
      `Detection(label="person", confidence=0.9)` so every tick has a
      target. `MockMissionControl` returns `MOVE` when the intent is
      `"follow person"`, so every tick produces a dispatched MOVE in
      the clean run.
    - `MissionLoop(tick_interval_s=0.05, stale_world_timeout_s=2.0)`
      — fast ticks for CI; stale-world timeout long enough to absorb
      the `--inject=vision_drop_after_n` window before the world
      goes stale (see that inject's expected behavior in
      `docs/pi-benchmark.md`).
    """
    safety = SafetyMode.BENCH
    cfg = Config(
        token="bench-mode-token-not-used",
        device_id="pi-follow-bench-ci",
        safety_default=safety,
        hardware_profile="host",
        vision_backend="mock",
        mission_backend="mock",
    )

    inner = MockHardwareController()
    controller = SafetyGate(inner, cfg.safety_default)

    vision = _build_ci_vision(args)
    mission = _build_ci_mission(args)
    world = WorldState()

    return _wire_stack(cfg, controller, vision, mission, world, args, _ci_cleanup())


def _wire_stack(
    cfg: Config,
    controller: HardwareController,
    vision: VisionBackend,
    mission: MissionPolicy,
    world: WorldState,
    args: argparse.Namespace,
    cleanup: Callable[[], None],
) -> _Stack:
    """Compose the router + mission loop the same way `pi_closed_loop_demo` does.

    This is the seam that keeps the benchmark architecturally honest:
    `build_router_without_loop` and `attach_mission_loop` are the
    canonical demo's helpers; the benchmark reuses them rather than
    inventing its own router build path.
    """
    # Forward declaration for the on_stop closure — `mission_loop` is
    # built below.
    mission_loop_ref: Dict[str, MissionLoop] = {}

    def _stop_everything() -> None:
        loop = mission_loop_ref.get("loop")
        if loop is not None:
            try:
                loop.stop()
            except Exception:  # pragma: no cover - defensive
                LOG.warning(
                    "mission_loop.stop raised during /stop; ignoring",
                    exc_info=True,
                )
        try:
            controller.stop()
        except Exception:  # pragma: no cover - defensive
            LOG.warning(
                "controller.stop raised during /stop; ignoring", exc_info=True
            )

    router = pi_closed_loop_demo.build_router_without_loop(
        cfg, controller=controller, on_stop=_stop_everything
    )

    mission_loop = MissionLoop(
        vision=vision,
        mission=mission,
        world=world,
        router=router,
        cfg=cfg,
        tick_interval_s=args.tick_interval,
        stale_world_timeout_s=args.stale_world_timeout,
    )
    mission_loop_ref["loop"] = mission_loop

    pi_closed_loop_demo.attach_mission_loop(
        router,
        cfg=cfg,
        controller=controller,
        mission_loop=mission_loop,
        default_intent=args.intent,
    )

    return _Stack(
        cfg=cfg,
        controller=controller,
        vision=vision,
        mission=mission,
        world=world,
        mission_loop=mission_loop,
        router=router,
        cleanup=cleanup,
    )


# -----------------------------------------------------------------------------
# CI vision/mission builders + failure injection.
# -----------------------------------------------------------------------------


_PERSON_SCENE = VisionResult(
    detections=(
        Detection(
            label="person",
            confidence=0.9,
            bbox=(0.4, 0.4, 0.2, 0.2),
        ),
    ),
)
_EMPTY_SCENE = VisionResult(detections=())


class _DroppingVision:
    """Returns N person scenes, then raises on every subsequent call.

    Used by `--inject=vision_drop_after_n`. Models a vision backend
    that runs cleanly for a while and then starts erroring (the most
    common real-world failure mode for live YOLO on a Pi).
    """

    name = "ci_dropping"

    def __init__(self, *, n_clean: int = 3) -> None:
        self._n_clean = max(0, int(n_clean))
        self._calls = 0

    @property
    def available(self) -> bool:
        return True

    def scene(self) -> VisionResult:
        i = self._calls
        self._calls += 1
        if i < self._n_clean:
            return _PERSON_SCENE
        raise RuntimeError(
            f"_DroppingVision: simulated drop on call #{i + 1}"
        )


class _OfflineMission:
    """`MissionPolicy` that always returns idle.

    Used by `--inject=mission_offline`. Models a mission backend whose
    model load failed (Gemma OOM, missing weights, network drop). Per
    [ADR-0008] the real adapter exposes the same shape: `available=False`
    + `plan()` returns idle decisions.
    """

    name = "ci_offline_mission"

    @property
    def available(self) -> bool:
        return False

    def plan(
        self,
        *,
        intent: str,
        scene: VisionResult,
        world: WorldStateSnapshot,
    ) -> MissionDecision:
        return MissionDecision(
            next_command=None,
            reason="ci_offline_mission: simulated offline policy",
            confidence=0.0,
        )


def _build_ci_vision(args: argparse.Namespace) -> VisionBackend:
    if args.inject == benchmark.INJECT_CAMERA_OFFLINE:
        # Camera-offline produces empty scenes (no detections). The
        # mission policy will report "follow: no person in scene" and
        # the loop will not dispatch MOVE; after `stale_world_timeout_s`
        # the world goes stale.
        return MockVision(scripted=[_EMPTY_SCENE])
    if args.inject == benchmark.INJECT_VISION_DROP_AFTER_N:
        return _DroppingVision(n_clean=args.vision_drop_after)
    return MockVision(scripted=[_PERSON_SCENE])


def _build_ci_mission(args: argparse.Namespace) -> MissionPolicy:
    if args.inject == benchmark.INJECT_MISSION_OFFLINE:
        return _OfflineMission()
    return MockMissionControl()


# -----------------------------------------------------------------------------
# Cleanup helpers.
# -----------------------------------------------------------------------------


def _bench_cleanup(cam: Any, inner: Any) -> Callable[[], None]:
    def cleanup() -> None:
        try:
            cam.close()
        except Exception:
            LOG.warning("cam.close raised during cleanup; ignoring", exc_info=True)
        clean = getattr(inner, "cleanup", None)
        if callable(clean):
            try:
                clean()
            except Exception:
                LOG.warning(
                    "inner.cleanup raised during cleanup; ignoring",
                    exc_info=True,
                )

    return cleanup


def _ci_cleanup() -> Callable[[], None]:
    def cleanup() -> None:
        return None

    return cleanup


# -----------------------------------------------------------------------------
# Output paths.
# -----------------------------------------------------------------------------


_DEFAULT_RESULTS_DIR = Path.home() / ".cache" / "freemotion" / "results"


def _default_output_path(*, mode: str, inject: Optional[str]) -> Path:
    """Compose a deterministic output path for a fresh run.

    Filename pattern: `pi_follow_bench-<mode>[-<inject>]-<UTCstamp>.json`.
    Stamp is `YYYYMMDDTHHMMSSZ` so a `ls` lists runs chronologically.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{inject}" if inject else ""
    return _DEFAULT_RESULTS_DIR / f"pi_follow_bench-{mode}{suffix}-{stamp}.json"


def _write_artifact(result_dict: Dict[str, Any], output: str) -> Optional[Path]:
    """Write the JSON artifact to `output`. Returns the resolved path
    (or `None` when written to stdout via `--output -`).
    """
    text = json.dumps(result_dict, indent=2, sort_keys=False)
    if output == "-":
        sys.stdout.write(text)
        sys.stdout.write("\n")
        return None
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    return path


# -----------------------------------------------------------------------------
# Subcommands.
# -----------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    """Build the stack, run the benchmark, write the artifact, exit."""
    if args.mode == "bench":
        stack = _build_bench_stack(args)
    else:
        stack = _build_ci_stack(args)

    LOG.info(
        "pi_follow_bench: mode=%s inject=%s intent=%r tick=%.3fs hold=%.2fs "
        "stale_world_timeout=%.2fs vision=%s mission=%s",
        args.mode,
        args.inject or "(none)",
        args.intent,
        args.tick_interval,
        args.hold,
        args.stale_world_timeout,
        getattr(stack.vision, "name", "?"),
        getattr(stack.mission, "name", "?"),
    )

    try:
        result = benchmark.run_benchmark(
            cfg=stack.cfg,
            controller=stack.controller,
            mission_loop=stack.mission_loop,
            router=stack.router,
            mode=args.mode,
            inject=args.inject,
            intent=args.intent,
            hold_s=args.hold,
            min_loop_ticks=args.min_loop_ticks,
            min_move_dispatches=args.min_move_dispatches,
        )
    finally:
        # Same ordering as `pi_closed_loop_demo.graceful_shutdown`: the
        # mission loop is stopped via `/stop`, but a hung tick or a
        # benchmark abort might have left it running. Belt-and-suspenders.
        try:
            stack.mission_loop.stop()
        except Exception:
            LOG.warning(
                "mission_loop.stop raised during teardown; ignoring",
                exc_info=True,
            )
        try:
            stack.controller.stop()
        except Exception:
            LOG.warning(
                "controller.stop raised during teardown; ignoring",
                exc_info=True,
            )
        try:
            stack.cleanup()
        except Exception:
            LOG.warning(
                "stack cleanup raised during teardown; ignoring", exc_info=True
            )

    output = (
        args.output
        if args.output != "default"
        else str(_default_output_path(mode=args.mode, inject=args.inject))
    )
    written = _write_artifact(benchmark.result_to_dict(result), output)
    if written is not None:
        LOG.info("pi_follow_bench: wrote artifact to %s", written)

    if args.print_human:
        sys.stderr.write(benchmark.format_result_human(result) + "\n")

    if result.success:
        LOG.info("pi_follow_bench: PASS")
        return 0
    LOG.warning("pi_follow_bench: FAIL")
    return 1


def _cmd_view(args: argparse.Namespace) -> int:
    """Read a previously written artifact and print the human view."""
    path = Path(args.path).expanduser()
    if not path.is_file():
        sys.stderr.write(f"pi_follow_bench: no such file: {path}\n")
        return 2
    raw = json.loads(path.read_text())
    result = _result_from_dict(raw)
    sys.stdout.write(benchmark.format_result_human(result) + "\n")
    return 0


def _result_from_dict(d: Dict[str, Any]) -> benchmark.BenchmarkResult:
    """Round-trip a dict back into a `BenchmarkResult` for the view path."""
    seq: List[benchmark.BenchmarkStep] = []
    for s in d.get("command_sequence", []):
        seq.append(
            benchmark.BenchmarkStep(
                step=int(s["step"]),
                name=str(s["name"]),
                kind=str(s["kind"]),
                started_at=str(s["started_at"]),
                duration_s=float(s["duration_s"]),
                ok=bool(s["ok"]),
                state=s.get("state"),
                error_code=s.get("error_code"),
                error_message=s.get("error_message"),
                message=str(s.get("message") or ""),
                telemetry_snapshot=s.get("telemetry_snapshot"),
            )
        )
    c = d.get("criteria", {})
    criteria = benchmark.BenchmarkCriteria(
        expected_outcome=str(c.get("expected_outcome", "clean")),
        all_commands_ok=bool(c.get("all_commands_ok", False)),
        capabilities_match_locked_surface=bool(
            c.get("capabilities_match_locked_surface", False)
        ),
        loop_reached_running=bool(c.get("loop_reached_running", False)),
        min_loop_ticks_required=int(c.get("min_loop_ticks_required", 0)),
        loop_ticks_observed=int(c.get("loop_ticks_observed", 0)),
        loop_ticks_met=bool(c.get("loop_ticks_met", False)),
        loop_stopped_clean=bool(c.get("loop_stopped_clean", False)),
        pins_low_at_end=bool(c.get("pins_low_at_end", False)),
        move_dispatches_observed=bool(c.get("move_dispatches_observed", False)),
        min_move_dispatches_required=bool(
            c.get("min_move_dispatches_required", False)
        ),
        no_unexpected_failures=bool(c.get("no_unexpected_failures", False)),
        vision_failures=int(c.get("vision_failures", 0)),
        mission_failures=int(c.get("mission_failures", 0)),
        dispatch_failures=int(c.get("dispatch_failures", 0)),
        stale_world_skips=int(c.get("stale_world_skips", 0)),
        notes=list(c.get("notes", []) or []),
    )
    return benchmark.BenchmarkResult(
        schema_version=int(d.get("schema_version", benchmark.SCHEMA_VERSION)),
        run_id=str(d.get("run_id", "")),
        started_at=str(d.get("started_at", "")),
        completed_at=str(d.get("completed_at", "")),
        duration_s=float(d.get("duration_s", 0.0)),
        mode=str(d.get("mode", "ci")),
        inject=d.get("inject"),
        intent=str(d.get("intent", "")),
        hold_s=float(d.get("hold_s", 0.0)),
        config_summary=dict(d.get("config_summary", {}) or {}),
        command_sequence=seq,
        criteria=criteria,
        success=bool(d.get("success", False)),
    )


# -----------------------------------------------------------------------------
# argparse glue.
# -----------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi_follow_bench",
        description=(
            "Free Motion pi_follow_bench — the named, repeatable Pi benchmark "
            "demo. Drives the locked Pi reference architecture through a fixed "
            "10-step command sequence and emits a stable JSON artifact."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("FREEMOTION_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO; env FREEMOTION_LOG_LEVEL)",
    )

    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Run the benchmark and write a result artifact.")
    run_p.add_argument(
        "--mode",
        choices=["bench", "ci"],
        default="bench",
        help=(
            "bench: real-Pi stack via Config.from_env (default). "
            "ci: deterministic mock stack — no Pi, no models, no env vars."
        ),
    )
    run_p.add_argument(
        "--inject",
        choices=list(benchmark.KNOWN_INJECTS),
        default=None,
        help="Failure injection. Default is no injection (clean run).",
    )
    run_p.add_argument(
        "--intent",
        default=os.environ.get("FREEMOTION_DEFAULT_INTENT", "follow person"),
        help="Intent passed to /mission_start (default: 'follow person').",
    )
    run_p.add_argument(
        "--hold",
        type=float,
        default=benchmark.DEFAULT_HOLD_S,
        help=(
            "Seconds to wait between /mission_start and the mid-mission "
            f"/status (default: {benchmark.DEFAULT_HOLD_S})."
        ),
    )
    run_p.add_argument(
        "--tick-interval",
        type=float,
        default=float(
            os.environ.get("FREEMOTION_MISSION_TICK_INTERVAL_S", "1.0")
        ),
        help="Mission loop tick interval (default: 1.0).",
    )
    run_p.add_argument(
        "--stale-world-timeout",
        type=float,
        default=MissionLoop.DEFAULT_STALE_WORLD_TIMEOUT_S,
        help=(
            "Stale-world refusal timeout for the mission loop "
            f"(default: {MissionLoop.DEFAULT_STALE_WORLD_TIMEOUT_S})."
        ),
    )
    run_p.add_argument(
        "--min-loop-ticks",
        type=int,
        default=benchmark.DEFAULT_MIN_LOOP_TICKS,
        help=(
            "Minimum mission_loop.tick_count required at the mid-mission "
            f"/status (default: {benchmark.DEFAULT_MIN_LOOP_TICKS})."
        ),
    )
    run_p.add_argument(
        "--min-move-dispatches",
        type=int,
        choices=[0, 1],
        default=0,
        help=(
            "Set to 1 to require last_dispatched=='move' with ok=True at the "
            "mid-mission /status (recommended for CI clean runs; default 0)."
        ),
    )
    run_p.add_argument(
        "--vision-drop-after",
        type=int,
        default=3,
        help=(
            "For --inject=vision_drop_after_n: number of clean scenes to "
            "return before raising on every subsequent scene() call (default: 3)."
        ),
    )
    run_p.add_argument(
        "--output",
        default="default",
        help=(
            "Path to write the JSON artifact. Default writes to "
            "~/.cache/freemotion/results/pi_follow_bench-<mode>[-<inject>]-<ts>.json. "
            "Pass '-' to write to stdout."
        ),
    )
    run_p.add_argument(
        "--print-human",
        action="store_true",
        help="Also print the human-readable summary to stderr after writing.",
    )
    run_p.set_defaults(func=_cmd_run)

    view_p = sub.add_parser(
        "view", help="Read a previously written artifact and pretty-print it."
    )
    view_p.add_argument("path", help="Path to a pi_follow_bench artifact JSON file.")
    view_p.set_defaults(func=_cmd_view)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=str(args.log_level).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.cmd is None:
        parser.print_help(sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
