"""Free Motion vision (M3, interface-first).

Today: a `VisionBackend` Protocol and a `MockVision` implementation.
A `YoloVision` adapter is planned and gated behind a config flag.
"""

from .interface import Detection, VisionBackend, VisionResult
from .mock import MockVision

__all__ = [
    "Detection",
    "MockVision",
    "VisionBackend",
    "VisionResult",
]
