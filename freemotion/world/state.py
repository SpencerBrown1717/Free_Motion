"""WorldState v1.

The contract:

- The *snapshot* is immutable. Anyone holding one can read it freely
  from any thread without coordinating.
- The *state* is mutable but thread-safe. All writes go through the
  lock; readers always observe a consistent snapshot.
- Updates are total replacements: `update(...)` swaps the snapshot
  reference under the lock. We never mutate fields in place.

Shape is intentionally narrow (target, current_state, confidence,
last_seen, next_action) per ADR-0005. Wider state belongs in dedicated
modules (telemetry, mission history, etc.).
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Any, Mapping, Optional

from freemotion.protocol import now_iso


@dataclasses.dataclass(frozen=True)
class WorldStateSnapshot:
    """Immutable point-in-time view of the device's world model."""

    target: Optional[str] = None
    current_state: str = "idle"
    confidence: float = 0.0
    last_seen: Mapping[str, str] = dataclasses.field(default_factory=dict)
    next_action: Optional[str] = None
    ts: str = dataclasses.field(default_factory=now_iso)


class WorldState:
    """Thread-safe wrapper around a `WorldStateSnapshot`."""

    def __init__(
        self, *, initial: Optional[WorldStateSnapshot] = None
    ) -> None:
        self._lock = threading.Lock()
        self._snapshot: WorldStateSnapshot = initial or WorldStateSnapshot()

    def snapshot(self) -> WorldStateSnapshot:
        """Return the current snapshot. Frozen; safe to share."""
        with self._lock:
            return self._snapshot

    def update(self, **changes: Any) -> WorldStateSnapshot:
        """Replace named fields atomically and stamp `ts`.

        Unknown field names raise `TypeError` (via `dataclasses.replace`).
        """
        with self._lock:
            self._snapshot = dataclasses.replace(
                self._snapshot, ts=now_iso(), **changes
            )
            return self._snapshot

    def see(
        self, label: str, *, confidence: Optional[float] = None
    ) -> WorldStateSnapshot:
        """Mark `label` as seen now. Convenience for the vision -> world hop.

        Also updates `target` to `label`. Pass `confidence` to record it.
        """
        with self._lock:
            new_last_seen = dict(self._snapshot.last_seen)
            new_last_seen[label] = now_iso()
            changes: dict[str, Any] = {
                "target": label,
                "last_seen": new_last_seen,
                "ts": now_iso(),
            }
            if confidence is not None:
                changes["confidence"] = confidence
            self._snapshot = dataclasses.replace(self._snapshot, **changes)
            return self._snapshot
