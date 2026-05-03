"""Free Motion mission control.

Today: a `MissionPolicy` Protocol, a `MockMissionControl` (default),
and a real `GemmaMissionControl` adapter (post-M4) gated behind
`FREEMOTION_MISSION_BACKEND=gemma` and a `pip install -e .[gemma]`
extra.

`make_mission_from_config` is the runtime factory: given a `Config`,
it returns the policy matching `config.mission_backend`. The Gemma
backend is import-safe on any host because its heavy deps
(`transformers`, `torch`) are imported lazily inside its constructor.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .gemma import GemmaMissionControl
from .interface import MissionDecision, MissionPolicy
from .mock import MockMissionControl

if TYPE_CHECKING:  # pragma: no cover
    from freemotion.config import Config

LOG = logging.getLogger("freemotion.mission_control")

__all__ = [
    "GemmaMissionControl",
    "MissionDecision",
    "MissionPolicy",
    "MockMissionControl",
    "make_mission_from_config",
]


def make_mission_from_config(config: "Config") -> MissionPolicy:
    """Pick a `MissionPolicy` for `config.mission_backend`.

    - ``"gemma"``: constructs `GemmaMissionControl` with defaults
      (small instruction-tuned Gemma, low temperature, 128-token cap).
      If `transformers` is missing or model load fails, the adapter
      returns from `__init__` already offline; `plan()` falls back to
      idle decisions instead of raising. Examples that need richer
      wiring (custom model, sampling, GPU placement) should construct
      `GemmaMissionControl` directly.
    - ``"mock"`` (or unset / unknown): `MockMissionControl`. Unknown
      values log a warning so misconfiguration is visible.
    """
    backend = (config.mission_backend or "").strip().lower()
    if backend == "gemma":
        return GemmaMissionControl()
    if backend not in {"", "mock"}:
        LOG.warning(
            "unknown FREEMOTION_MISSION_BACKEND=%r; falling back to "
            "MockMissionControl",
            backend,
        )
    return MockMissionControl()
