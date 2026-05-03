"""Free Motion mission control (M3, interface-first).

Today: a `MissionPolicy` Protocol and a `MockMissionControl`
implementation. A `GemmaMissionControl` adapter is planned and gated
behind a config flag.
"""

from .interface import MissionDecision, MissionPolicy
from .mock import MockMissionControl

__all__ = [
    "MissionDecision",
    "MissionPolicy",
    "MockMissionControl",
]
