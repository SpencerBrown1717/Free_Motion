"""YOLO vision backend (post-M4, first real perception adapter).

Implements `VisionBackend` against `ultralytics` YOLO. v1 scope is
intentionally narrow per ADR-0007:

- Person detection by default (configurable via `classes`).
- One model file (default ``yolov8n.pt`` — nano, ~6 MB, CPU-friendly).
- A confidence threshold passed straight through to YOLO's ``conf``.
- A simple `min_interval_s` throttle as the "cheap `scene()`" contract.
- Frames come from a callable the caller injects. The backend does
  **not** own the camera — that decoupling keeps tests trivial and
  lets contributors plug in `cv2.VideoCapture`, `picamera2`, an MJPEG
  stream, or any other source without changing this file.

Heavy deps (`ultralytics`, `torch`) live behind ``pip install -e .[yolo]``.
``ultralytics`` is imported **lazily** inside ``__init__`` so the
module imports cleanly on a host that doesn't have it. Tests inject a
fake YOLO via the ``yolo_factory`` arg.

bbox convention: ``(x, y, w, h)`` normalized to 0..1, **top-left
corner-based**. ``ultralytics`` returns center-based ``xywhn``; we
convert. ADR-0007 locks this convention for the project.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, List, Optional, Tuple

from .interface import Detection, VisionResult

LOG = logging.getLogger("freemotion.vision.yolo")


def _scalar(x: Any) -> float:
    """Pull a Python scalar out of a possibly-tensor value."""
    item = getattr(x, "item", None)
    if callable(item):
        return float(item())
    return float(x)


def _to_list(x: Any) -> List[float]:
    """Convert a possibly-tensor row to a plain list of floats."""
    tolist = getattr(x, "tolist", None)
    if callable(tolist):
        return [float(v) for v in tolist()]
    return [float(v) for v in x]


class YoloVision:
    """`VisionBackend` backed by an Ultralytics YOLO model.

    Construction never raises on missing dependencies or bad model
    paths. If anything goes wrong, the backend stays offline:
    ``available is False`` and ``scene()`` returns an empty result.
    The agent loop never sees a vision-induced crash from this
    backend.
    """

    name = "yolo"

    DEFAULT_MODEL = "yolov8n.pt"
    DEFAULT_CLASSES = frozenset({"person"})
    DEFAULT_CONFIDENCE = 0.25

    def __init__(
        self,
        *,
        frame_source: Optional[Callable[[], Any]] = None,
        model: Optional[str] = None,
        classes: Optional[Iterable[str]] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE,
        min_interval_s: float = 0.0,
        yolo_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._frame_source = frame_source
        self._model_path = model or self.DEFAULT_MODEL
        if classes is None:
            self._classes: Optional[frozenset[str]] = frozenset(
                self.DEFAULT_CLASSES
            )
        elif not classes:
            self._classes = None
        else:
            self._classes = frozenset(classes)
        self._confidence = max(0.0, min(1.0, float(confidence_threshold)))
        self._min_interval_s = max(0.0, float(min_interval_s))

        self._cached: Optional[VisionResult] = None
        self._last_call: Optional[float] = None

        self._model: Any = None
        self._ready = False

        if yolo_factory is None:
            try:
                from ultralytics import YOLO  # type: ignore[import-not-found]

                yolo_factory = lambda path: YOLO(path)  # noqa: E731
            except Exception as exc:  # pragma: no cover - non-yolo path
                LOG.warning(
                    "ultralytics unavailable (%s); YoloVision is offline. "
                    "Install with `pip install -e .[yolo]` on a Python where "
                    "torch is supported.",
                    exc,
                )
                return

        try:
            self._model = yolo_factory(self._model_path)
            self._ready = True
        except Exception as exc:
            LOG.warning(
                "YOLO model load failed (%s); YoloVision is offline", exc
            )
            self._model = None
            self._ready = False

    @property
    def available(self) -> bool:
        return self._ready

    @property
    def model_path(self) -> str:
        return self._model_path

    @property
    def classes(self) -> Optional[frozenset[str]]:
        """The label filter, or `None` if every class is accepted."""
        return self._classes

    def scene(self) -> VisionResult:
        if not self._ready:
            return VisionResult(detections=())

        now = time.monotonic()
        if (
            self._min_interval_s > 0.0
            and self._last_call is not None
            and (now - self._last_call) < self._min_interval_s
            and self._cached is not None
        ):
            return self._cached
        self._last_call = now

        if self._frame_source is None:
            empty = VisionResult(detections=())
            self._cached = empty
            return empty

        try:
            frame = self._frame_source()
        except Exception as exc:
            LOG.warning("YoloVision frame_source raised: %s", exc)
            return VisionResult(detections=())
        if frame is None:
            empty = VisionResult(detections=())
            self._cached = empty
            return empty

        try:
            results = self._model(
                frame, conf=self._confidence, verbose=False
            )
        except Exception as exc:
            LOG.warning("YOLO inference failed: %s", exc)
            return VisionResult(detections=())

        detections = self._extract(results)
        self._cached = VisionResult(detections=detections)
        return self._cached

    def _extract(self, results: Any) -> Tuple[Detection, ...]:
        out: List[Detection] = []
        for r in results:
            names = getattr(r, "names", {}) or {}
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            try:
                count = len(boxes)
            except TypeError:
                continue
            for i in range(count):
                try:
                    cls_id = int(_scalar(boxes.cls[i]))
                    label = names.get(cls_id, str(cls_id))
                    if self._classes is not None and label not in self._classes:
                        continue
                    conf = _scalar(boxes.conf[i])
                    cx, cy, w, h = _to_list(boxes.xywhn[i])
                except Exception as exc:
                    LOG.warning("skipping malformed YOLO box: %s", exc)
                    continue
                # ultralytics xywhn is center-based; convert to top-left.
                x = max(0.0, min(1.0, cx - w / 2.0))
                y = max(0.0, min(1.0, cy - h / 2.0))
                w = max(0.0, min(1.0, w))
                h = max(0.0, min(1.0, h))
                out.append(
                    Detection(
                        label=label,
                        confidence=conf,
                        bbox=(x, y, w, h),
                    )
                )
        return tuple(out)
