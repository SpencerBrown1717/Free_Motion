from __future__ import annotations

from freemotion.mission_control import (
    MissionDecision,
    MissionPolicy,
    MockMissionControl,
)
from freemotion.protocol import CommandName
from freemotion.vision import Detection, VisionResult
from freemotion.world import WorldStateSnapshot

EMPTY_SCENE = VisionResult(detections=())
EMPTY_WORLD = WorldStateSnapshot()


def test_mock_satisfies_protocol() -> None:
    m = MockMissionControl()
    assert isinstance(m, MissionPolicy)
    assert m.name == "mock"
    assert m.available is True


def test_stop_intent() -> None:
    d = MockMissionControl().plan(
        intent="stop", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.STOP
    assert d.confidence == 1.0


def test_stop_intent_is_case_insensitive() -> None:
    d = MockMissionControl().plan(
        intent="HALT", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.STOP


def test_abort_intent() -> None:
    d = MockMissionControl().plan(
        intent="abort", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.STOP


def test_disarm_intent() -> None:
    d = MockMissionControl().plan(
        intent="disarm", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.DISARM


def test_land_intent_disarms() -> None:
    d = MockMissionControl().plan(
        intent="land", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.DISARM


def test_follow_with_no_person_is_idle() -> None:
    d = MockMissionControl().plan(
        intent="follow person", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command is None
    assert d.confidence == 0.0
    assert "no person" in d.reason


def test_follow_with_person_moves_forward() -> None:
    scene = VisionResult(
        detections=(Detection("person", 0.85, (0.4, 0.4, 0.2, 0.4)),)
    )
    d = MockMissionControl().plan(
        intent="follow", scene=scene, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.MOVE
    assert d.args == {"x": 1.0, "y": 0.0, "z": 0.0}
    assert abs(d.confidence - 0.85) < 1e-6


def test_follow_picks_highest_confidence_person() -> None:
    scene = VisionResult(
        detections=(
            Detection("person", 0.50, (0, 0, 1, 1)),
            Detection("person", 0.95, (0, 0, 1, 1)),
            Detection("obstacle", 0.99, (0, 0, 1, 1)),
        )
    )
    d = MockMissionControl().plan(
        intent="follow", scene=scene, world=EMPTY_WORLD
    )
    assert d.next_command == CommandName.MOVE
    assert abs(d.confidence - 0.95) < 1e-6


def test_unknown_intent_is_idle() -> None:
    d = MockMissionControl().plan(
        intent="party time", scene=EMPTY_SCENE, world=EMPTY_WORLD
    )
    assert d.next_command is None
    assert d.confidence == 0.0
    assert "party time" in d.reason


def test_accepts_populated_world_without_using_it() -> None:
    """Mock policy doesn't read world today, but must accept any snapshot."""
    populated = WorldStateSnapshot(
        target="person", current_state="armed", confidence=0.9
    )
    d = MockMissionControl().plan(
        intent="stop", scene=EMPTY_SCENE, world=populated
    )
    assert d.next_command == CommandName.STOP


def test_decision_is_immutable() -> None:
    d = MissionDecision(next_command=None)
    try:
        d.reason = "x"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("MissionDecision should be frozen")
