"""Tests for freemotion.hardware.mock."""

from __future__ import annotations

from freemotion.hardware import HardwareController, MockHardwareController


def test_mock_satisfies_protocol() -> None:
    m = MockHardwareController()
    assert isinstance(m, HardwareController)


def test_mock_initial_state() -> None:
    m = MockHardwareController()
    s = m.state()
    assert s["armed"] is False
    assert s["position"] == [0.0, 0.0, 0.0]
    assert s["altitude"] == 0.0
    assert s["connected"] is True
    assert s["battery"] == 100.0


def test_mock_arm_succeeds_with_battery() -> None:
    m = MockHardwareController()
    assert m.arm() is True
    assert m.state()["armed"] is True


def test_mock_arm_fails_with_low_battery() -> None:
    m = MockHardwareController(battery_start=5.0)
    assert m.arm() is False
    assert m.state()["armed"] is False


def test_mock_disarm_idempotent() -> None:
    m = MockHardwareController()
    m.arm()
    m.disarm()
    m.disarm()
    assert m.state()["armed"] is False


def test_mock_stop_idle_in_any_state() -> None:
    m = MockHardwareController()
    m.stop()
    assert m.state()["armed"] is False
    m.arm()
    m.stop()
    assert m.state()["armed"] is False


def test_mock_move_fails_when_not_armed() -> None:
    m = MockHardwareController()
    assert m.move(1.0, 0.0, 0.0) is False
    assert m.state()["position"] == [0.0, 0.0, 0.0]


def test_mock_move_updates_position_when_armed() -> None:
    m = MockHardwareController()
    m.arm()
    assert m.move(1.0, 2.0, 3.0) is True
    assert m.state()["position"] == [1.0, 2.0, 3.0]
    assert m.state()["altitude"] == 3.0


def test_mock_move_drains_battery() -> None:
    m = MockHardwareController()
    m.arm()
    before = m.state()["battery"]
    m.move(10.0, 0.0, 0.0)
    after = m.state()["battery"]
    assert after < before


def test_mock_move_fails_when_battery_insufficient() -> None:
    m = MockHardwareController(
        battery_start=5.0,
        battery_arm_cost=0.0,
        min_battery_to_arm=0.0,
    )
    assert m.arm() is True
    assert m.move(100.0, 0.0, 0.0) is False
    assert m.state()["position"] == [0.0, 0.0, 0.0]
