"""Config for a Free Motion device.

Read once at startup, frozen after. Anything mutable lives elsewhere.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import socket
from typing import FrozenSet, Mapping, Optional

from freemotion.protocol import SafetyMode

LOG = logging.getLogger("freemotion.config")


def _parse_chat_ids(raw: str) -> FrozenSet[int]:
    out: set[int] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(int(piece))
        except ValueError:
            LOG.warning("ignoring non-integer chat id: %r", piece)
    return frozenset(out)


def _parse_features(raw: str) -> FrozenSet[str]:
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def _parse_denied_commands(raw: str) -> FrozenSet[str]:
    """Comma-separated wire command names. Values are not validated here
    against `CommandName`; an unknown name in the deny set is harmless
    (it just denies a command the device wouldn't have known anyway)
    and forward-compatible with newer protocol versions.
    """
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


@dataclasses.dataclass(frozen=True)
class Config:
    """Free Motion device config.

    `from_env()` is the only construction path the runtime should use;
    direct construction is fine for tests.
    """

    token: str
    allowed_chat_ids: FrozenSet[int] = frozenset()
    device_id: str = "unknown"
    safety_default: SafetyMode = SafetyMode.DRY_RUN
    led_pin: Optional[int] = None
    hardware_profile: str = "host"
    enabled_features: FrozenSet[str] = frozenset()
    denied_commands: FrozenSet[str] = frozenset()

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        e: Mapping[str, str] = env if env is not None else os.environ

        token = e.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN is not set. See docs/pi-setup.md section 4."
            )

        allowed = _parse_chat_ids(e.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))

        device_id = (
            e.get("FREEMOTION_DEVICE_ID", "").strip() or socket.gethostname()
        )

        safety_raw = (
            e.get("FREEMOTION_SAFETY_DEFAULT", "dry_run").strip().lower()
        )
        try:
            safety_default = SafetyMode(safety_raw)
        except ValueError:
            LOG.warning(
                "invalid FREEMOTION_SAFETY_DEFAULT=%r, falling back to dry_run",
                safety_raw,
            )
            safety_default = SafetyMode.DRY_RUN

        led_raw = e.get("FREEMOTION_LED_PIN", "").strip()
        led_pin: Optional[int] = None
        if led_raw:
            try:
                led_pin = int(led_raw)
            except ValueError:
                LOG.warning(
                    "ignoring non-integer FREEMOTION_LED_PIN: %r", led_raw
                )

        hardware_profile = (
            e.get("FREEMOTION_HARDWARE", "").strip() or "host"
        )

        enabled = _parse_features(e.get("FREEMOTION_FEATURES", ""))

        denied = _parse_denied_commands(e.get("FREEMOTION_DENIED_COMMANDS", ""))
        if "stop" in denied:
            LOG.warning(
                "FREEMOTION_DENIED_COMMANDS lists 'stop'; ignoring. "
                "stop is honored unconditionally per protocol v0."
            )
            denied = denied - {"stop"}

        return cls(
            token=token,
            allowed_chat_ids=allowed,
            device_id=device_id,
            safety_default=safety_default,
            led_pin=led_pin,
            hardware_profile=hardware_profile,
            enabled_features=enabled,
            denied_commands=denied,
        )
