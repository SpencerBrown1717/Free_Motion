"""Tests for freemotion.router."""

from __future__ import annotations

import pytest

from freemotion.protocol import Command, CommandName, ErrorCode, Reply
from freemotion.router import Router, RouterError


def _ok(cmd: Command) -> Reply:
    return Reply(
        sender="dev-test",
        state="idle",
        ok=True,
        error=None,
        telemetry={},
        message="ok",
        correlation_id=cmd.correlation_id,
    )


def test_register_and_dispatch() -> None:
    r = Router(device_id="dev-test")
    r.register(CommandName.PING, _ok)
    cmd = Command(cmd=CommandName.PING, sender="x")
    reply = r.dispatch(cmd)
    assert reply.ok is True
    assert reply.message == "ok"
    assert reply.correlation_id == cmd.correlation_id


def test_register_duplicate_raises() -> None:
    r = Router(device_id="dev-test")
    r.register(CommandName.PING, _ok)
    with pytest.raises(RouterError):
        r.register(CommandName.PING, _ok)


def test_dispatch_unknown_returns_unknown_cmd() -> None:
    r = Router(device_id="dev-test")
    cmd = Command(cmd=CommandName.PING, sender="x")
    reply = r.dispatch(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.UNKNOWN_CMD
    assert reply.correlation_id == cmd.correlation_id


def test_dispatch_handler_exception_becomes_internal() -> None:
    def boom(cmd: Command) -> Reply:
        raise RuntimeError("kaboom")

    r = Router(device_id="dev-test")
    r.register(CommandName.STATUS, boom)
    cmd = Command(cmd=CommandName.STATUS, sender="x")
    reply = r.dispatch(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.INTERNAL
    assert "kaboom" in reply.message
    assert reply.correlation_id == cmd.correlation_id


def test_known_returns_sorted_wire_names() -> None:
    r = Router(device_id="dev-test")
    r.register(CommandName.STOP, _ok)
    r.register(CommandName.PING, _ok)
    r.register(CommandName.STATUS, _ok)
    assert r.known == ["ping", "status", "stop"]


def test_device_id_preserved_in_error_replies() -> None:
    r = Router(device_id="my-device")
    cmd = Command(cmd=CommandName.PING, sender="x")
    reply = r.dispatch(cmd)
    assert reply.sender == "my-device"


def test_denied_command_returns_denied_by_policy() -> None:
    r = Router(device_id="dev-test", denied_commands={"arm"})
    r.register(CommandName.ARM, _ok)
    cmd = Command(cmd=CommandName.ARM, sender="x")
    reply = r.dispatch(cmd)
    assert reply.ok is False
    assert reply.error is not None
    assert reply.error.code == ErrorCode.DENIED_BY_POLICY
    assert "denied by device policy" in reply.message
    assert reply.correlation_id == cmd.correlation_id


def test_denied_takes_precedence_over_unknown() -> None:
    """A command that's both denied and not registered must report the deny,
    not leak that the device would have refused for a different reason.
    """
    r = Router(device_id="dev-test", denied_commands={"move"})
    cmd = Command(cmd=CommandName.MOVE, sender="x")
    reply = r.dispatch(cmd)
    assert reply.error is not None
    assert reply.error.code == ErrorCode.DENIED_BY_POLICY


def test_stop_is_exempt_from_deny() -> None:
    r = Router(
        device_id="dev-test",
        denied_commands={"arm", "move", "stop"},
    )
    r.register(CommandName.STOP, _ok)
    cmd = Command(cmd=CommandName.STOP, sender="x")
    reply = r.dispatch(cmd)
    assert reply.ok is True


def test_stop_is_silently_dropped_from_denied_property() -> None:
    """`stop` slipping into the deny set must not be observable via the API."""
    r = Router(
        device_id="dev-test",
        denied_commands={"arm", "stop"},
    )
    assert "stop" not in r.denied
    assert "arm" in r.denied


def test_default_router_has_empty_deny_set() -> None:
    r = Router(device_id="dev-test")
    assert r.denied == frozenset()


def test_unknown_denied_name_is_harmless() -> None:
    """Devices may run against newer protocols. An unknown deny entry
    just denies a command nothing would have routed anyway.
    """
    r = Router(device_id="dev-test", denied_commands={"future_cmd"})
    r.register(CommandName.PING, _ok)
    cmd = Command(cmd=CommandName.PING, sender="x")
    assert r.dispatch(cmd).ok is True
