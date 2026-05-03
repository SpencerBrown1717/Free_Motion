"""Free Motion vision.

Today: a `VisionBackend` Protocol, a `MockVision` implementation, and
a real `YoloVision` adapter (post-M4) gated behind
`FREEMOTION_VISION_BACKEND=yolo` and a `pip install -e .[yolo]` extra.

`make_vision_from_config` is the runtime factory: given a `Config`, it
returns the backend matching `config.vision_backend`. The YOLO backend
is imported lazily so this package stays importable on any host.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from .interface import Detection, VisionBackend, VisionResult
from .mock import MockVision
from .picamera import PiCameraSource
from .yolo import YoloVision

if TYPE_CHECKING:  # pragma: no cover
    from freemotion.config import Config

LOG = logging.getLogger("freemotion.vision")

__all__ = [
    "Detection",
    "MockVision",
    "PiCameraSource",
    "VisionBackend",
    "VisionResult",
    "YoloVision",
    "make_vision_from_config",
]


def make_vision_from_config(
    config: "Config",
    *,
    frame_source: Optional[Callable[[], Any]] = None,
) -> VisionBackend:
    """Pick a `VisionBackend` for `config.vision_backend`.

    - ``"yolo"``: lazy-imports `YoloVision`. Construction takes the
      backend's defaults (person-only, ``yolov8n.pt``, 0.25
      confidence). Pass `frame_source=` to wire a live frame producer
      (e.g. `PiCameraSource()`); without it `YoloVision` runs in
      demo-fixture mode where `scene()` always returns no detections
      until a frame source is provided.
    - ``"mock"`` (or unset / unknown): `MockVision` with no script.
      `frame_source` is ignored — the mock has its own scripted
      sequence.
      Unknown values log a warning so misconfiguration is visible.
    """
    backend = (config.vision_backend or "").strip().lower()
    if backend == "yolo":
        if frame_source is not None:
            return YoloVision(frame_source=frame_source)
        return YoloVision()
    if backend not in {"", "mock"}:
        LOG.warning(
            "unknown FREEMOTION_VISION_BACKEND=%r; falling back to MockVision",
            backend,
        )
    return MockVision()
