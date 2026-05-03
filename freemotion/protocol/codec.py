"""Free Motion protocol v0: parse, serialize, and slash sugar."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .envelopes import (
    PROTOCOL_VERSION,
    Command,
    CommandName,
    Error,
    ErrorCode,
    ProtocolError,
    Reply,
    SafetyMode,
)

_VALID_CMDS = {c.value for c in CommandName}
_VALID_SAFETY = {s.value for s in SafetyMode}
_VALID_ERROR_CODES = {e.value for e in ErrorCode}


def command_to_dict(cmd: Command) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "v": cmd.v,
        "id": cmd.correlation_id,
        "ts": cmd.ts,
        "from": cmd.sender,
        "cmd": cmd.cmd.value,
        "args": dict(cmd.args),
        "safety": cmd.safety.value,
    }
    if cmd.target is not None:
        out["to"] = cmd.target
    return out


def reply_to_dict(reply: Reply) -> Dict[str, Any]:
    error_obj: Optional[Dict[str, str]] = None
    if reply.error is not None:
        error_obj = {
            "code": reply.error.code.value,
            "message": reply.error.message,
        }
    return {
        "v": reply.v,
        "id": reply.correlation_id,
        "ts": reply.ts,
        "from": reply.sender,
        "ok": reply.ok,
        "error": error_obj,
        "state": reply.state,
        "telemetry": dict(reply.telemetry),
        "message": reply.message,
    }


def serialize_command(cmd: Command) -> str:
    return json.dumps(command_to_dict(cmd), separators=(",", ":"))


def serialize_reply(reply: Reply) -> str:
    return json.dumps(reply_to_dict(reply), separators=(",", ":"))


def _require(d: Dict[str, Any], key: str, type_: type, *, where: str) -> Any:
    if key not in d:
        raise ProtocolError(
            ErrorCode.BAD_ARGS, f"{where}: missing field '{key}'"
        )
    val = d[key]
    if not isinstance(val, type_):
        raise ProtocolError(
            ErrorCode.BAD_ARGS,
            f"{where}: field '{key}' must be {type_.__name__}",
        )
    return val


def parse_command_json(raw: str) -> Command:
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ErrorCode.BAD_ARGS, f"invalid JSON: {exc}") from exc
    if not isinstance(d, dict):
        raise ProtocolError(
            ErrorCode.BAD_ARGS, "envelope must be a JSON object"
        )

    v = _require(d, "v", int, where="command")
    if v != PROTOCOL_VERSION:
        raise ProtocolError(
            ErrorCode.BAD_ARGS, f"unsupported protocol version: {v}"
        )

    cid = _require(d, "id", str, where="command")
    ts = _require(d, "ts", str, where="command")
    sender = _require(d, "from", str, where="command")
    cmd_name = _require(d, "cmd", str, where="command")
    args = _require(d, "args", dict, where="command")
    safety = _require(d, "safety", str, where="command")

    target = d.get("to")
    if target is not None and not isinstance(target, str):
        raise ProtocolError(ErrorCode.BAD_ARGS, "command: 'to' must be a string")

    if cmd_name not in _VALID_CMDS:
        raise ProtocolError(
            ErrorCode.UNKNOWN_CMD, f"unknown cmd '{cmd_name}'"
        )
    if safety not in _VALID_SAFETY:
        raise ProtocolError(
            ErrorCode.BAD_ARGS, f"invalid safety '{safety}'"
        )

    return Command(
        cmd=CommandName(cmd_name),
        sender=sender,
        args=args,
        safety=SafetyMode(safety),
        target=target,
        correlation_id=cid,
        ts=ts,
        v=v,
    )


def parse_reply_json(raw: str) -> Reply:
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ErrorCode.BAD_ARGS, f"invalid JSON: {exc}") from exc
    if not isinstance(d, dict):
        raise ProtocolError(
            ErrorCode.BAD_ARGS, "envelope must be a JSON object"
        )

    v = _require(d, "v", int, where="reply")
    cid = _require(d, "id", str, where="reply")
    ts = _require(d, "ts", str, where="reply")
    sender = _require(d, "from", str, where="reply")
    ok = _require(d, "ok", bool, where="reply")
    state = _require(d, "state", str, where="reply")
    telemetry = _require(d, "telemetry", dict, where="reply")
    message = _require(d, "message", str, where="reply")
    err_raw = d.get("error")

    error: Optional[Error] = None
    if err_raw is not None:
        if not isinstance(err_raw, dict):
            raise ProtocolError(
                ErrorCode.BAD_ARGS,
                "reply: 'error' must be object or null",
            )
        ec = _require(err_raw, "code", str, where="reply.error")
        em = _require(err_raw, "message", str, where="reply.error")
        if ec not in _VALID_ERROR_CODES:
            raise ProtocolError(
                ErrorCode.BAD_ARGS, f"unknown error code '{ec}'"
            )
        error = Error(code=ErrorCode(ec), message=em)

    return Reply(
        sender=sender,
        state=state,
        ok=ok,
        error=error,
        telemetry=telemetry,
        message=message,
        correlation_id=cid,
        ts=ts,
        v=v,
    )


_SLASH_RE = re.compile(r"^/(\w+)(?:\s+(.*))?$", re.DOTALL)
_SLASH_TO_CMD = {
    "ping": CommandName.PING,
    "status": CommandName.STATUS,
    "capabilities": CommandName.CAPABILITIES,
    "arm": CommandName.ARM,
    "disarm": CommandName.DISARM,
    "stop": CommandName.STOP,
}


def parse_slash(
    text: str, *, sender: str, default_safety: SafetyMode
) -> Command:
    """Translate slash-command sugar into a Command.

    `default_safety` is the device's configured `safety_default`, applied
    because slash sugar has no way to express the safety field.
    """
    m = _SLASH_RE.match(text.strip())
    if not m:
        raise ProtocolError(ErrorCode.BAD_ARGS, "not a slash command")

    name = m.group(1).lower()
    arg_str = (m.group(2) or "").strip()

    if name == "led":
        sub = arg_str.lower().split()
        if not sub or sub[0] not in {"on", "off"}:
            raise ProtocolError(ErrorCode.BAD_ARGS, "usage: /led on|off")
        cmd_name = (
            CommandName.LED_ON if sub[0] == "on" else CommandName.LED_OFF
        )
        return Command(
            cmd=cmd_name, sender=sender, args={}, safety=default_safety
        )

    if name == "move":
        parts = arg_str.split()
        if len(parts) != 3:
            raise ProtocolError(
                ErrorCode.BAD_ARGS, "usage: /move x y z"
            )
        try:
            x, y, z = (float(p) for p in parts)
        except ValueError as exc:
            raise ProtocolError(
                ErrorCode.BAD_ARGS, "usage: /move x y z (x, y, z must be numbers)"
            ) from exc
        return Command(
            cmd=CommandName.MOVE,
            sender=sender,
            args={"x": x, "y": y, "z": z},
            safety=default_safety,
        )

    if name == "mission_start":
        # Trailing tokens are the free-form intent string. Empty intent
        # is permitted (the mission-control policy treats it as idle),
        # so `/mission_start` alone is parseable. Whitespace is
        # collapsed to a single space so log lines and `world.target`
        # entries don't carry stray indentation.
        intent = " ".join(arg_str.split())
        return Command(
            cmd=CommandName.MISSION_START,
            sender=sender,
            args={"intent": intent},
            safety=default_safety,
        )

    if name in _SLASH_TO_CMD:
        return Command(
            cmd=_SLASH_TO_CMD[name],
            sender=sender,
            args={},
            safety=default_safety,
        )

    raise ProtocolError(
        ErrorCode.UNKNOWN_CMD, f"unknown slash command '/{name}'"
    )
