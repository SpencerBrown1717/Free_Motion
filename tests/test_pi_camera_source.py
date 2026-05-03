"""Tests for freemotion.vision.picamera.PiCameraSource.

CI-clean: every test injects a `_FakePicam` via the source's
`picam_factory` arg, so the real `picamera2` / `libcamera` stack is
never imported. Behavior covered:

- Construction degrades to "offline" cleanly when:
  - the factory raises (camera busy / not present),
  - configure raises,
  - start raises (camera in a bad state).
  In every offline case, ``cam()`` returns ``None`` and the agent
  loop is unaffected.
- Per-call capture failures don't latch the source offline; the
  next call retries.
- ``close()`` is idempotent and stops + closes the underlying camera.
- The source is callable, returns the frame the camera handed back,
  and integrates with `YoloVision(frame_source=cam, ...)` exactly
  like a hand-rolled `lambda` would.
- A failed start triggers `stop()` + `close()` on the partially
  initialized camera so we don't leak a hardware handle.

A trailing test boots a real `PiCameraSource` only when `picamera2`
is installed and is happy to construct; otherwise it skips. CI
without `[picam]` produces a single skip, not a failure.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from freemotion.vision import (
    MockVision,
    PiCameraSource,
    VisionBackend,
    VisionResult,
    YoloVision,
)


class _FakePicam:
    """Stand-in for a `picamera2.Picamera2` instance.

    Records every method call so tests can assert on lifecycle order.
    Flip the ``raise_on_*`` knobs to exercise failure paths.
    """

    def __init__(
        self,
        *,
        frames: Optional[List[Any]] = None,
        raise_on_configure: bool = False,
        raise_on_start: bool = False,
        raise_on_capture: bool = False,
        raise_on_stop: bool = False,
    ) -> None:
        self._frames: List[Any] = list(frames or [])
        self._idx = 0
        self.calls: List[str] = []
        self.last_config: Any = None
        self.raise_on_configure = raise_on_configure
        self.raise_on_start = raise_on_start
        self.raise_on_capture = raise_on_capture
        self.raise_on_stop = raise_on_stop

    def create_preview_configuration(self, params: Any) -> Any:
        self.calls.append("create_preview_configuration")
        self.last_config = params
        return {"_cfg": params}

    def configure(self, cfg: Any) -> None:
        self.calls.append("configure")
        if self.raise_on_configure:
            raise RuntimeError("configure failed")

    def start(self) -> None:
        self.calls.append("start")
        if self.raise_on_start:
            raise RuntimeError("start failed")

    def capture_array(self) -> Any:
        self.calls.append("capture_array")
        if self.raise_on_capture:
            raise RuntimeError("capture failed")
        if not self._frames:
            return object()
        out = self._frames[self._idx % len(self._frames)]
        self._idx += 1
        return out

    def stop(self) -> None:
        self.calls.append("stop")
        if self.raise_on_stop:
            raise RuntimeError("stop failed")

    def close(self) -> None:
        self.calls.append("close")


# -- happy path --------------------------------------------------------


def test_source_starts_camera_in_constructor() -> None:
    fake = _FakePicam(frames=["frame-0"])
    cam = PiCameraSource(picam_factory=lambda: fake)
    try:
        assert cam.available is True
        assert cam.name == "picamera"
        # configure ran with the requested resolution; start was called.
        assert "configure" in fake.calls
        assert "start" in fake.calls
        assert fake.last_config == {"size": cam.resolution}
    finally:
        cam.close()


def test_source_default_resolution() -> None:
    cam = PiCameraSource(picam_factory=lambda: _FakePicam())
    try:
        assert cam.resolution == PiCameraSource.DEFAULT_RESOLUTION
    finally:
        cam.close()


def test_source_custom_resolution() -> None:
    fake = _FakePicam()
    cam = PiCameraSource(resolution=(1280, 720), picam_factory=lambda: fake)
    try:
        assert cam.resolution == (1280, 720)
        assert fake.last_config == {"size": (1280, 720)}
    finally:
        cam.close()


def test_source_callable_returns_frame() -> None:
    fake = _FakePicam(frames=["frame-A", "frame-B", "frame-C"])
    cam = PiCameraSource(picam_factory=lambda: fake)
    try:
        assert cam() == "frame-A"
        assert cam() == "frame-B"
        assert cam() == "frame-C"
        # cycles, like MockVision and _FakeYOLO.
        assert cam() == "frame-A"
    finally:
        cam.close()


def test_source_is_a_valid_yolo_frame_source() -> None:
    """The whole point of the seam: drop the source into YoloVision
    without writing a single adapter line."""

    # Minimal in-line fakes that mimic ultralytics' shape — same
    # contract as `_FakeYOLO` / `_FakeBoxes` in test_vision_yolo.py
    # but inlined so this test is self-contained.
    class _FakeBoxes:
        def __init__(self) -> None:
            self.cls = [0]
            self.conf = [0.88]
            self.xywhn = [[0.5, 0.5, 0.2, 0.4]]

        def __len__(self) -> int:
            return 1

    class _FakeResult:
        names = {0: "person"}
        boxes = _FakeBoxes()

    class _FakeYOLO:
        def __call__(self, frame: Any, **_: Any) -> List[Any]:
            self.last_frame = frame
            return [_FakeResult()]

    fake_yolo = _FakeYOLO()
    fake_cam = _FakePicam(frames=["A FRAME OBJECT"])
    cam = PiCameraSource(picam_factory=lambda: fake_cam)
    try:
        vision = YoloVision(frame_source=cam, yolo_factory=lambda _p: fake_yolo)
        result = vision.scene()
    finally:
        cam.close()

    assert isinstance(result, VisionResult)
    assert len(result.detections) == 1
    assert result.detections[0].label == "person"
    assert result.detections[0].confidence == pytest.approx(0.88)
    # And the frame YOLO actually saw came straight from the camera.
    assert fake_yolo.last_frame == "A FRAME OBJECT"


def test_source_close_is_idempotent() -> None:
    fake = _FakePicam()
    cam = PiCameraSource(picam_factory=lambda: fake)
    cam.close()
    cam.close()
    cam.close()
    assert fake.calls.count("stop") == 1
    assert fake.calls.count("close") == 1
    assert cam.available is False


def test_source_call_after_close_returns_none() -> None:
    fake = _FakePicam(frames=["x"])
    cam = PiCameraSource(picam_factory=lambda: fake)
    cam.close()
    assert cam() is None


def test_source_context_manager_closes_on_exit() -> None:
    fake = _FakePicam()
    with PiCameraSource(picam_factory=lambda: fake) as cam:
        assert cam.available is True
    assert "stop" in fake.calls


# -- offline construction paths ---------------------------------------


def test_source_offline_when_factory_raises() -> None:
    def boom() -> Any:
        raise RuntimeError("camera busy")

    cam = PiCameraSource(picam_factory=boom)
    assert cam.available is False
    assert cam() is None


def test_source_offline_when_configure_raises_and_camera_is_closed() -> None:
    """A configure failure means the camera is in a bad state;
    leaving it open would leak a real hardware handle."""
    fake = _FakePicam(raise_on_configure=True)
    cam = PiCameraSource(picam_factory=lambda: fake)
    assert cam.available is False
    assert cam() is None
    # Source must have rolled back: stop + close on the partial camera.
    assert "stop" in fake.calls
    assert "close" in fake.calls


def test_source_offline_when_start_raises_and_camera_is_closed() -> None:
    fake = _FakePicam(raise_on_start=True)
    cam = PiCameraSource(picam_factory=lambda: fake)
    assert cam.available is False
    assert cam() is None
    assert "stop" in fake.calls
    assert "close" in fake.calls


# -- per-call capture failures ----------------------------------------


def test_capture_failure_returns_none_and_increments_counter() -> None:
    fake = _FakePicam(raise_on_capture=True)
    cam = PiCameraSource(picam_factory=lambda: fake)
    try:
        assert cam() is None
        assert cam() is None
        assert cam.capture_failures == 2
        # Must remain available — one bad frame must not flip the
        # source permanently offline.
        assert cam.available is True
    finally:
        cam.close()


def test_capture_failure_does_not_break_subsequent_good_calls() -> None:
    """First call raises, second call (after we drop the raise flag)
    must succeed. This proves the failure path is per-call, not
    sticky."""
    fake = _FakePicam(frames=["good-frame"], raise_on_capture=True)
    cam = PiCameraSource(picam_factory=lambda: fake)
    try:
        assert cam() is None
        fake.raise_on_capture = False
        assert cam() == "good-frame"
        assert cam.capture_failures == 1
    finally:
        cam.close()


def test_capture_when_camera_lacks_capture_array_returns_none() -> None:
    """Defensive path: a custom picam_factory that returns the wrong
    shape must not blow up the agent loop."""

    class _NoCaptureCam:
        # only the bare minimum to pass start-up
        def create_preview_configuration(self, params: Any) -> Any:
            return params

        def configure(self, cfg: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    cam = PiCameraSource(picam_factory=lambda: _NoCaptureCam())
    try:
        assert cam.available is True
        assert cam() is None
    finally:
        cam.close()


# -- close() resilience ------------------------------------------------


def test_close_swallows_stop_exception() -> None:
    fake = _FakePicam(raise_on_stop=True)
    cam = PiCameraSource(picam_factory=lambda: fake)
    cam.close()
    assert cam.available is False


# -- protocol-level sanity -------------------------------------------


def test_mock_vision_remains_default_for_factory() -> None:
    """PiCameraSource lives outside `make_vision_from_config`'s
    contract; the factory still returns MockVision for the default
    config. This guards against accidental coupling."""
    from freemotion.config import Config
    from freemotion.vision import make_vision_from_config

    cfg = Config.from_env(env={"TELEGRAM_BOT_TOKEN": "abc"})
    backend = make_vision_from_config(cfg)
    assert isinstance(backend, MockVision)
    assert isinstance(backend, VisionBackend)


# -- real-dep smoke (skips when picamera2 isn't installed) ------------


def test_real_picamera2_smoke() -> None:
    """If `[picam]` is installed AND a real Pi camera is wired in,
    confirm the lazy-import path is sane. We don't actually capture a
    frame in CI — picamera2 won't initialize without a real camera or
    a v4l2 mock — we just confirm the import path doesn't blow up the
    test runner. If ``picamera2`` itself crashes on import on this
    host, we skip rather than fail."""
    pytest.importorskip("picamera2")
    # We do not call `PiCameraSource()` here: even with picamera2
    # importable, opening the camera will fail on any host that isn't
    # a Pi with a wired-in camera. The structural tests above already
    # cover every code path; this is a "the import path works" gate.
    from freemotion.vision import PiCameraSource as _Cam

    assert _Cam.name == "picamera"
