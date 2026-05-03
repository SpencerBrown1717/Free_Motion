"""Smoke tests for examples/pipe_check.

These intentionally avoid network and Telegram mocking. They cover the
pure helpers and the no-op LED path so CI catches regressions in the
plumbing without needing a bot token or a Pi.
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPE_CHECK_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "examples", "pipe_check")
)
if _PIPE_CHECK_DIR not in sys.path:
    sys.path.insert(0, _PIPE_CHECK_DIR)

import pipe_check  # noqa: E402


class _FakeChat:
    def __init__(self, cid: int) -> None:
        self.id = cid


class _FakeUpdate:
    def __init__(self, cid: int) -> None:
        self.effective_chat = _FakeChat(cid)


def test_parse_chat_ids_empty() -> None:
    assert pipe_check._parse_chat_ids("") == set()


def test_parse_chat_ids_basic() -> None:
    assert pipe_check._parse_chat_ids("123, 456") == {123, 456}


def test_parse_chat_ids_ignores_non_integers() -> None:
    assert pipe_check._parse_chat_ids("123, abc, 456") == {123, 456}


def test_is_authorized_open_when_allowlist_empty() -> None:
    assert pipe_check.is_authorized(_FakeUpdate(999), set()) is True


def test_is_authorized_allows_match() -> None:
    assert pipe_check.is_authorized(_FakeUpdate(7), {7, 8}) is True


def test_is_authorized_blocks_nonmatch() -> None:
    assert pipe_check.is_authorized(_FakeUpdate(9), {7, 8}) is False


def test_led_no_pin_is_not_available() -> None:
    led = pipe_check.Led(None)
    assert led.available is False
    led.set(True)
    led.cleanup()


def test_config_from_env_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        pipe_check.Config.from_env()


def test_config_from_env_parses_allowlist_and_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1, 2, 3")
    monkeypatch.setenv("FREEMOTION_LED_PIN", "17")
    cfg = pipe_check.Config.from_env()
    assert cfg.token == "token"
    assert cfg.allowed_chat_ids == {1, 2, 3}
    assert cfg.led_pin == 17
