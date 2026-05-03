"""Free Motion device agent (M2 + Step 2 closed loop)."""

from .agent import HELP_TEXT, Agent, handle_text, is_authorized
from .builtins import (
    make_arm_handler,
    make_capabilities_handler,
    make_disarm_handler,
    make_mission_start_handler,
    make_move_handler,
    make_ping_handler,
    make_status_handler,
    make_stop_handler,
)
from .mission_loop import MissionLoop

__all__ = [
    "Agent",
    "HELP_TEXT",
    "MissionLoop",
    "handle_text",
    "is_authorized",
    "make_arm_handler",
    "make_capabilities_handler",
    "make_disarm_handler",
    "make_mission_start_handler",
    "make_move_handler",
    "make_ping_handler",
    "make_status_handler",
    "make_stop_handler",
]
