#!/usr/bin/env python3
"""Free Motion pi_bench_demo (M4 Phase 2).

The first **real hardware** Free Motion device. Wires the runtime to a
Raspberry Pi via `PiHardwareController`:

    Config.from_env() -> make_controller_from_config() ->
        Router(arm/disarm/move/stop/status/capabilities/ping) ->
        Agent -> Telegram

Bench-safe by design. The Pi controller drives only two GPIO output
pins:

- ``armed_pin``  (default BCM 27) — HIGH while armed
- ``moving_pin`` (default BCM 22) — pulsed HIGH for ``move_pulse_s``
  on each successful ``move()``

Wire those pins to LEDs (or opto-isolated relay indicators). No motor
drivers, no propellers, no actuated platform. The "motion primitive"
is intentionally bench-safe — proving the runtime path end-to-end
without real motion. Real actuation lands later behind explicit safety
modes.

If `FREEMOTION_HARDWARE` is not ``"pi"``, the runtime falls back to a
`MockHardwareController` (so the demo works on any laptop for
development). A warning is logged so the misconfiguration is visible.

See ``examples/pi_bench_demo/README.md`` for the full operator
walkthrough. Wire format and command set match every other Free Motion
device — the only thing that changes here is the controller.
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
from freemotion.hardware import HardwareController, make_controller_from_config
from freemotion.protocol import CommandName
from freemotion.router import Router

LOG = logging.getLogger("freemotion.pi_bench_demo")


def build_router(cfg: Config, controller: HardwareController) -> Router:
    """Register exactly the M4 Phase 2 command set.

    The Phase 2 plan is brutally narrow on purpose:
    ``/capabilities``, ``/status``, ``/arm``, ``/move``, ``/stop``,
    ``/disarm``. ``/ping`` is registered too because every Free Motion
    device exposes it — it costs nothing and is the standard liveness
    probe.
    """
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
    parser = argparse.ArgumentParser(description="Free Motion pi_bench_demo")
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
            "DM the bot once to find your chat_id, then add it to the env file."
        )
    if cfg.hardware_profile != "pi":
        LOG.warning(
            "FREEMOTION_HARDWARE=%r; pi_bench_demo expects 'pi'. "
            "Falling back to a mock controller; no GPIO will be driven.",
            cfg.hardware_profile,
        )

    controller = make_controller_from_config(cfg)
    if cfg.hardware_profile == "pi" and not controller.available:
        LOG.warning(
            "PiHardwareController is offline (RPi.GPIO not importable, or "
            "GPIO setup failed). The agent will still run; arm/move will "
            "return False and /status will report connected: false."
        )

    router = build_router(cfg, controller)
    agent = Agent(config=cfg, router=router)
    try:
        agent.run()
    finally:
        cleanup = getattr(controller, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:  # pragma: no cover - hardware-specific
                LOG.warning("controller cleanup raised; ignoring", exc_info=True)


if __name__ == "__main__":
    main()
