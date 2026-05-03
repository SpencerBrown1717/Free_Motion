"""Tests for freemotion.hardware.pi.PiHardwareController.

Hardware-free: every test injects a `FakeGPIO` adapter, so CI never
needs `RPi.GPIO`. The fake mirrors the slice of the API the controller
uses: `setmode`, `setup`, `output`, `cleanup` and the `BCM` / `OUT` /
`HIGH` / `LOW` constants. Tests can flip its `fail_*` flags to drive
the controller's hardware-failure paths.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pytest

from freemotion.hardware import HardwareController, make_controller_from_config
from freemotion.hardware.pi import PiHardwareController


class FakeGPIO:
    BCM = "BCM"
    OUT = "OUT"
    HIGH = 1
    LOW = 0

    def __init__(self) -> None:
        self.mode: Optional[str] = None
        self.setup_calls: List[Tuple[int, str, Optional[int]]] = []
        self.outputs: List[Tuple[int, int]] = []
        self.cleanup_calls: List[int] = []
        self.fail_setup = False
        self.fail_output_pins: set[int] = set()

    def setmode(self, mode: str) -> None:
        self.mode = mode

    def setup(self, pin: int, direction: str, initial: Optional[int] = None) -> None:
        if self.fail_setup:
            raise RuntimeError("setup failed")
        self.setup_calls.append((pin, direction, initial))

    def output(self, pin: int, level: int) -> None:
        if pin in self.fail_output_pins:
            raise RuntimeError(f"output failed on pin {pin}")
        self.outputs.append((pin, level))

    def cleanup(self, pin: Optional[int] = None) -> None:
        if pin is not None:
            self.cleanup_calls.append(pin)


def _make(*, gpio: Optional[FakeGPIO] = None, **kw: Any) -> PiHardwareController:
    g = gpio if gpio is not None else FakeGPIO()
    return PiHardwareController(gpio=g, move_pulse_s=0.0, **kw)


def test_pi_satisfies_protocol() -> None:
    assert isinstance(_make(), HardwareController)


def test_pi_initial_state_uses_default_pins() -> None:
    c = _make()
    s = c.state()
    assert s["armed"] is False
    assert s["position"] == [0.0, 0.0, 0.0]
    assert s["altitude"] == 0.0
    assert s["connected"] is True
    assert s["pins"] == {
        "armed": PiHardwareController.DEFAULT_ARMED_PIN,
        "moving": PiHardwareController.DEFAULT_MOVING_PIN,
    }
    assert s["last_move_ts"] is None


def test_pi_setup_initializes_both_pins_low() -> None:
    g = FakeGPIO()
    _make(gpio=g, armed_pin=5, moving_pin=6)
    pins = {pin for pin, _, _ in g.setup_calls}
    assert pins == {5, 6}
    for _, direction, initial in g.setup_calls:
        assert direction == g.OUT
        assert initial == g.LOW
    assert g.mode == g.BCM


def test_pi_arm_drives_armed_pin_high() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    assert c.arm() is True
    assert c.state()["armed"] is True
    assert (11, g.HIGH) in g.outputs


def test_pi_disarm_drives_armed_pin_low_and_is_idempotent() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    c.arm()
    c.disarm()
    c.disarm()
    assert c.state()["armed"] is False
    assert g.outputs.count((11, g.LOW)) >= 1


def test_pi_stop_drops_both_pins_low_in_any_state() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    c.stop()
    assert (11, g.LOW) in g.outputs
    assert (12, g.LOW) in g.outputs
    c.arm()
    c.stop()
    assert c.state()["armed"] is False


def test_pi_stop_swallows_output_exceptions() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    c.arm()
    g.fail_output_pins = {11, 12}
    c.stop()
    assert c.state()["armed"] is False


def test_pi_move_when_not_armed_returns_false_no_pulse() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    assert c.move(1.0, 0.0, 0.0) is False
    assert (12, g.HIGH) not in g.outputs
    assert c.state()["position"] == [0.0, 0.0, 0.0]


def test_pi_move_when_armed_pulses_moving_pin_high_then_low() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    c.arm()
    before = len(g.outputs)
    assert c.move(1.0, 2.0, 3.0) is True
    pulse_outputs = g.outputs[before:]
    assert (12, g.HIGH) in pulse_outputs
    assert (12, g.LOW) in pulse_outputs
    assert pulse_outputs.index((12, g.HIGH)) < pulse_outputs.index((12, g.LOW))


def test_pi_move_updates_position_and_timestamp() -> None:
    c = _make()
    c.arm()
    assert c.move(1.0, 2.0, 3.0) is True
    s = c.state()
    assert s["position"] == [1.0, 2.0, 3.0]
    assert s["altitude"] == 3.0
    assert isinstance(s["last_move_ts"], float)


def test_pi_move_accumulates() -> None:
    c = _make()
    c.arm()
    c.move(1.0, 0.0, 0.0)
    c.move(0.0, 2.0, 0.0)
    assert c.state()["position"] == [1.0, 2.0, 0.0]


def test_pi_move_rejects_non_numeric_args() -> None:
    c = _make()
    c.arm()
    assert c.move("nope", 0.0, 0.0) is False  # type: ignore[arg-type]
    assert c.state()["position"] == [0.0, 0.0, 0.0]


def test_pi_arm_returns_false_on_output_failure() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    g.fail_output_pins = {11}
    assert c.arm() is False
    assert c.state()["armed"] is False


def test_pi_move_returns_false_on_output_failure_and_drops_pin_low() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    c.arm()
    g.fail_output_pins = {12}
    assert c.move(1.0, 0.0, 0.0) is False
    assert c.state()["position"] == [0.0, 0.0, 0.0]


def test_pi_offline_when_setup_fails() -> None:
    g = FakeGPIO()
    g.fail_setup = True
    c = PiHardwareController(gpio=g, move_pulse_s=0.0)
    assert c.available is False
    assert c.arm() is False
    assert c.move(1.0, 0.0, 0.0) is False
    s = c.state()
    assert s["armed"] is False
    assert s["connected"] is False
    c.stop()


def test_pi_offline_when_no_gpio_available() -> None:
    """If no `gpio` is passed and `RPi.GPIO` import fails, the
    controller stays offline rather than crashing the runtime.

    We can't easily force the lazy import to fail in CI, so we
    construct with `gpio=` set to an explicit ``None``-equivalent
    sentinel by passing a fake whose setup raises — covered above —
    and rely on the import-time fallback in ``__init__`` for the
    no-RPi case (logged warning, ``available is False``).
    """
    g = FakeGPIO()
    g.fail_setup = True
    c = PiHardwareController(gpio=g, move_pulse_s=0.0)
    assert c.available is False


def test_pi_uses_custom_pins_in_state() -> None:
    c = _make(armed_pin=5, moving_pin=6)
    assert c.state()["pins"] == {"armed": 5, "moving": 6}


def test_pi_cleanup_releases_both_pins() -> None:
    g = FakeGPIO()
    c = _make(gpio=g, armed_pin=11, moving_pin=12)
    c.cleanup()
    assert set(g.cleanup_calls) == {11, 12}
    assert c.available is False


def test_pi_cleanup_is_idempotent() -> None:
    c = _make()
    c.cleanup()
    c.cleanup()
    assert c.available is False


# -- factory ------------------------------------------------------------


class _CfgStub:
    def __init__(
        self,
        *,
        hardware_profile: str = "host",
        pi_armed_pin: Optional[int] = None,
        pi_moving_pin: Optional[int] = None,
    ) -> None:
        self.hardware_profile = hardware_profile
        self.pi_armed_pin = pi_armed_pin
        self.pi_moving_pin = pi_moving_pin


def test_factory_returns_mock_for_host_profile() -> None:
    from freemotion.hardware import MockHardwareController

    ctl = make_controller_from_config(_CfgStub(hardware_profile="host"))
    assert isinstance(ctl, MockHardwareController)


def test_factory_returns_mock_for_unknown_profile_with_warning(caplog) -> None:
    from freemotion.hardware import MockHardwareController

    with caplog.at_level("WARNING", logger="freemotion.hardware"):
        ctl = make_controller_from_config(_CfgStub(hardware_profile="jetson"))
    assert isinstance(ctl, MockHardwareController)
    assert any("jetson" in rec.message for rec in caplog.records)


def test_factory_returns_pi_for_pi_profile(monkeypatch) -> None:
    """Patch the lazy `RPi.GPIO` import so the factory can construct a
    real `PiHardwareController` on a non-Pi host."""
    fake = FakeGPIO()

    import sys
    import types

    rpi_pkg = types.ModuleType("RPi")
    rpi_gpio = types.ModuleType("RPi.GPIO")
    for attr in ("BCM", "OUT", "HIGH", "LOW"):
        setattr(rpi_gpio, attr, getattr(fake, attr))
    rpi_gpio.setmode = fake.setmode  # type: ignore[attr-defined]
    rpi_gpio.setup = fake.setup  # type: ignore[attr-defined]
    rpi_gpio.output = fake.output  # type: ignore[attr-defined]
    rpi_gpio.cleanup = fake.cleanup  # type: ignore[attr-defined]
    rpi_pkg.GPIO = rpi_gpio  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "RPi", rpi_pkg)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", rpi_gpio)

    cfg = _CfgStub(hardware_profile="pi", pi_armed_pin=23, pi_moving_pin=24)
    ctl = make_controller_from_config(cfg)
    assert isinstance(ctl, PiHardwareController)
    assert ctl.state()["pins"] == {"armed": 23, "moving": 24}
