"""Command router for Free Motion.

Maps `CommandName` to a handler, dispatches a `Command`, and returns a
`Reply`. Pure: no I/O, no Telegram. The `Agent` class wraps this for I/O.
"""

from __future__ import annotations

from typing import Callable, Dict, FrozenSet, Iterable, List, Optional

from freemotion.protocol import (
    Command,
    CommandName,
    Error,
    ErrorCode,
    Reply,
)

Handler = Callable[[Command], Reply]


class RouterError(Exception):
    """Raised on invalid router config (e.g. duplicate handler)."""


class Router:
    """Maps `CommandName` -> `Handler`.

    Unknown commands return an `unknown_cmd` reply with the same
    correlation id. Handler exceptions are caught and surfaced as
    `internal` replies. Both behaviors keep dispatch total: the agent
    can always send something back.

    `denied_commands` is an opt-in policy list (wire command names, e.g.
    `{"arm", "move"}`). When set, dispatch returns a `denied_by_policy`
    error before invoking the handler. `stop` is exempt from the deny
    policy unconditionally — see ADR-0004 in `docs/decisions.md`.
    """

    def __init__(
        self,
        *,
        device_id: str,
        denied_commands: Optional[Iterable[str]] = None,
    ) -> None:
        self._device_id = device_id
        self._handlers: Dict[CommandName, Handler] = {}
        denied: FrozenSet[str] = frozenset(denied_commands or ())
        self._denied: FrozenSet[str] = denied - {CommandName.STOP.value}

    def register(self, name: CommandName, handler: Handler) -> None:
        if name in self._handlers:
            raise RouterError(
                f"handler already registered for {name.value}"
            )
        self._handlers[name] = handler

    @property
    def known(self) -> List[str]:
        """Sorted list of registered command names (wire form)."""
        return sorted(c.value for c in self._handlers)

    @property
    def denied(self) -> FrozenSet[str]:
        """Effective deny set (with `stop` always excluded)."""
        return self._denied

    def dispatch(self, cmd: Command) -> Reply:
        if cmd.cmd.value in self._denied:
            return self._reply_err(
                cmd,
                code=ErrorCode.DENIED_BY_POLICY,
                message=(
                    f"command '{cmd.cmd.value}' denied by device policy"
                ),
            )
        handler = self._handlers.get(cmd.cmd)
        if handler is None:
            return self._reply_err(
                cmd,
                code=ErrorCode.UNKNOWN_CMD,
                message=(
                    f"command '{cmd.cmd.value}' not implemented on this device"
                ),
            )
        try:
            return handler(cmd)
        except Exception as exc:
            return self._reply_err(
                cmd,
                code=ErrorCode.INTERNAL,
                message=f"internal: {exc}",
            )

    def _reply_err(
        self, cmd: Command, *, code: ErrorCode, message: str
    ) -> Reply:
        return Reply(
            sender=self._device_id,
            state="error",
            ok=False,
            error=Error(code=code, message=message),
            telemetry={},
            message=message,
            correlation_id=cmd.correlation_id,
        )
