#!/usr/bin/env python3
"""Free Motion mock_drone — no-hardware reference.

A device whose state lives entirely in memory. Run this on any laptop,
DM the bot, watch fake state change. Useful for:

- contributors who don't own a Pi
- CI / integration tests
- demo videos without flying anything

Wire format and command set are unchanged from a real device. The only
difference is `MockHardwareController` instead of a real `HardwareController`.
"""

from __future__ import annotations

import argparse
import logging
import os

from freemotion.agent import (
    Agent,
    make_arm_handler,
    make_capabilities_handler,
    make_disarm_handler,
    make_move_handler,
    make_ping_handler,
    make_status_handler,
    make_stop_handler,
)
from freemotion.config import Config
from freemotion.hardware import MockHardwareController
from freemotion.protocol import CommandName
from freemotion.router import Router

LOG = logging.getLogger("freemotion.mock_drone")


def build_router(cfg: Config, controller: MockHardwareController) -> Router:
    router = Router(
        device_id=cfg.device_id,
        denied_commands=cfg.denied_commands,
    )
    router.register(CommandName.PING, make_ping_handler(cfg))
    router.register(
        CommandName.STATUS,
        make_status_handler(cfg, controller=controller),
    )
    router.register(
        CommandName.CAPABILITIES, make_capabilities_handler(cfg, router)
    )
    router.register(
        CommandName.STOP,
        make_stop_handler(cfg, on_stop=controller.stop),
    )
    router.register(CommandName.ARM, make_arm_handler(cfg, controller))
    router.register(CommandName.DISARM, make_disarm_handler(cfg, controller))
    router.register(CommandName.MOVE, make_move_handler(cfg, controller))
    return router


def main() -> None:
    parser = argparse.ArgumentParser(description="Free Motion mock_drone")
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

    controller = MockHardwareController()
    router = build_router(cfg, controller)
    Agent(config=cfg, router=router).run()


if __name__ == "__main__":
    main()
