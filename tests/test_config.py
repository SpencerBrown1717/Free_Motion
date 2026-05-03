"""Tests for freemotion.config."""

from __future__ import annotations

import dataclasses

import pytest

from freemotion.config import Config
from freemotion.protocol import SafetyMode


def test_from_env_requires_token() -> None:
    with pytest.raises(SystemExit):
        Config.from_env(env={})


def test_from_env_minimum_defaults() -> None:
    cfg = Config.from_env(env={"TELEGRAM_BOT_TOKEN": "abc"})
    assert cfg.token == "abc"
    assert cfg.allowed_chat_ids == frozenset()
    assert cfg.safety_default == SafetyMode.DRY_RUN
    assert cfg.led_pin is None
    assert cfg.hardware_profile == "host"
    assert cfg.enabled_features == frozenset()


def test_from_env_full() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "TELEGRAM_ALLOWED_CHAT_IDS": "1, 2, 3",
            "FREEMOTION_DEVICE_ID": "pi-bench-01",
            "FREEMOTION_SAFETY_DEFAULT": "bench",
            "FREEMOTION_LED_PIN": "17",
            "FREEMOTION_HARDWARE": "pi",
            "FREEMOTION_FEATURES": "vision, mission",
        }
    )
    assert cfg.token == "abc"
    assert cfg.allowed_chat_ids == frozenset({1, 2, 3})
    assert cfg.device_id == "pi-bench-01"
    assert cfg.safety_default == SafetyMode.BENCH
    assert cfg.led_pin == 17
    assert cfg.hardware_profile == "pi"
    assert cfg.enabled_features == frozenset({"vision", "mission"})


def test_from_env_ignores_bad_chat_ids() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "TELEGRAM_ALLOWED_CHAT_IDS": "1, abc, 2",
        }
    )
    assert cfg.allowed_chat_ids == frozenset({1, 2})


def test_from_env_ignores_bad_led_pin() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_LED_PIN": "not-a-number",
        }
    )
    assert cfg.led_pin is None


def test_from_env_falls_back_on_bad_safety() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_SAFETY_DEFAULT": "bogus",
        }
    )
    assert cfg.safety_default == SafetyMode.DRY_RUN


def test_config_is_frozen() -> None:
    cfg = Config.from_env(env={"TELEGRAM_BOT_TOKEN": "abc"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.token = "different"  # type: ignore[misc]


def test_from_env_default_denied_commands_is_empty() -> None:
    cfg = Config.from_env(env={"TELEGRAM_BOT_TOKEN": "abc"})
    assert cfg.denied_commands == frozenset()


def test_from_env_parses_denied_commands() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_DENIED_COMMANDS": "arm, move,led_on",
        }
    )
    assert cfg.denied_commands == frozenset({"arm", "move", "led_on"})


def test_from_env_strips_stop_from_denied_commands(caplog) -> None:
    """`stop` is honored unconditionally per protocol; it cannot be denied."""
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_DENIED_COMMANDS": "arm,stop,move",
        }
    )
    assert "stop" not in cfg.denied_commands
    assert cfg.denied_commands == frozenset({"arm", "move"})


def test_from_env_default_pi_pins_are_none() -> None:
    cfg = Config.from_env(env={"TELEGRAM_BOT_TOKEN": "abc"})
    assert cfg.pi_armed_pin is None
    assert cfg.pi_moving_pin is None


def test_from_env_parses_pi_pins() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_PI_ARMED_PIN": "23",
            "FREEMOTION_PI_MOVING_PIN": "24",
        }
    )
    assert cfg.pi_armed_pin == 23
    assert cfg.pi_moving_pin == 24


def test_from_env_ignores_bad_pi_pin_values() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_PI_ARMED_PIN": "not-a-number",
            "FREEMOTION_PI_MOVING_PIN": "",
        }
    )
    assert cfg.pi_armed_pin is None
    assert cfg.pi_moving_pin is None


def test_from_env_default_vision_backend_is_mock() -> None:
    cfg = Config.from_env(env={"TELEGRAM_BOT_TOKEN": "abc"})
    assert cfg.vision_backend == "mock"


def test_from_env_parses_vision_backend_yolo() -> None:
    cfg = Config.from_env(
        env={
            "TELEGRAM_BOT_TOKEN": "abc",
            "FREEMOTION_VISION_BACKEND": "YOLO",
        }
    )
    assert cfg.vision_backend == "yolo"


def test_from_env_unknown_vision_backend_falls_back_with_warning(caplog) -> None:
    with caplog.at_level("WARNING", logger="freemotion.config"):
        cfg = Config.from_env(
            env={
                "TELEGRAM_BOT_TOKEN": "abc",
                "FREEMOTION_VISION_BACKEND": "midjourney",
            }
        )
    assert cfg.vision_backend == "mock"
    assert any("midjourney" in rec.message for rec in caplog.records)
