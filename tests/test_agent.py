"""Tests for freemotion.agent message handling.

The Telegram side (Application setup, async handlers) is exercised
via `examples/pipe_check/`; here we test the pure logic.
"""

from __future__ import annotations

import json

from freemotion.agent import HELP_TEXT, handle_text, is_authorized
from freemotion.config import Config
from freemotion.protocol import Command, CommandName, Reply, SafetyMode
from freemotion.router import Router


def _cfg(allowed: frozenset[int] = frozenset()) -> Config:
    return Config(
        token="abc",
        allowed_chat_ids=allowed,
        device_id="dev-test",
        safety_default=SafetyMode.BENCH,
    )


def _ok_reply(cmd: Command) -> Reply:
    return Reply(
        sender="dev-test",
        state="idle",
        ok=True,
        error=None,
        telemetry={},
        message="ok",
        correlation_id=cmd.correlation_id,
    )


def _make_router() -> Router:
    r = Router(device_id="dev-test")
    r.register(CommandName.PING, _ok_reply)
    return r


def test_is_authorized_empty_allowlist_is_open() -> None:
    assert is_authorized(123, frozenset()) is True


def test_is_authorized_match() -> None:
    assert is_authorized(123, frozenset({123, 456})) is True


def test_is_authorized_no_match() -> None:
    assert is_authorized(999, frozenset({123, 456})) is False


def test_is_authorized_none_chat_with_allowlist() -> None:
    assert is_authorized(None, frozenset({123})) is False


def test_handle_text_help() -> None:
    out = handle_text(
        text="/help", chat_id=1, config=_cfg(), router=_make_router()
    )
    assert out == HELP_TEXT


def test_handle_text_start_with_args_still_help() -> None:
    out = handle_text(
        text="/start now", chat_id=1, config=_cfg(), router=_make_router()
    )
    assert out == HELP_TEXT


def test_handle_text_plain_echoes_chat_id() -> None:
    out = handle_text(
        text="hello", chat_id=42, config=_cfg(), router=_make_router()
    )
    assert "echo: hello" in out
    assert "chat_id: 42" in out


def test_handle_text_unauthorized_slash_is_string() -> None:
    cfg = _cfg(allowed=frozenset({1}))
    out = handle_text(
        text="/ping", chat_id=999, config=cfg, router=_make_router()
    )
    assert "unauthorized" in out
    assert "999" in out


def test_handle_text_unauthorized_json_returns_envelope() -> None:
    cfg = _cfg(allowed=frozenset({1}))
    payload = json.dumps(
        {
            "v": 0,
            "id": "x",
            "ts": "x",
            "from": "x",
            "cmd": "ping",
            "args": {},
            "safety": "bench",
        }
    )
    out = handle_text(
        text=payload, chat_id=999, config=cfg, router=_make_router()
    )
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "unauthorized"


def test_handle_text_slash_dispatches_to_router() -> None:
    out = handle_text(
        text="/ping", chat_id=1, config=_cfg(), router=_make_router()
    )
    assert out == "ok"


def test_handle_text_json_dispatches_and_returns_envelope() -> None:
    payload = json.dumps(
        {
            "v": 0,
            "id": "abc",
            "ts": "x",
            "from": "x",
            "cmd": "ping",
            "args": {},
            "safety": "bench",
        }
    )
    out = handle_text(
        text=payload, chat_id=1, config=_cfg(), router=_make_router()
    )
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["id"] == "abc"


def test_handle_text_slash_parse_error_returns_string() -> None:
    out = handle_text(
        text="/totallyfake", chat_id=1, config=_cfg(), router=_make_router()
    )
    assert out.startswith("error:")


def test_handle_text_json_parse_error_returns_envelope() -> None:
    out = handle_text(
        text="{not json", chat_id=1, config=_cfg(), router=_make_router()
    )
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "bad_args"
