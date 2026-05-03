"""MissionPolicy contract.

The minimum shape every mission-control implementation (mock, Gemma,
future adapters) must satisfy. Policies map a high-level intent + scene
+ world state into a single concrete next action that the router can
execute, plus a reason and a confidence.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from freemotion.protocol import CommandName
from freemotion.vision import VisionResult
from freemotion.world import WorldStateSnapshot


@dataclasses.dataclass(frozen=True)
class MissionDecision:
    """One step's worth of decision.

    `next_command=None` is the explicit "do nothing this tick" signal.
    Confidence stays cheap to interpret (0.0..1.0); reason is for logs
    and chat replies, not for further parsing.
    """

    next_command: Optional[CommandName] = None
    args: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0


@runtime_checkable
class MissionPolicy(Protocol):
    """Implementations:

    - SHOULD be pure functions of (intent, scene, world). Side-effecting
      policies break replayability and tests.
    - SHOULD prefer returning `next_command=None` over guessing when
      confidence would be low.
    - SHOULD set `reason` to a short human-readable string; chat clients
      may surface it.
    """

    @property
    def name(self) -> str:
        """Short identifier, e.g. `"mock"`, `"gemma"`."""

    @property
    def available(self) -> bool:
        """Whether the policy is ready to plan."""

    def plan(
        self,
        *,
        intent: str,
        scene: VisionResult,
        world: WorldStateSnapshot,
    ) -> MissionDecision:
        """Map an intent + scene + world state to a single next action.

        Pass `WorldStateSnapshot()` (the default) when no live world state
        is available; the policy must accept that case without raising.
        """
