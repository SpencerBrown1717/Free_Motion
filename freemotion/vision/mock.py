"""Deterministic mock vision backend.

Returns scripted `VisionResult`s in order, cycling when exhausted. With
no script, returns an empty scene. Useful for tests, demos, and the
(forthcoming) follow-task example. Not for training or evaluation.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from .interface import VisionResult


class MockVision:
    """In-memory scripted vision backend."""

    name = "mock"

    def __init__(
        self, *, scripted: Optional[Iterable[VisionResult]] = None
    ) -> None:
        self._scripted: List[VisionResult] = list(scripted or [])
        self._idx = 0

    @property
    def available(self) -> bool:
        return True

    def scene(self) -> VisionResult:
        if not self._scripted:
            return VisionResult(detections=())
        result = self._scripted[self._idx % len(self._scripted)]
        self._idx += 1
        return result
