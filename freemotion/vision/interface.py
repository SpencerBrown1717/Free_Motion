"""VisionBackend contract.

The minimum shape every vision implementation (mock, YOLO, future
adapters) must satisfy. Backends manage their own input source
internally; callers only ask for the latest scene.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol, Tuple, runtime_checkable

from freemotion.protocol import now_iso


@dataclasses.dataclass(frozen=True)
class Detection:
    """One detected object in a scene."""

    label: str
    confidence: float
    bbox: Tuple[float, float, float, float]
    """`(x, y, w, h)` normalized to 0..1."""


@dataclasses.dataclass(frozen=True)
class VisionResult:
    """Snapshot of what the backend currently believes is in view."""

    detections: Tuple[Detection, ...]
    ts: str = dataclasses.field(default_factory=now_iso)


@runtime_checkable
class VisionBackend(Protocol):
    """Implementations:

    - SHOULD make `scene()` cheap (don't run inference twice in a row
      if the underlying frame hasn't changed; cache when reasonable).
    - SHOULD return an empty `VisionResult` rather than raise when
      no signal is available.
    """

    @property
    def name(self) -> str:
        """Short identifier, e.g. `"mock"`, `"yolo"`."""

    @property
    def available(self) -> bool:
        """Whether the backend is ready to produce scenes."""

    def scene(self) -> VisionResult:
        """Latest scene snapshot."""
