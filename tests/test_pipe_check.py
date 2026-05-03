"""Smoke tests for examples/pipe_check.

Protocol, config, router, and agent each have their own test files.
This file just verifies the example imports cleanly, the LED adapter
behaves on a non-Pi host, and the router wiring looks right.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPE_CHECK_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "examples", "pipe_check")
)
if _PIPE_CHECK_DIR not in sys.path:
    sys.path.insert(0, _PIPE_CHECK_DIR)

import pipe_check  # noqa: E402

from freemotion.config import Config  # noqa: E402
from freemotion.protocol import CommandName, SafetyMode  # noqa: E402


def test_pipe_check_imports() -> None:
    assert hasattr(pipe_check, "main")
    assert hasattr(pipe_check, "build_router")
    assert hasattr(pipe_check, "Led")


def test_led_no_pin_is_not_available() -> None:
    led = pipe_check.Led(None)
    assert led.available is False
    led.set(True)
    led.set(False)
    led.cleanup()


def test_build_router_registers_expected_commands() -> None:
    cfg = Config(token="abc", device_id="dev-test", safety_default=SafetyMode.BENCH)
    led = pipe_check.Led(None)
    router = pipe_check.build_router(cfg, led)
    expected = {
        CommandName.PING.value,
        CommandName.STOP.value,
        CommandName.STATUS.value,
        CommandName.CAPABILITIES.value,
        CommandName.LED_ON.value,
        CommandName.LED_OFF.value,
    }
    assert set(router.known) == expected
