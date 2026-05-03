"""Free Motion protocol v0.

See docs/protocol.md for the full contract. This package is the single
source of truth for command and reply shapes; everything else in the
codebase (agents, examples, tests) should import from here rather than
hand-rolling JSON.
"""

from .codec import (
    command_to_dict,
    parse_command_json,
    parse_reply_json,
    parse_slash,
    reply_to_dict,
    serialize_command,
    serialize_reply,
)
from .envelopes import (
    PROTOCOL_VERSION,
    Command,
    CommandName,
    Error,
    ErrorCode,
    ProtocolError,
    Reply,
    SafetyMode,
    new_id,
    now_iso,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Command",
    "CommandName",
    "Error",
    "ErrorCode",
    "ProtocolError",
    "Reply",
    "SafetyMode",
    "command_to_dict",
    "new_id",
    "now_iso",
    "parse_command_json",
    "parse_reply_json",
    "parse_slash",
    "reply_to_dict",
    "serialize_command",
    "serialize_reply",
]
