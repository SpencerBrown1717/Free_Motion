from __future__ import annotations

from freemotion.vision import (
    Detection,
    MockVision,
    VisionBackend,
    VisionResult,
)


def test_mock_satisfies_protocol() -> None:
    m = MockVision()
    assert isinstance(m, VisionBackend)
    assert m.name == "mock"
    assert m.available is True


def test_default_returns_empty_scene() -> None:
    m = MockVision()
    r = m.scene()
    assert r.detections == ()
    assert isinstance(r, VisionResult)


def test_scripted_results_cycle() -> None:
    r1 = VisionResult(
        detections=(Detection("person", 0.9, (0.4, 0.4, 0.2, 0.4)),)
    )
    r2 = VisionResult(detections=())
    m = MockVision(scripted=[r1, r2])

    assert m.scene() == r1
    assert m.scene() == r2
    assert m.scene() == r1
    assert m.scene() == r2


def test_vision_result_default_ts_is_set() -> None:
    r = VisionResult(detections=())
    assert isinstance(r.ts, str)
    assert r.ts


def test_detection_is_immutable() -> None:
    d = Detection("person", 0.9, (0, 0, 1, 1))
    try:
        d.label = "obstacle"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Detection should be frozen")


def test_vision_result_is_immutable() -> None:
    r = VisionResult(detections=())
    try:
        r.detections = ()  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("VisionResult should be frozen")
