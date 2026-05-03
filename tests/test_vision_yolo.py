"""Tests for freemotion.vision.yolo.YoloVision.

CI-clean: all tests inject a `FakeYOLO` via the controller's
`yolo_factory` arg, so the real `ultralytics` / `torch` stack is never
imported. Behavior covered:

- Protocol satisfaction.
- Construction degrades to "offline" cleanly when the YOLO factory
  raises (model missing, weights corrupt, etc.).
- `scene()` returns empty when no `frame_source` is wired, when the
  source returns `None`, when the source raises, and when inference
  raises. The agent loop never sees a vision-induced crash.
- Detections are parsed from the ultralytics shape (`r.boxes.cls /
  conf / xywhn`, `r.names`), with the `xywhn` (center-based)
  → `(x, y, w, h)` (top-left corner-based) conversion locked in
  ADR-0007.
- Confidence threshold is forwarded to the model (caller-side).
- Class filter defaults to `{"person"}` and drops anything else;
  passing an empty iterable accepts every class.
- The `min_interval_s` throttle returns the cached result without
  re-running inference inside the window.
- The `make_vision_from_config` factory returns the right backend.

A trailing `pytest.importorskip("ultralytics")` test boots a real
`YoloVision` only when the optional dep is installed; otherwise it
skips. CI without `[yolo]` should produce a single skip, not a fail.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from freemotion.vision import (
    Detection,
    MockVision,
    VisionBackend,
    VisionResult,
    make_vision_from_config,
)
from freemotion.vision.yolo import YoloVision


class _FakeBoxes:
    """Mimics `ultralytics.engine.results.Boxes` for the slice we read.

    Each list entry is one detection. `cls` is class id, `conf` is a
    float, `xywhn` is a 4-tuple of floats (center-based, normalized).
    """

    def __init__(
        self,
        cls: List[int],
        conf: List[float],
        xywhn: List[Tuple[float, float, float, float]],
    ) -> None:
        self.cls = cls
        self.conf = conf
        self.xywhn = [list(row) for row in xywhn]

    def __len__(self) -> int:
        return len(self.cls)


class _FakeResult:
    def __init__(self, names: Dict[int, str], boxes: Optional[_FakeBoxes]) -> None:
        self.names = names
        self.boxes = boxes


class _FakeYOLO:
    """Stand-in for an `ultralytics.YOLO` instance.

    Construct with the results to return on each call (cycles when
    exhausted, like `MockVision`). Records the `conf=` threshold the
    caller passed so we can assert on it.
    """

    def __init__(
        self,
        results_per_call: List[List[_FakeResult]],
        *,
        raises: Optional[Exception] = None,
    ) -> None:
        self._results = results_per_call
        self._idx = 0
        self.calls: List[Dict[str, Any]] = []
        self.raises = raises

    def __call__(self, frame: Any, *, conf: float, verbose: bool) -> List[_FakeResult]:
        self.calls.append({"frame": frame, "conf": conf, "verbose": verbose})
        if self.raises is not None:
            raise self.raises
        if not self._results:
            return []
        out = self._results[self._idx % len(self._results)]
        self._idx += 1
        return out


def _person_result(conf: float = 0.92) -> List[_FakeResult]:
    """A single 'person' detection at center (0.5, 0.5) sized (0.2, 0.4)."""
    return [
        _FakeResult(
            names={0: "person", 16: "dog"},
            boxes=_FakeBoxes(cls=[0], conf=[conf], xywhn=[(0.5, 0.5, 0.2, 0.4)]),
        )
    ]


def _frame() -> object:
    return object()


# -- protocol + offline construction -----------------------------------


def test_yolo_satisfies_protocol() -> None:
    yv = YoloVision(yolo_factory=lambda path: _FakeYOLO([]))
    assert isinstance(yv, VisionBackend)
    assert yv.name == "yolo"
    assert yv.available is True


def test_yolo_offline_when_factory_raises() -> None:
    def boom(_path: str) -> Any:
        raise RuntimeError("weights missing")

    yv = YoloVision(yolo_factory=boom)
    assert yv.available is False
    assert yv.scene() == VisionResult(detections=())


def test_yolo_default_classes_is_person_only() -> None:
    yv = YoloVision(yolo_factory=lambda path: _FakeYOLO([]))
    assert yv.classes == frozenset({"person"})


def test_yolo_empty_classes_accepts_every_label() -> None:
    yv = YoloVision(classes=[], yolo_factory=lambda path: _FakeYOLO([]))
    assert yv.classes is None


def test_yolo_uses_default_model_path() -> None:
    yv = YoloVision(yolo_factory=lambda path: _FakeYOLO([]))
    assert yv.model_path == YoloVision.DEFAULT_MODEL


def test_yolo_uses_custom_model_path() -> None:
    yv = YoloVision(model="yolov8s.pt", yolo_factory=lambda path: _FakeYOLO([]))
    assert yv.model_path == "yolov8s.pt"


# -- scene() defensive paths -------------------------------------------


def test_scene_empty_when_no_frame_source() -> None:
    yv = YoloVision(yolo_factory=lambda path: _FakeYOLO([_person_result()]))
    assert yv.scene() == VisionResult(detections=())


def test_scene_empty_when_frame_source_returns_none() -> None:
    yv = YoloVision(
        frame_source=lambda: None,
        yolo_factory=lambda path: _FakeYOLO([_person_result()]),
    )
    assert yv.scene() == VisionResult(detections=())


def test_scene_empty_when_frame_source_raises() -> None:
    def boom() -> object:
        raise RuntimeError("camera died")

    fake = _FakeYOLO([_person_result()])
    yv = YoloVision(frame_source=boom, yolo_factory=lambda path: fake)
    assert yv.scene().detections == ()
    assert fake.calls == []


def test_scene_empty_when_inference_raises() -> None:
    fake = _FakeYOLO([], raises=RuntimeError("CUDA OOM"))
    yv = YoloVision(frame_source=_frame, yolo_factory=lambda path: fake)
    assert yv.scene().detections == ()


# -- detection parsing -------------------------------------------------


def test_scene_parses_person_detection() -> None:
    fake = _FakeYOLO([_person_result(conf=0.91)])
    yv = YoloVision(frame_source=_frame, yolo_factory=lambda path: fake)
    result = yv.scene()
    assert isinstance(result, VisionResult)
    assert len(result.detections) == 1
    det = result.detections[0]
    assert isinstance(det, Detection)
    assert det.label == "person"
    assert det.confidence == pytest.approx(0.91)


def test_xywhn_center_to_topleft_corner_conversion() -> None:
    """ADR-0007: bbox is top-left corner-based normalized."""
    fake = _FakeYOLO(
        [
            [
                _FakeResult(
                    names={0: "person"},
                    boxes=_FakeBoxes(
                        cls=[0],
                        conf=[0.8],
                        xywhn=[(0.5, 0.5, 0.2, 0.4)],
                    ),
                )
            ]
        ]
    )
    yv = YoloVision(frame_source=_frame, yolo_factory=lambda path: fake)
    det = yv.scene().detections[0]
    x, y, w, h = det.bbox
    assert x == pytest.approx(0.4)  # 0.5 - 0.2/2
    assert y == pytest.approx(0.3)  # 0.5 - 0.4/2
    assert w == pytest.approx(0.2)
    assert h == pytest.approx(0.4)


def test_bbox_clamped_to_unit_square() -> None:
    """A box whose center sits at the edge can produce a negative
    corner; clamp to 0..1 so callers never see out-of-range values."""
    fake = _FakeYOLO(
        [
            [
                _FakeResult(
                    names={0: "person"},
                    boxes=_FakeBoxes(
                        cls=[0],
                        conf=[0.8],
                        xywhn=[(0.05, 0.05, 0.2, 0.2)],
                    ),
                )
            ]
        ]
    )
    yv = YoloVision(frame_source=_frame, yolo_factory=lambda path: fake)
    x, y, w, h = yv.scene().detections[0].bbox
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(0.0)
    assert w == pytest.approx(0.2)
    assert h == pytest.approx(0.2)


def test_class_filter_drops_non_person_by_default() -> None:
    fake = _FakeYOLO(
        [
            [
                _FakeResult(
                    names={0: "person", 16: "dog"},
                    boxes=_FakeBoxes(
                        cls=[16, 0],
                        conf=[0.95, 0.7],
                        xywhn=[
                            (0.3, 0.3, 0.1, 0.1),
                            (0.6, 0.6, 0.2, 0.4),
                        ],
                    ),
                )
            ]
        ]
    )
    yv = YoloVision(frame_source=_frame, yolo_factory=lambda path: fake)
    dets = yv.scene().detections
    assert [d.label for d in dets] == ["person"]


def test_class_filter_can_be_overridden() -> None:
    fake = _FakeYOLO(
        [
            [
                _FakeResult(
                    names={0: "person", 16: "dog"},
                    boxes=_FakeBoxes(
                        cls=[16, 0],
                        conf=[0.95, 0.7],
                        xywhn=[
                            (0.3, 0.3, 0.1, 0.1),
                            (0.6, 0.6, 0.2, 0.4),
                        ],
                    ),
                )
            ]
        ]
    )
    yv = YoloVision(
        classes=["person", "dog"],
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    labels = sorted(d.label for d in yv.scene().detections)
    assert labels == ["dog", "person"]


def test_empty_class_filter_accepts_unlabeled_class_ids() -> None:
    """If a class id isn't in `r.names`, fall back to the stringified
    id so callers still get *something* — useful for custom-trained
    models that didn't ship a name table."""
    fake = _FakeYOLO(
        [
            [
                _FakeResult(
                    names={},
                    boxes=_FakeBoxes(
                        cls=[7],
                        conf=[0.6],
                        xywhn=[(0.5, 0.5, 0.1, 0.1)],
                    ),
                )
            ]
        ]
    )
    yv = YoloVision(
        classes=[],
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    dets = yv.scene().detections
    assert len(dets) == 1
    assert dets[0].label == "7"


def test_scene_skips_malformed_box_without_crashing() -> None:
    class _BadBoxes:
        cls = [0]
        conf = [0.9]
        xywhn = [None]  # type: ignore[var-annotated]

        def __len__(self) -> int:
            return 1

    fake_results = [_FakeResult(names={0: "person"}, boxes=_BadBoxes())]
    fake = _FakeYOLO([fake_results])  # type: ignore[list-item]
    yv = YoloVision(frame_source=_frame, yolo_factory=lambda path: fake)
    assert yv.scene().detections == ()


# -- confidence threshold passthrough ----------------------------------


def test_confidence_threshold_is_forwarded_to_model() -> None:
    fake = _FakeYOLO([_person_result()])
    yv = YoloVision(
        confidence_threshold=0.5,
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    yv.scene()
    assert fake.calls[-1]["conf"] == pytest.approx(0.5)


def test_confidence_threshold_is_clamped_to_unit_interval() -> None:
    fake = _FakeYOLO([_person_result()])
    yv_low = YoloVision(
        confidence_threshold=-0.5,
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    yv_high = YoloVision(
        confidence_threshold=2.0,
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    yv_low.scene()
    yv_high.scene()
    assert fake.calls[0]["conf"] == 0.0
    assert fake.calls[-1]["conf"] == 1.0


# -- throttle / cache --------------------------------------------------


def test_min_interval_returns_cached_result_without_reinference() -> None:
    fake = _FakeYOLO([_person_result(0.7), _person_result(0.99)])
    yv = YoloVision(
        min_interval_s=10.0,
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    first = yv.scene()
    second = yv.scene()
    assert first is second
    assert len(fake.calls) == 1


def test_zero_interval_re_runs_every_call() -> None:
    fake = _FakeYOLO([_person_result(0.7), _person_result(0.99)])
    yv = YoloVision(
        min_interval_s=0.0,
        frame_source=_frame,
        yolo_factory=lambda path: fake,
    )
    yv.scene()
    yv.scene()
    assert len(fake.calls) == 2


# -- factory -----------------------------------------------------------


class _CfgStub:
    def __init__(self, *, vision_backend: str) -> None:
        self.vision_backend = vision_backend


def test_factory_returns_mock_for_mock_or_default() -> None:
    assert isinstance(make_vision_from_config(_CfgStub(vision_backend="mock")), MockVision)
    assert isinstance(make_vision_from_config(_CfgStub(vision_backend="")), MockVision)


def test_factory_returns_mock_for_unknown_with_warning(caplog) -> None:
    with caplog.at_level("WARNING", logger="freemotion.vision"):
        backend = make_vision_from_config(_CfgStub(vision_backend="midjourney"))
    assert isinstance(backend, MockVision)
    assert any("midjourney" in rec.message for rec in caplog.records)


def test_factory_returns_yolo_when_ultralytics_available(monkeypatch) -> None:
    """Patch the lazy `ultralytics` import so the factory can construct
    a `YoloVision` on a host without the optional dep."""
    import sys
    import types

    fake_pkg = types.ModuleType("ultralytics")
    fake_pkg.YOLO = lambda path: _FakeYOLO([])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", fake_pkg)

    backend = make_vision_from_config(_CfgStub(vision_backend="yolo"))
    assert isinstance(backend, YoloVision)
    assert backend.available is True


# -- real-dep smoke (skips when ultralytics isn't installed) -----------


def test_real_ultralytics_smoke() -> None:
    """If `[yolo]` is installed, confirm the lazy import path works
    end-to-end. The model load is skipped (no weights download in CI);
    we only assert that YoloVision can construct and respond to a
    no-frame_source `scene()` call."""
    pytest.importorskip("ultralytics")
    yv = YoloVision(yolo_factory=lambda path: _FakeYOLO([]))
    assert yv.available is True
    assert yv.scene() == VisionResult(detections=())
