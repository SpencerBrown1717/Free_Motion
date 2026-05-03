"""Free Motion device agent.

`Agent` wires Telegram transport to `Router` + `Config`. The pure
message-handling logic is in `handle_text` so it can be unit-tested
without spinning up a Telegram client.
"""

from __future__ import annotations

import logging
from typing import FrozenSet, Optional

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from freemotion.config import Config
from freemotion.protocol import (
    Error,
    ErrorCode,
    ProtocolError,
    Reply,
    parse_command_json,
    parse_slash,
    serialize_reply,
)
from freemotion.router import Router

LOG = logging.getLogger("freemotion.agent")

HELP_TEXT = (
    "Free Motion device agent\n"
    "\n"
    "Slash commands available depend on the device's registered handlers.\n"
    "Try /capabilities to list them, or /help for this message.\n"
    "JSON envelopes per docs/protocol.md are also accepted as plain text.\n"
    "Anything that is neither slash nor JSON is echoed back with your chat id."
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


def is_authorized(
    chat_id: Optional[int], allowed: FrozenSet[int]
) -> bool:
    """Open when allowlist is empty; otherwise the chat id must match."""
    if not allowed:
        return True
    return chat_id is not None and chat_id in allowed


def _err_reply(
    config: Config, *, cid: str, code: ErrorCode, message: str
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


def handle_text(
    *,
    text: str,
    chat_id: Optional[int],
    config: Config,
    router: Router,
) -> str:
    """Pure message handler.

    Takes the raw text and chat id from the transport, returns the exact
    string to send back. Slash callers get a human-readable message;
    JSON callers get a serialized reply envelope. Plain text gets an
    echo (preserves the M0 onboarding behavior).
    """
    sender = f"chat:{chat_id}" if chat_id is not None else "unknown"

    lower = text.strip().lower()
    if lower in {"/start", "/help"} or lower.startswith(("/start ", "/help ")):
        return HELP_TEXT

    kind = _classify(text)

    if kind in ("plain", "empty"):
        return (
            f"echo: {text}\n"
            f"chat_id: {chat_id if chat_id is not None else 'unknown'}\n"
            "send /help for usage; lock the bot down by adding this id to "
            "TELEGRAM_ALLOWED_CHAT_IDS."
        )

    if not is_authorized(chat_id, config.allowed_chat_ids):
        unauth = _err_reply(
            config,
            cid="unauthorized",
            code=ErrorCode.UNAUTHORIZED,
            message=(
                f"unauthorized chat (id="
                f"{chat_id if chat_id is not None else 'unknown'})"
            ),
        )
        if kind == "json":
            return serialize_reply(unauth)
        return unauth.message

    try:
        if kind == "slash":
            cmd = parse_slash(
                text, sender=sender, default_safety=config.safety_default
            )
        else:
            cmd = parse_command_json(text)
    except ProtocolError as exc:
        err = _err_reply(
            config, cid="parse-error", code=exc.code, message=exc.message
        )
        if kind == "json":
            return serialize_reply(err)
        return f"error: {exc.message}"

    reply = router.dispatch(cmd)

    if kind == "json":
        return serialize_reply(reply)
    return reply.message


class Agent:
    """Telegram-backed device agent on top of `Router` + `Config`."""

    def __init__(self, *, config: Config, router: Router) -> None:
        self.config = config
        self.router = router

    def build_application(self) -> Application:
        app = ApplicationBuilder().token(self.config.token).build()
        app.add_handler(MessageHandler(filters.TEXT, self._on_message))
        return app

    def run(self) -> None:
        app = self.build_application()
        LOG.info(
            "device_id=%s safety_default=%s hardware=%s known=%s",
            self.config.device_id,
            self.config.safety_default.value,
            self.config.hardware_profile,
            ",".join(self.router.known) or "(none)",
        )
        LOG.info("starting long polling")
        app.run_polling()

    async def _on_message(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        if msg is None or msg.text is None:
            return
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        out = handle_text(
            text=msg.text,
            chat_id=chat_id,
            config=self.config,
            router=self.router,
        )
        await msg.reply_text(out)
