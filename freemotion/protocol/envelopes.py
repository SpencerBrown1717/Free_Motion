"""Free Motion protocol v0 envelopes.

Pure data types. No I/O. Parsing and serialization live in `codec.py`.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 0


class SafetyMode(str, enum.Enum):
    DRY_RUN = "dry_run"
    BENCH = "bench"
    LIVE = "live"


class CommandName(str, enum.Enum):
    PING = "ping"
    STATUS = "status"
    CAPABILITIES = "capabilities"
    LED_ON = "led_on"
    LED_OFF = "led_off"
    ARM = "arm"
    DISARM = "disarm"
    STOP = "stop"
    MOVE = "move"


class ErrorCode(str, enum.Enum):
    UNAUTHORIZED = "unauthorized"
    UNKNOWN_CMD = "unknown_cmd"
    BAD_ARGS = "bad_args"
    UNSAFE_IN_MODE = "unsafe_in_mode"
    DEVICE_BUSY = "device_busy"
    INTERNAL = "internal"


class ProtocolError(Exception):
    """Raised when an envelope is malformed or invalid.

    Carries a stable `code` so callers can surface it as `error.code`
    in a reply without translating exception classes.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message


def now_iso() -> str:
    """Current UTC time as RFC 3339 with a `Z` suffix."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def new_id() -> str:
    """Fresh correlation id."""
    return str(uuid.uuid4())


@dataclasses.dataclass(frozen=True)
class Command:
    cmd: CommandName
    sender: str = "unknown"
    args: Dict[str, Any] = dataclasses.field(default_factory=dict)
    safety: SafetyMode = SafetyMode.DRY_RUN
    target: Optional[str] = None  # `to` on the wire; optional in v0
    correlation_id: str = dataclasses.field(default_factory=new_id)
    ts: str = dataclasses.field(default_factory=now_iso)
    v: int = PROTOCOL_VERSION


@dataclasses.dataclass(frozen=True)
class Error:
    code: ErrorCode
    message: str


@dataclasses.dataclass(frozen=True)
class Reply:
    sender: str
    state: str = "idle"
    ok: bool = True
    error: Optional[Error] = None
    telemetry: Dict[str, Any] = dataclasses.field(default_factory=dict)
    message: str = ""
    correlation_id: str = dataclasses.field(default_factory=new_id)
    ts: str = dataclasses.field(default_factory=now_iso)
    v: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.ok and self.error is not None:
            raise ProtocolError(
                ErrorCode.INTERNAL, "Reply: ok=True but error is set"
            )
        if not self.ok and self.error is None:
            raise ProtocolError(
                ErrorCode.INTERNAL, "Reply: ok=False but error is missing"
            )
