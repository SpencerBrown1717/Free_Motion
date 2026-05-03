"""Tests for examples/pi_camera_demo/pi_camera_demo.py.

CI-clean: on a non-Pi host without picamera2 installed, the demo's
`main()` must exit with code 2 (camera offline) — not crash. We
monkeypatch `freemotion.vision.PiCameraSource` to drive both the
"camera offline" and "happy path" exit codes without needing real
hardware.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, List

import pytest

DEMO_DIR = Path(__file__).resolve().parent.parent / "examples" / "pi_camera_demo"


@pytest.fixture(scope="module")
def pi_camera_demo() -> Any:
    """Import the demo module from the example directory.

    Same trick as tests/test_pi_bench_demo.py: prepend the example
    directory to sys.path so `import pi_camera_demo` resolves."""
    sys.path.insert(0, str(DEMO_DIR))
    try:
        if "pi_camera_demo" in sys.modules:
            del sys.modules["pi_camera_demo"]
        mod = importlib.import_module("pi_camera_demo")
        yield mod
    finally:
        sys.path.remove(str(DEMO_DIR))
        sys.modules.pop("pi_camera_demo", None)


# -- smoke ------------------------------------------------------------


def test_demo_imports_without_picamera2(pi_camera_demo: Any) -> None:
    assert callable(pi_camera_demo.main)


# -- main() exit codes ------------------------------------------------


class _FakeCam:
    name = "picamera"

    def __init__(self, *, available: bool) -> None:
        self._available = available
        self.closed = False
        self.capture_failures = 0

    @property
    def available(self) -> bool:
        return self._available and not self.closed

    def __call__(self) -> Any:
        return object()

    def close(self) -> None:
        self.closed = True


class _FakeVision:
    """Stand-in for YoloVision."""

    DEFAULT_CONFIDENCE = 0.25
    DEFAULT_MODEL = "yolov8n.pt"

    def __init__(self, *, available: bool, scenes: int = 0) -> None:
        self._available = available
        self._scenes_remaining = scenes
        self.calls = 0

    @property
    def available(self) -> bool:
        return self._available

    def scene(self) -> Any:
        self.calls += 1
        from freemotion.vision import VisionResult

        return VisionResult(detections=())


def _run_main_with_argv(
    pi_camera_demo: Any,
    monkeypatch: pytest.MonkeyPatch,
    argv: List[str],
) -> int:
    monkeypatch.setattr(sys, "argv", ["pi_camera_demo.py", *argv])
    return pi_camera_demo.main()


def test_main_returns_2_when_camera_offline(
    pi_camera_demo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pi_camera_demo,
        "PiCameraSource",
        lambda **_: _FakeCam(available=False),
    )

    rc = _run_main_with_argv(pi_camera_demo, monkeypatch, ["--max-ticks", "1"])
    assert rc == 2


def test_main_returns_3_when_yolo_offline(
    pi_camera_demo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cam = _FakeCam(available=True)
    monkeypatch.setattr(
        pi_camera_demo, "PiCameraSource", lambda **_: cam
    )
    monkeypatch.setattr(
        pi_camera_demo,
        "YoloVision",
        lambda **_: _FakeVision(available=False),
    )

    rc = _run_main_with_argv(pi_camera_demo, monkeypatch, ["--max-ticks", "1"])
    assert rc == 3
    # The demo must close the camera even when YOLO is the failing
    # layer — otherwise we'd leak a hardware handle.
    assert cam.closed is True


def test_main_runs_loop_and_exits_zero_on_max_ticks(
    pi_camera_demo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cam = _FakeCam(available=True)
    vision = _FakeVision(available=True)
    monkeypatch.setattr(
        pi_camera_demo, "PiCameraSource", lambda **_: cam
    )
    monkeypatch.setattr(
        pi_camera_demo, "YoloVision", lambda **_: vision
    )

    rc = _run_main_with_argv(
        pi_camera_demo,
        monkeypatch,
        ["--max-ticks", "3", "--interval", "0"],
    )
    assert rc == 0
    assert vision.calls == 3
    assert cam.closed is True


def test_systemd_unit_present() -> None:
    """The demo ships a user-level systemd unit so contributors can
    autostart it with the same pattern as `pipe_check` and
    `pi_bench_demo`."""
    unit = DEMO_DIR / "systemd" / "freemotion-pi-camera-demo.service"
    assert unit.is_file()
    contents = unit.read_text()
    assert "ExecStart" in contents
    assert "pi_camera_demo.py" in contents


def test_readme_present() -> None:
    readme = DEMO_DIR / "README.md"
    assert readme.is_file()
    text = readme.read_text()
    # README must at least name the demo and link to the canonical doc.
    assert "pi_camera_demo" in text
    assert "PiCameraSource" in text
