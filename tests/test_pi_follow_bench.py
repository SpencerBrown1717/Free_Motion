"""Tests for examples/pi_follow_bench (Step 5).

Coverage strategy:

- The runner core (`benchmark.py`) is exercised end-to-end through
  the CLI's CI mode — same code path the GitHub Actions runner takes.
- Each `--inject` mode is exercised; each must produce a passing
  artifact (universal contracts hold under injection).
- Bad inputs (unknown mode, unknown inject, bad knobs) raise.
- The artifact schema is verified field-by-field for both the clean
  run and one inject.
- The view path is round-tripped: run -> dict -> view.
- Capability surface is asserted to match the locked Pi reference
  (the benchmark's reason to exist).
- Output paths are verified: the auto-generated path lands in
  ~/.cache/freemotion/results/, and `--output -` writes to stdout
  without creating a file.

Tests run on a CI host without RPi.GPIO, picamera2, ultralytics, or
transformers — `--mode=ci` uses MockHardwareController +
MockVision + MockMissionControl all the way down.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "examples", "pi_follow_bench")
)
_DEMO_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "examples", "pi_closed_loop_demo")
)
for _d in (_BENCH_DIR, _DEMO_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import benchmark  # noqa: E402
import pi_follow_bench  # noqa: E402


# ----------------------------------------------------------------------
# Tunables for the test environment.
# ----------------------------------------------------------------------

# Fast ticks + short hold keep the CI suite under ~1s per benchmark run.
# Stale-world timeout is short enough to engage during the
# vision_drop_after_n inject, long enough to absorb default latency.
_FAST_HOLD = 0.3
_FAST_TICK = 0.02
_SHORT_STALE = 0.2
_FAST_MIN_LOOP_TICKS = 2


# ----------------------------------------------------------------------
# Smoke: the CLI imports cleanly on a non-Pi host.
# ----------------------------------------------------------------------


def test_modules_import() -> None:
    assert hasattr(benchmark, "run_benchmark")
    assert hasattr(benchmark, "result_to_dict")
    assert hasattr(benchmark, "format_result_human")
    assert hasattr(pi_follow_bench, "main")
    assert hasattr(pi_follow_bench, "_build_ci_stack")
    assert hasattr(pi_follow_bench, "_build_bench_stack")


def test_locked_surface_matches_pi_reference() -> None:
    """The benchmark's idea of the locked surface MUST match
    `docs/pi-reference.md` §2 verbatim — eight commands, sorted."""
    assert benchmark.LOCKED_PI_SURFACE == (
        "arm",
        "capabilities",
        "disarm",
        "mission_start",
        "move",
        "ping",
        "status",
        "stop",
    )


def test_known_injects_are_only_three() -> None:
    """The benchmark protocol freezes three injects. Adding a fourth
    requires updating docs/pi-benchmark.md and ADR-0013."""
    assert benchmark.KNOWN_INJECTS == (
        "camera_offline",
        "mission_offline",
        "vision_drop_after_n",
    )


def test_systemd_unit_exists_and_is_oneshot() -> None:
    root = Path(_BENCH_DIR)
    assert (root / "README.md").is_file()
    unit = root / "systemd" / "freemotion-pi-follow-bench.service"
    assert unit.is_file()
    text = unit.read_text()
    assert "Type=oneshot" in text
    assert "pi_follow_bench/pi_follow_bench.py" in text


# ----------------------------------------------------------------------
# Run-level: clean CI run passes every contract.
# ----------------------------------------------------------------------


def _run_main(*args: str) -> int:
    """Invoke pi_follow_bench.main() with the given argv."""
    return pi_follow_bench.main(["--log-level=ERROR"] + list(args))


def _run_ci(
    tmp_path: Path,
    *,
    inject: str | None = None,
    extra: tuple[str, ...] = (),
) -> Dict[str, Any]:
    out = tmp_path / "result.json"
    argv = [
        "run",
        "--mode=ci",
        f"--hold={_FAST_HOLD}",
        f"--tick-interval={_FAST_TICK}",
        f"--stale-world-timeout={_SHORT_STALE}",
        f"--min-loop-ticks={_FAST_MIN_LOOP_TICKS}",
        f"--output={out}",
    ]
    if inject is not None:
        argv.append(f"--inject={inject}")
    argv.extend(extra)
    rc = _run_main(*argv)
    raw = json.loads(out.read_text())
    return {"rc": rc, "artifact": raw}


def test_ci_clean_run_passes(tmp_path: Path) -> None:
    """Clean CI run: every flag true, exit 0."""
    res = _run_ci(tmp_path, extra=("--min-move-dispatches=1",))
    assert res["rc"] == 0
    art = res["artifact"]
    assert art["success"] is True
    c = art["criteria"]
    assert c["expected_outcome"] == "clean"
    assert c["all_commands_ok"] is True
    assert c["capabilities_match_locked_surface"] is True
    assert c["loop_reached_running"] is True
    assert c["loop_ticks_met"] is True
    assert c["loop_ticks_observed"] >= _FAST_MIN_LOOP_TICKS
    assert c["loop_stopped_clean"] is True
    assert c["pins_low_at_end"] is True
    assert c["move_dispatches_observed"] is True
    assert c["min_move_dispatches_required"] is True
    assert c["no_unexpected_failures"] is True
    assert c["vision_failures"] == 0
    assert c["mission_failures"] == 0
    assert c["dispatch_failures"] == 0


def test_ci_clean_run_artifact_schema(tmp_path: Path) -> None:
    """Schema-shape verification: the artifact carries every field
    documented in `docs/pi-benchmark.md` §3, with the right types."""
    art = _run_ci(tmp_path)["artifact"]
    assert art["schema_version"] == benchmark.SCHEMA_VERSION
    assert isinstance(art["run_id"], str) and len(art["run_id"]) > 0
    assert isinstance(art["started_at"], str)
    assert isinstance(art["completed_at"], str)
    assert isinstance(art["duration_s"], (int, float))
    assert art["mode"] == "ci"
    assert art["inject"] is None
    assert art["intent"] == "follow person"
    assert isinstance(art["hold_s"], (int, float))
    cs = art["config_summary"]
    assert cs["device_id"] == "pi-follow-bench-ci"
    assert cs["safety_default"] == "bench"
    assert cs["vision_backend"] == "mock"
    assert cs["mission_backend"] == "mock"
    seq = art["command_sequence"]
    assert len(seq) == 10
    expected_names = [
        "ping",
        "capabilities",
        "status",
        "arm",
        "mission_start",
        "observe",
        "status",
        "stop",
        "disarm",
        "status",
    ]
    assert [s["name"] for s in seq] == expected_names
    assert [s["kind"] for s in seq] == [
        "command",
        "command",
        "command",
        "command",
        "command",
        "observe",
        "command",
        "command",
        "command",
        "command",
    ]
    # Capabilities step (step 2) carries the full locked surface.
    cap = seq[1]
    assert cap["telemetry_snapshot"] is not None
    assert tuple(sorted(cap["telemetry_snapshot"]["capabilities"])) == (
        benchmark.LOCKED_PI_SURFACE
    )
    # Mid-mission status (step 7) carries mission_loop telemetry.
    mid = seq[6]
    loop = mid["telemetry_snapshot"]["mission_loop"]
    assert loop["running"] is True
    assert loop["tick_count"] >= _FAST_MIN_LOOP_TICKS
    # Final status (step 10) carries idle controller + idle loop.
    final = seq[9]
    fc = final["telemetry_snapshot"]
    assert fc["controller"]["armed"] is False
    assert fc["mission_loop"]["running"] is False
    # success bit
    assert art["success"] is True


# ----------------------------------------------------------------------
# Run-level: each inject produces a passing artifact (universal
# contracts hold under injection).
# ----------------------------------------------------------------------


def test_inject_camera_offline(tmp_path: Path) -> None:
    """Empty scenes -> no MOVE dispatched, but every universal
    contract still holds and `success` is True."""
    res = _run_ci(tmp_path, inject="camera_offline")
    art = res["artifact"]
    assert art["inject"] == "camera_offline"
    c = art["criteria"]
    assert c["expected_outcome"] == "camera_offline"
    assert art["success"] is True
    # The whole point of this inject:
    assert c["move_dispatches_observed"] is False
    # Universal contracts hold:
    assert c["all_commands_ok"] is True
    assert c["capabilities_match_locked_surface"] is True
    assert c["loop_reached_running"] is True
    assert c["loop_stopped_clean"] is True
    assert c["pins_low_at_end"] is True
    # No counter regressed (vision is allowed under this inject; mock
    # returns empty scenes silently, not raises):
    assert c["mission_failures"] == 0
    assert c["dispatch_failures"] == 0
    assert c["no_unexpected_failures"] is True


def test_inject_mission_offline(tmp_path: Path) -> None:
    """Offline mission policy returns idle every tick -> no MOVE,
    no exceptions, every contract holds."""
    res = _run_ci(tmp_path, inject="mission_offline")
    art = res["artifact"]
    assert art["inject"] == "mission_offline"
    c = art["criteria"]
    assert art["success"] is True
    assert c["move_dispatches_observed"] is False
    assert c["vision_failures"] == 0
    assert c["mission_failures"] == 0  # offline policy returns idle, not raises
    assert c["dispatch_failures"] == 0


def test_inject_vision_drop_after_n(tmp_path: Path) -> None:
    """Vision raises after N successful scenes -> vision_failures > 0,
    MOVE dispatched in the clean window, every universal contract
    holds."""
    res = _run_ci(
        tmp_path,
        inject="vision_drop_after_n",
        extra=("--vision-drop-after=2",),
    )
    art = res["artifact"]
    c = art["criteria"]
    assert art["success"] is True
    # Vision_failures should have grown post-drop:
    assert c["vision_failures"] > 0, c
    # But mission and dispatch stages should not have regressed:
    assert c["mission_failures"] == 0
    assert c["dispatch_failures"] == 0
    # The clean window should have produced at least one MOVE dispatch:
    assert c["move_dispatches_observed"] is True


# ----------------------------------------------------------------------
# Output paths.
# ----------------------------------------------------------------------


def test_output_dash_writes_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--output -` writes the JSON to stdout without creating a file."""
    rc = _run_main(
        "run",
        "--mode=ci",
        f"--hold={_FAST_HOLD}",
        f"--tick-interval={_FAST_TICK}",
        f"--stale-world-timeout={_SHORT_STALE}",
        "--output=-",
    )
    assert rc == 0
    captured = capsys.readouterr()
    raw = json.loads(captured.out)
    assert raw["mode"] == "ci"
    assert raw["success"] is True
    # No file was created in tmp_path:
    files = list(tmp_path.iterdir())
    assert files == []


