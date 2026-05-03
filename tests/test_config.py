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
