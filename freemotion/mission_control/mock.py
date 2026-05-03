"""Deterministic rule-based mock mission control.

Recognizes a small set of intents:

- `stop` / `halt` / `abort` → `stop`
- `disarm` / `land` → `disarm`
- `follow` / `follow person` → if a person is in the scene, move
  forward 1 unit; otherwise idle.
- anything else → idle

Pure, deterministic, no LLM. The structural pattern Gemma will follow.
"""

from __future__ import annotations

from freemotion.protocol import CommandName
from freemotion.vision import VisionResult
from freemotion.world import WorldStateSnapshot

from .interface import MissionDecision

_STOP_INTENTS = frozenset({"stop", "halt", "abort"})
_DISARM_INTENTS = frozenset({"disarm", "land"})
_FOLLOW_INTENTS = frozenset({"follow", "follow person"})


class MockMissionControl:
    """Rule-based mock policy."""

    name = "mock"

    @property
    def available(self) -> bool:
        return True

    def plan(
        self,
        *,
        intent: str,
        scene: VisionResult,
        world: WorldStateSnapshot,
    ) -> MissionDecision:
        normalized = intent.lower().strip()

        if normalized in _STOP_INTENTS:
            return MissionDecision(
                next_command=CommandName.STOP,
                reason="explicit stop intent",
                confidence=1.0,
            )

        if normalized in _DISARM_INTENTS:
            return MissionDecision(
                next_command=CommandName.DISARM,
                reason="explicit disarm intent",
                confidence=1.0,
            )

        if normalized in _FOLLOW_INTENTS:
            persons = [
                d for d in scene.detections if d.label == "person"
            ]
            if not persons:
                return MissionDecision(
                    next_command=None,
                    reason="follow: no person in scene",
                    confidence=0.0,
                )
            best = max(persons, key=lambda d: d.confidence)
            return MissionDecision(
                next_command=CommandName.MOVE,
                args={"x": 1.0, "y": 0.0, "z": 0.0},
                reason=(
                    f"follow: person at confidence {best.confidence:.2f}"
                ),
                confidence=best.confidence,
            )

        return MissionDecision(
            next_command=None,
            reason=f"unknown intent: {intent!r}",
            confidence=0.0,
        )
