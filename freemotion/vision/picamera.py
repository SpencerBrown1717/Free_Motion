"""Pi camera frame source (Step 1 — Pi live camera integration).

Canonical Raspberry Pi camera adapter for the existing
`YoloVision(frame_source=...)` seam. v1 scope per ADR-0009:

- One source class, `PiCameraSource`, callable like a frame producer:
  ``cam()`` returns the latest captured frame as a numpy array, or
  ``None`` if the camera is offline or capture failed. That's the
  exact shape `YoloVision` already expects.
- Backed by ``picamera2`` (the modern libcamera-based stack on
  Bullseye+/Bookworm Pi OS). USB webcam users do not need this
  module — they can pass an `cv2.VideoCapture(0).read()`-shaped
  callable straight into `YoloVision`.
- Heavy/Pi-only deps live behind ``pip install -e .[picam]``.
  ``picamera2`` is imported **lazily** inside ``__init__`` so this
  module is safe to import on any host (CI, dev laptop, Jetson).
- Failure model, in order:
  1. ``picamera2`` not installed → source is offline; ``cam()``
     returns ``None``.
  2. Camera open / configure / start raises → offline; same.
  3. Per-call capture raises → returns ``None`` for that frame
     and stays available for the next call (one bad frame must
     not flip the source permanently offline).
  4. ``close()`` is idempotent and never raises.

The agent loop never sees a camera-induced exception.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional, Tuple

LOG = logging.getLogger("freemotion.vision.picamera")


class PiCameraSource:
    """Live frame producer backed by an Ultralytics-friendly Pi camera.

    Use as the ``frame_source`` for `YoloVision`:

    .. code-block:: python

        cam = PiCameraSource()
        try:
            vision = YoloVision(frame_source=cam, classes=["person"])
            result = vision.scene()
        finally:
            cam.close()

    Construction never raises on missing dependencies or failed camera
    init. If anything goes wrong, the source stays offline:
    ``available is False`` and ``cam()`` returns ``None``. Callers
    (typically `YoloVision`) already treat ``None`` as "no frame this
    tick" and return an empty `VisionResult`.
    """

    name = "picamera"

    DEFAULT_RESOLUTION: Tuple[int, int] = (640, 480)

    def __init__(
        self,
        *,
        resolution: Tuple[int, int] = DEFAULT_RESOLUTION,
        picam_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._resolution: Tuple[int, int] = (
            int(resolution[0]),
            int(resolution[1]),
        )

        # `_lock` only guards `_picam` / `_ready` flips during start
        # and close. Per-frame capture deliberately does **not** hold
        # it: a slow grab must not block `close()` or `available`
        # readers.
        self._lock = threading.Lock()
        self._picam: Any = None
        self._ready = False
        self._closed = False
        self._capture_failures = 0

        if picam_factory is None:
            try:
                from picamera2 import Picamera2  # type: ignore[import-not-found]

                picam_factory = lambda: Picamera2()  # noqa: E731
            except Exception as exc:  # pragma: no cover - non-Pi path
                LOG.warning(
                    "picamera2 unavailable (%s); PiCameraSource is offline. "
                    "Install with `pip install -e .[picam]` on a Pi running "
                    "Bullseye or newer.",
                    exc,
                )
                return

        try:
            picam = picam_factory()
        except Exception as exc:
            LOG.warning(
                "Pi camera open failed (%s); PiCameraSource is offline", exc
            )
            return

        try:
            # picamera2's `create_preview_configuration` is the right
            # mode for continuous capture. `capture_array()` reads the
            # current frame as an ndarray; YOLO accepts it directly.
            configure = getattr(picam, "configure", None)
            create_cfg = getattr(picam, "create_preview_configuration", None)
            if callable(create_cfg) and callable(configure):
                cfg = create_cfg({"size": self._resolution})
                configure(cfg)
            start = getattr(picam, "start", None)
            if callable(start):
                start()
        except Exception as exc:
            LOG.warning(
                "Pi camera start failed (%s); PiCameraSource is offline", exc
            )
            self._safe_close(picam)
            return

        self._picam = picam
        self._ready = True

    @property
    def available(self) -> bool:
        return self._ready and not self._closed

    @property
    def resolution(self) -> Tuple[int, int]:
        return self._resolution

    @property
    def capture_failures(self) -> int:
        """Total per-call capture failures since construction. Surfaces in
        `/status` telemetry so operators can see a flaky camera without
        scraping logs."""
        return self._capture_failures

    def __call__(self) -> Optional[Any]:
        """Capture the next frame.

        Returns the frame as the camera's native ndarray, or ``None`` on
        any failure (camera offline, closed, or capture exception). One
        bad frame does **not** flip the source offline — the next call
        retries. That matches the way real cameras drop the occasional
        frame without us pretending the device is gone.
        """
        if not self.available or self._picam is None:
            return None

        capture = getattr(self._picam, "capture_array", None)
        if not callable(capture):
            return None

        try:
            frame = capture()
        except Exception as exc:
            self._capture_failures += 1
            LOG.warning("Pi camera capture failed: %s", exc)
            return None

        # picamera2 returns a fresh buffer per call; we just hand it
        # straight back to the caller. YOLO and OpenCV both accept
        # ndarrays directly.
        return frame

    def close(self) -> None:
        """Release the camera. Idempotent and never raises."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._ready = False
            picam, self._picam = self._picam, None
        self._safe_close(picam)

    @staticmethod
    def _safe_close(picam: Any) -> None:
        if picam is None:
            return
        for method_name in ("stop", "close"):
            method = getattr(picam, method_name, None)
            if not callable(method):
                continue
            try:
                method()
            except Exception as exc:  # pragma: no cover - hardware-specific
                LOG.warning(
                    "PiCameraSource: %s() raised during close: %s",
                    method_name,
                    exc,
                )

    def __enter__(self) -> "PiCameraSource":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
