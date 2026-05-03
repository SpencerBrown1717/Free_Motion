#!/usr/bin/env python3
"""Free Motion - pipe_check (M0, on top of protocol v0).

Smallest end-to-end demo: receive a Telegram message, parse it through
the v0 protocol, and reply. No motion, no vision, no models.

Runs on a Pi (chat + optional GPIO) and on a laptop (chat only).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import socket
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from freemotion import __version__ as FREEMOTION_VERSION
from freemotion.protocol import (
    Command,
    CommandName,
    Error,
    ErrorCode,
    ProtocolError,
    Reply,
    SafetyMode,
    parse_command_json,
    parse_slash,
    serialize_reply,
)

try:
    import RPi.GPIO as GPIO  # type: ignore[import-not-found]

    _GPIO_AVAILABLE = True
except Exception:
    GPIO = None  # type: ignore[assignment]
    _GPIO_AVAILABLE = False

LOG = logging.getLogger("freemotion.pipe_check")

CAPABILITIES = [
    CommandName.PING.value,
    CommandName.STATUS.value,
    CommandName.CAPABILITIES.value,
    CommandName.LED_ON.value,
    CommandName.LED_OFF.value,
    CommandName.DISARM.value,
    CommandName.STOP.value,
]


def _parse_chat_ids(raw: str) -> Set[int]:
    out: Set[int] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(int(piece))
        except ValueError:
            LOG.warning("ignoring non-integer chat id: %r", piece)
    return out


@dataclass(frozen=True)
class Config:
    token: str
    allowed_chat_ids: Set[int]
    led_pin: Optional[int]
    device_id: str
    safety_default: SafetyMode

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN is not set. See docs/pi-setup.md section 4."
            )
        allowed = _parse_chat_ids(
            os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        )
        led_raw = os.environ.get("FREEMOTION_LED_PIN", "").strip()
        led_pin: Optional[int] = None
        if led_raw:
            try:
                led_pin = int(led_raw)
            except ValueError:
                LOG.warning(
                    "ignoring non-integer FREEMOTION_LED_PIN: %r", led_raw
                )
        device_id = (
            os.environ.get("FREEMOTION_DEVICE_ID", "").strip()
            or socket.gethostname()
        )
        safety_raw = (
            os.environ.get("FREEMOTION_SAFETY_DEFAULT", "dry_run")
            .strip()
            .lower()
        )
        try:
            safety_default = SafetyMode(safety_raw)
        except ValueError:
            LOG.warning(
                "invalid FREEMOTION_SAFETY_DEFAULT=%r, falling back to dry_run",
                safety_raw,
            )
            safety_default = SafetyMode.DRY_RUN
        return cls(
            token=token,
            allowed_chat_ids=allowed,
            led_pin=led_pin,
            device_id=device_id,
            safety_default=safety_default,
        )


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


def is_authorized(update: Update, allowed: Set[int]) -> bool:
    """Open when allowlist is empty; otherwise the chat id must match."""
    if not allowed:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id in allowed


HELP_TEXT = (
    "Free Motion - pipe_check\n"
    "\n"
    "Slash commands:\n"
    "  /ping            round-trip check\n"
    "  /status          host info\n"
    "  /capabilities    what this device can do\n"
    "  /led on|off      toggle GPIO LED (Pi only)\n"
    "  /disarm          set state idle\n"
    "  /stop            hard stop, always honored\n"
    "\n"
    "JSON envelopes per docs/protocol.md are also accepted as plain text.\n"
    "Anything that is neither slash nor JSON is echoed back with your chat id."
)


def _reply_ok(
    cfg: Config,
    *,
    cid: str,
    state: str,
    message: str,
    telemetry: Optional[Dict[str, Any]] = None,
) -> Reply:
    return Reply(
        sender=cfg.device_id,
        state=state,
        ok=True,
        error=None,
        telemetry=telemetry or {},
        message=message,
        correlation_id=cid,
    )


def _reply_err(
    cfg: Config,
    *,
    cid: str,
    code: ErrorCode,
    message: str,
    state: str = "error",
) -> Reply:
    return Reply(
        sender=cfg.device_id,
        state=state,
        ok=False,
        error=Error(code=code, message=message),
        telemetry={},
        message=message,
        correlation_id=cid,
    )


def dispatch(cfg: Config, led: Led, cmd: Command) -> Reply:
    """Pure dispatch: Command in, Reply out, no I/O on the wire."""
    name = cmd.cmd
    cid = cmd.correlation_id

    if name == CommandName.STOP:
        led.set(False)
        return _reply_ok(cfg, cid=cid, state="idle", message="stopped")

    if name == CommandName.PING:
        return _reply_ok(cfg, cid=cid, state="idle", message="pong")

    if name == CommandName.STATUS:
        gpio_line = "no"
        if led.available:
            gpio_line = f"yes (pin {cfg.led_pin})"
        msg = (
            f"host: {socket.gethostname()}\n"
            f"system: {platform.system()} {platform.release()}\n"
            f"machine: {platform.machine()}\n"
            f"gpio: {gpio_line}\n"
            f"safety: {cfg.safety_default.value}\n"
            f"freemotion: {FREEMOTION_VERSION}"
        )
        return _reply_ok(cfg, cid=cid, state="idle", message=msg)

    if name == CommandName.CAPABILITIES:
        return _reply_ok(
            cfg,
            cid=cid,
            state="idle",
            message=f"capabilities: {', '.join(CAPABILITIES)}",
            telemetry={
                "device_id": cfg.device_id,
                "hardware": "pi" if _GPIO_AVAILABLE else "host",
                "software_version": FREEMOTION_VERSION,
                "capabilities": CAPABILITIES,
                "safety_default": cfg.safety_default.value,
            },
        )

    if name in (CommandName.LED_ON, CommandName.LED_OFF):
        target = "on" if name == CommandName.LED_ON else "off"
        if not led.available:
            return _reply_err(
                cfg,
                cid=cid,
                code=ErrorCode.UNSAFE_IN_MODE,
                message=(
                    "GPIO not available on this host "
                    "(set FREEMOTION_LED_PIN on a Pi)"
                ),
            )
        if cmd.safety == SafetyMode.DRY_RUN:
            return _reply_ok(
                cfg,
                cid=cid,
                state="idle",
                message=f"dry_run: would turn led {target}",
            )
        led.set(name == CommandName.LED_ON)
        return _reply_ok(
            cfg, cid=cid, state="idle", message=f"led {target}"
        )

    if name == CommandName.DISARM:
        return _reply_ok(cfg, cid=cid, state="idle", message="disarmed")

    if name == CommandName.ARM:
        return _reply_err(
            cfg,
            cid=cid,
            code=ErrorCode.UNSAFE_IN_MODE,
            message="pipe_check does not implement arm; this is a transport demo only",
        )

    return _reply_err(
        cfg,
        cid=cid,
        code=ErrorCode.INTERNAL,
        message=f"unhandled cmd: {name.value}",
    )


def _classify(text: str) -> str:
    """Return 'slash', 'json', 'plain', or 'empty'."""
    t = text.strip()
    if not t:
        return "empty"
    if t.startswith("/"):
        return "slash"
    if t.startswith("{"):
        return "json"
    return "plain"


def build_application(cfg: Config, led: Led) -> Application:
    app = ApplicationBuilder().token(cfg.token).build()

    async def on_message(
        update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        if msg is None or msg.text is None:
            return
        chat = update.effective_chat
        text = msg.text
        sender = f"chat:{chat.id}" if chat else "unknown"

        lower = text.strip().lower()
        if lower in {"/start", "/help"} or lower.startswith(("/start ", "/help ")):
            await msg.reply_text(HELP_TEXT)
            return

        kind = _classify(text)

        if kind in ("plain", "empty"):
            await msg.reply_text(
                f"echo: {text}\n"
                f"chat_id: {chat.id if chat else 'unknown'}\n"
                "send /help for usage; lock the bot down by adding this id "
                "to TELEGRAM_ALLOWED_CHAT_IDS."
            )
            return

        if not is_authorized(update, cfg.allowed_chat_ids):
            unauth = _reply_err(
                cfg,
                cid="unauthorized",
                code=ErrorCode.UNAUTHORIZED,
                message=f"unauthorized chat (id={chat.id if chat else 'unknown'})",
            )
            if kind == "json":
                await msg.reply_text(serialize_reply(unauth))
            else:
                await msg.reply_text(unauth.message)
            return

        try:
            if kind == "slash":
                cmd = parse_slash(
                    text, sender=sender, default_safety=cfg.safety_default
                )
            else:
                cmd = parse_command_json(text)
        except ProtocolError as exc:
            err_reply = _reply_err(
                cfg,
                cid="parse-error",
                code=exc.code,
                message=exc.message,
            )
            if kind == "json":
                await msg.reply_text(serialize_reply(err_reply))
            else:
                await msg.reply_text(f"error: {exc.message}")
            return

        try:
            reply = dispatch(cfg, led, cmd)
        except Exception as exc:  # pragma: no cover - safety net
            LOG.exception("internal error in dispatch")
            reply = _reply_err(
                cfg,
                cid=cmd.correlation_id,
                code=ErrorCode.INTERNAL,
                message=f"internal: {exc}",
            )

        if kind == "json":
            await msg.reply_text(serialize_reply(reply))
        else:
            await msg.reply_text(reply.message)

    app.add_handler(MessageHandler(filters.TEXT, on_message))
    return app


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

    LOG.info(
        "device_id=%s safety_default=%s gpio=%s freemotion=%s",
        cfg.device_id,
        cfg.safety_default.value,
        "yes" if led.available else "no",
        FREEMOTION_VERSION,
    )

    app = build_application(cfg, led)
    LOG.info("starting long polling")
    try:
        app.run_polling()
    finally:
        led.cleanup()


if __name__ == "__main__":
    main()
