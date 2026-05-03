"""Smoke test for examples/local_sim_demo.py.

The demo is a contract: a stranger should be able to clone the repo
and run it without setup. If anything below breaks, that contract is
broken.
"""

from __future__ import annotations

import io
import runpy
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = REPO_ROOT / "examples" / "local_sim_demo.py"


def _run_demo() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            runpy.run_path(str(DEMO_PATH), run_name="__main__")
        except SystemExit as exc:
            assert exc.code in (None, 0), f"demo exited with {exc.code!r}"
    return buf.getvalue()


def test_demo_file_exists() -> None:
    assert DEMO_PATH.is_file()


def test_demo_runs_to_completion() -> None:
    output = _run_demo()
    assert "done" in output
    assert "follow: person at confidence" in output
    assert "explicit stop intent" in output
    assert "explicit disarm intent" in output
    assert "unknown intent: 'party time'" in output


def test_demo_round_trips_protocol_envelopes() -> None:
    output = _run_demo()
    assert '"cmd":"arm"' in output
    assert '"cmd":"move"' in output
    assert '"cmd":"stop"' in output
    assert '"cmd":"disarm"' in output
    assert '"ok":true' in output


def test_demo_state_changes_through_run() -> None:
    output = _run_demo()
    assert "'armed': True" in output
    assert "'position': [1.0, 0.0, 0.0]" in output
    assert "'armed': False" in output


def test_demo_threads_world_state_through_loop() -> None:
    output = _run_demo()
    assert "WorldStateSnapshot" in output
    assert "target='person'" in output
    assert "current_state='moving'" in output or "current_state='armed'" in output
    assert "next_action='move'" in output


def test_demo_does_not_require_env() -> None:
    """Demo must not rely on TELEGRAM_BOT_TOKEN or any other env var."""
    saved = {
        k: sys.modules.pop(k, None)
        for k in [
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_ALLOWED_CHAT_IDS",
            "FREEMOTION_DEVICE_ID",
        ]
    }
    try:
        _run_demo()
    finally:
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v
