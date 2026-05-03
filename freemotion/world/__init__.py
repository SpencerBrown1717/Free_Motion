"""Free Motion world state (M3).

The shared "what does the device think is true right now" structure.
Read by the router for `/status`, written by vision + mission_control.

Public surface:

- `WorldStateSnapshot` — immutable read view (a frozen dataclass).
- `WorldState`        — thread-safe mutable wrapper around the snapshot.

`MissionPolicy.plan(...)` accepts a `WorldStateSnapshot` directly; pass
`WorldState().snapshot()` (or a freshly built `WorldStateSnapshot()`)
when no live world state is available.
"""

from .state import WorldState, WorldStateSnapshot

__all__ = [
    "WorldState",
    "WorldStateSnapshot",
]
