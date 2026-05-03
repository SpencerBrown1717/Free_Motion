#!/usr/bin/env python3
"""Free Motion pipe_check (M2 reference).

Wires Free Motion's runtime to a Pi:

    Config.from_env()  ->  Router  ->  Agent  ->  Telegram

The example contributes one piece of hardware (an optional GPIO LED) and
two example-local handlers (`led_on`, `led_off`). Everything else
(`ping`, `status`, `capabilities`, `stop`) comes from
`freemotion.agent`'s built-ins.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

from freemotion.agent import (
    Agent,
    make_capabilities_handler,
    make_ping_handler,
    make_status_handler,
    make_stop_handler,
)
from freemotion.config import Config
from freemotion.protocol import (
    Command,
    CommandName,
    Error,
    ErrorCode,
    Reply,
    SafetyMode,
)
from freemotion.router import Handler, Router

try:
    import RPi.GPIO as GPIO  # type: ignore[import-not-found]

    _GPIO_AVAILABLE = True
except Exception:
    GPIO = None  # type: ignore[assignment]
    _GPIO_AVAILABLE = False

LOG = logging.getLogger("freemotion.pipe_check")


class Led:
    """Optional GPIO LED. No-op when GPIO is unavailable or no pin is set."""

    def __init__(self, pin: Optional[int]) -> None:
        self.pin = pin
        self._ready = False
        if pin is not None and _GPIO_AVAILABLE:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                self._ready = True
            except Exception as exc:  # pragma: no cover - hardware-specific
                LOG.warning("GPIO init failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._ready

    def set(self, on: bool) -> None:
        if not self._ready:
            return
        GPIO.output(self.pin, GPIO.HIGH if on else GPIO.LOW)

    def cleanup(self) -> None:
        if self._ready:
            try:
                GPIO.cleanup(self.pin)
            except Exception:  # pragma: no cover - hardware-specific
                pass


def _reply_ok(config: Config, cid: str, message: str) -> Reply:
    return Reply(
        sender=config.device_id,
        state="idle",
        ok=True,
        error=None,
        telemetry={},
        message=message,
        correlation_id=cid,
    )


def _reply_err(
    config: Config, cid: str, code: ErrorCode, message: str
) -> Reply:
    return Reply(
        sender=config.device_id,
        state="error",
        ok=False,
        error=Error(code=code, message=message),
        telemetry={},
        message=message,
        correlation_id=cid,
    )


def make_led_handlers(config: Config, led: Led) -> tuple[Handler, Handler]:
    """Example-local handlers for `led_on` / `led_off`.

    Honors the protocol's `safety` field: `dry_run` logs but does not
    actuate.
    """

    def led_on(cmd: Command) -> Reply:
        if not led.available:
            return _reply_err(
                config,
                cmd.correlation_id,
                ErrorCode.UNSAFE_IN_MODE,
                "GPIO not available on this host (set FREEMOTION_LED_PIN on a Pi)",
            )
        if cmd.safety == SafetyMode.DRY_RUN:
            return _reply_ok(
                config, cmd.correlation_id, "dry_run: would turn led on"
            )
        led.set(True)
        return _reply_ok(config, cmd.correlation_id, "led on")

    def led_off(cmd: Command) -> Reply:
        if not led.available:
            return _reply_err(
                config,
                cmd.correlation_id,
                ErrorCode.UNSAFE_IN_MODE,
                "GPIO not available on this host (set FREEMOTION_LED_PIN on a Pi)",
            )
        if cmd.safety == SafetyMode.DRY_RUN:
            return _reply_ok(
                config, cmd.correlation_id, "dry_run: would turn led off"
            )
        led.set(False)
        return _reply_ok(config, cmd.correlation_id, "led off")

    return led_on, led_off


def build_router(config: Config, led: Led) -> Router:
    router = Router(device_id=config.device_id)
    router.register(CommandName.PING, make_ping_handler(config))
    router.register(
        CommandName.STOP,
        make_stop_handler(config, on_stop=lambda: led.set(False)),
    )
    router.register(
        CommandName.STATUS,
        make_status_handler(config, gpio_available=led.available),
    )
    router.register(
        CommandName.CAPABILITIES, make_capabilities_handler(config, router)
    )
    led_on, led_off = make_led_handlers(config, led)
    router.register(CommandName.LED_ON, led_on)
    router.register(CommandName.LED_OFF, led_off)
    return router


def main() -> None:
    parser = argparse.ArgumentParser(description="Free Motion pipe_check")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("FREEMOTION_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.from_env()
    if not cfg.allowed_chat_ids:
        LOG.warning(
            "TELEGRAM_ALLOWED_CHAT_IDS is empty - bot will reply to ANY chat. "
            "DM the bot, then copy the chat_id it echoes back into the env file."
        )

    led = Led(cfg.led_pin)
    if cfg.led_pin and not led.available:
        LOG.warning(
            "FREEMOTION_LED_PIN=%s but GPIO is not available on this host",
            cfg.led_pin,
        )

    router = build_router(cfg, led)
    agent = Agent(config=cfg, router=router)
    try:
        agent.run()
    finally:
        led.cleanup()


if __name__ == "__main__":
    main()