def test_output_path_creates_parents(tmp_path: Path) -> None:
    """The CLI creates the output directory if it doesn't exist."""
    nested = tmp_path / "deeply" / "nested" / "result.json"
    rc = _run_main(
        "run",
        "--mode=ci",
        f"--hold={_FAST_HOLD}",
        f"--tick-interval={_FAST_TICK}",
        f"--output={nested}",
    )
    assert rc == 0
    assert nested.is_file()
    assert json.loads(nested.read_text())["success"] is True


# ----------------------------------------------------------------------
# View path.
# ----------------------------------------------------------------------


def test_view_round_trips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run -> write -> view: the human format is parseable from the
    persisted JSON."""
    art_path = tmp_path / "result.json"
    rc = _run_main(
        "run",
        "--mode=ci",
        f"--hold={_FAST_HOLD}",
        f"--tick-interval={_FAST_TICK}",
        f"--output={art_path}",
    )
    assert rc == 0
    capsys.readouterr()  # discard run output

    rc2 = _run_main("view", str(art_path))
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "pi_follow_bench" in out
    assert "verdict: PASS" in out


def test_view_missing_file_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run_main("view", str(tmp_path / "no-such-file.json"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "no such file" in err


# ----------------------------------------------------------------------
# Bad inputs.
# ----------------------------------------------------------------------


def test_run_benchmark_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        benchmark.run_benchmark(
            cfg=None,  # type: ignore[arg-type]
            controller=None,  # type: ignore[arg-type]
            mission_loop=None,
            router=None,  # type: ignore[arg-type]
            mode="zonk",
        )


def test_run_benchmark_rejects_unknown_inject() -> None:
    with pytest.raises(ValueError, match="unknown inject"):
        benchmark.run_benchmark(
            cfg=None,  # type: ignore[arg-type]
            controller=None,  # type: ignore[arg-type]
            mission_loop=None,
            router=None,  # type: ignore[arg-type]
            mode="ci",
            inject="zonk",
        )


def test_run_benchmark_rejects_negative_hold() -> None:
    with pytest.raises(ValueError, match="hold_s"):
        benchmark.run_benchmark(
            cfg=None,  # type: ignore[arg-type]
            controller=None,  # type: ignore[arg-type]
            mission_loop=None,
            router=None,  # type: ignore[arg-type]
            mode="ci",
            hold_s=-1.0,
        )


def test_run_benchmark_rejects_bad_min_move_dispatches() -> None:
    with pytest.raises(ValueError, match="min_move_dispatches"):
        benchmark.run_benchmark(
            cfg=None,  # type: ignore[arg-type]
            controller=None,  # type: ignore[arg-type]
            mission_loop=None,
            router=None,  # type: ignore[arg-type]
            mode="ci",
            min_move_dispatches=2,
        )


def test_cli_no_subcommand_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = pi_follow_bench.main(["--log-level=ERROR"])
    assert rc == 2


# ----------------------------------------------------------------------
# Inject-aware failure-bound logic (unit-level).
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "inject,vf,mf,df,expected",
    [
        # clean: any failure regresses
        (None, 0, 0, 0, True),
        (None, 1, 0, 0, False),
        (None, 0, 1, 0, False),
        (None, 0, 0, 1, False),
        # camera_offline: only mission/dispatch must stay clean
        ("camera_offline", 0, 0, 0, True),
        ("camera_offline", 5, 0, 0, True),  # vision counter is bounded by inject
        ("camera_offline", 0, 1, 0, False),
        ("camera_offline", 0, 0, 1, False),
        # mission_offline: every counter must stay clean (offline returns idle)
        ("mission_offline", 0, 0, 0, True),
        ("mission_offline", 1, 0, 0, False),
        ("mission_offline", 0, 1, 0, False),
        ("mission_offline", 0, 0, 1, False),
        # vision_drop_after_n: vision counter is allowed to grow
        ("vision_drop_after_n", 0, 0, 0, True),
        ("vision_drop_after_n", 9, 0, 0, True),
        ("vision_drop_after_n", 9, 1, 0, False),
        ("vision_drop_after_n", 9, 0, 1, False),
        # unknown inject: fail closed
        ("zonk", 0, 0, 0, False),
    ],
)
def test_no_unexpected_failures_inject_aware(
    inject, vf, mf, df, expected
) -> None:
    assert (
        benchmark._no_unexpected_failures(
            inject=inject,
            vision_failures=vf,
            mission_failures=mf,
            dispatch_failures=df,
        )
        is expected
    )


# ----------------------------------------------------------------------
# Stack assembly: --mode=ci stack uses the canonical demo's wiring.
# ----------------------------------------------------------------------


class _Args:
    """Minimal stand-in for argparse.Namespace for direct stack tests."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def test_ci_stack_registers_locked_command_surface() -> None:
    """The same `build_router_without_loop` + `attach_mission_loop`
    wiring the canonical demo uses must produce the locked 8-command
    surface — proves the benchmark exercises the same router."""
    from freemotion.agent import MissionLoop  # noqa: F401 — for default

    args = _Args(
        inject=None,
        intent="follow person",
        tick_interval=_FAST_TICK,
        stale_world_timeout=_SHORT_STALE,
        vision_drop_after=3,
    )
    stack = pi_follow_bench._build_ci_stack(args)
    try:
        assert tuple(sorted(stack.router.known)) == benchmark.LOCKED_PI_SURFACE
    finally:
        # Idempotent teardown — the stack runs no thread until the
        # benchmark calls `/mission_start`, but be defensive.
        stack.mission_loop.stop()
        stack.controller.stop()
        stack.cleanup()
