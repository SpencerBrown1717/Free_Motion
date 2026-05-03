"""Tests for freemotion.protocol v0."""

from __future__ import annotations

import json

import pytest

from freemotion.protocol import (
    PROTOCOL_VERSION,
    Command,
    CommandName,
    Error,
    ErrorCode,
    ProtocolError,
    Reply,
    SafetyMode,
    parse_command_json,
    parse_reply_json,
    parse_slash,
    serialize_command,
    serialize_reply,
)


def test_protocol_version_is_zero() -> None:
    assert PROTOCOL_VERSION == 0


def test_command_round_trip_minimum_fields() -> None:
    cmd = Command(cmd=CommandName.PING, sender="chat:42", safety=SafetyMode.BENCH)
    parsed = parse_command_json(serialize_command(cmd))
    assert parsed.cmd == CommandName.PING
    assert parsed.sender == "chat:42"
    assert parsed.safety == SafetyMode.BENCH
    assert parsed.target is None
    assert parsed.correlation_id == cmd.correlation_id
    assert parsed.ts == cmd.ts


def test_command_to_field_omitted_when_none() -> None:
    cmd = Command(cmd=CommandName.PING, sender="chat:42")
    assert "to" not in json.loads(serialize_command(cmd))


def test_command_to_field_kept_when_set() -> None:
    cmd = Command(cmd=CommandName.PING, sender="chat:42", target="pi-01")
    parsed = parse_command_json(serialize_command(cmd))
    assert parsed.target == "pi-01"


def test_command_args_round_trip() -> None:
    cmd = Command(
        cmd=CommandName.LED_ON,
        sender="openclaw",
        args={"pin": 17},
        safety=SafetyMode.BENCH,
    )
    parsed = parse_command_json(serialize_command(cmd))
    assert parsed.args == {"pin": 17}


def test_reply_round_trip_ok() -> None:
    rep = Reply(sender="pi-01", state="idle", message="pong")
    parsed = parse_reply_json(serialize_reply(rep))
    assert parsed.ok is True
    assert parsed.error is None
    assert parsed.message == "pong"
    assert parsed.state == "idle"


def test_reply_round_trip_error() -> None:
    rep = Reply(
        sender="pi-01",
        state="error",
        ok=False,
        error=Error(code=ErrorCode.UNKNOWN_CMD, message="nope"),
        message="rejected",
    )
    parsed = parse_reply_json(serialize_reply(rep))
    assert parsed.ok is False
    assert parsed.error is not None
    assert parsed.error.code == ErrorCode.UNKNOWN_CMD
    assert parsed.error.message == "nope"


def test_reply_invariant_ok_with_error_raises() -> None:
    with pytest.raises(ProtocolError):
        Reply(
            sender="pi-01",
            ok=True,
            error=Error(code=ErrorCode.INTERNAL, message="nope"),
        )


def test_reply_invariant_not_ok_without_error_raises() -> None:
    with pytest.raises(ProtocolError):
        Reply(sender="pi-01", ok=False)


def test_parse_command_json_rejects_bad_json() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_command_json("not-json")
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_command_json_rejects_non_object() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_command_json("[1, 2, 3]")
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_command_json_rejects_missing_field() -> None:
    raw = json.dumps(
        {"v": 0, "id": "x", "ts": "x", "from": "x", "cmd": "ping"}
    )
    with pytest.raises(ProtocolError) as exc:
        parse_command_json(raw)
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_command_json_rejects_unknown_cmd() -> None:
    raw = json.dumps(
        {
            "v": 0,
            "id": "x",
            "ts": "x",
            "from": "x",
            "cmd": "yolo",
            "args": {},
            "safety": "dry_run",
        }
    )
    with pytest.raises(ProtocolError) as exc:
        parse_command_json(raw)
    assert exc.value.code == ErrorCode.UNKNOWN_CMD


def test_parse_command_json_rejects_bad_safety() -> None:
    raw = json.dumps(
        {
            "v": 0,
            "id": "x",
            "ts": "x",
            "from": "x",
            "cmd": "ping",
            "args": {},
            "safety": "bogus",
        }
    )
    with pytest.raises(ProtocolError) as exc:
        parse_command_json(raw)
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_command_json_rejects_wrong_version() -> None:
    raw = json.dumps(
        {
            "v": 99,
            "id": "x",
            "ts": "x",
            "from": "x",
            "cmd": "ping",
            "args": {},
            "safety": "dry_run",
        }
    )
    with pytest.raises(ProtocolError) as exc:
        parse_command_json(raw)
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_reply_json_rejects_unknown_error_code() -> None:
    raw = json.dumps(
        {
            "v": 0,
            "id": "x",
            "ts": "x",
            "from": "pi",
            "ok": False,
            "error": {"code": "made_up", "message": "x"},
            "state": "error",
            "telemetry": {},
            "message": "x",
        }
    )
    with pytest.raises(ProtocolError) as exc:
        parse_reply_json(raw)
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_slash_ping() -> None:
    cmd = parse_slash(
        "/ping", sender="chat:1", default_safety=SafetyMode.BENCH
    )
    assert cmd.cmd == CommandName.PING
    assert cmd.safety == SafetyMode.BENCH
    assert cmd.sender == "chat:1"


def test_parse_slash_led_on() -> None:
    cmd = parse_slash(
        "/led on", sender="chat:1", default_safety=SafetyMode.BENCH
    )
    assert cmd.cmd == CommandName.LED_ON


def test_parse_slash_led_off() -> None:
    cmd = parse_slash(
        "/led off", sender="chat:1", default_safety=SafetyMode.BENCH
    )
    assert cmd.cmd == CommandName.LED_OFF


def test_parse_slash_led_bad_arg() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_slash(
            "/led foo", sender="chat:1", default_safety=SafetyMode.BENCH
        )
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_slash_unknown() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_slash(
            "/totallyfake", sender="chat:1", default_safety=SafetyMode.BENCH
        )
    assert exc.value.code == ErrorCode.UNKNOWN_CMD


def test_parse_slash_not_slash() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_slash("hello", sender="chat:1", default_safety=SafetyMode.BENCH)
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_slash_with_hyphen_is_bad_args() -> None:
    """Slash command names are alphanumeric (Telegram convention)."""
    with pytest.raises(ProtocolError) as exc:
        parse_slash(
            "/foo-bar", sender="chat:1", default_safety=SafetyMode.BENCH
        )
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_slash_move() -> None:
    cmd = parse_slash(
        "/move 1 2 3", sender="chat:1", default_safety=SafetyMode.BENCH
    )
    assert cmd.cmd == CommandName.MOVE
    assert cmd.args == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert cmd.safety == SafetyMode.BENCH


def test_parse_slash_move_floats() -> None:
    cmd = parse_slash(
        "/move 1.5 -2 0.25",
        sender="chat:1",
        default_safety=SafetyMode.BENCH,
    )
    assert cmd.args == {"x": 1.5, "y": -2.0, "z": 0.25}


def test_parse_slash_move_wrong_arg_count() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_slash(
            "/move 1 2", sender="chat:1", default_safety=SafetyMode.BENCH
        )
    assert exc.value.code == ErrorCode.BAD_ARGS


def test_parse_slash_move_non_numeric() -> None:
    with pytest.raises(ProtocolError) as exc:
        parse_slash(
            "/move a b c", sender="chat:1", default_safety=SafetyMode.BENCH
        )
    assert exc.value.code == ErrorCode.BAD_ARGS
