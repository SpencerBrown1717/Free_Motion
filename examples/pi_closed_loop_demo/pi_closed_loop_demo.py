#!/usr/bin/env python3
"""Free Motion pi_closed_loop_demo (Step 2 — full Pi closed loop).

The first **end-to-end** Free Motion device. Wires every piece that
shipped before this step into one runtime:

    Telegram
        -> Agent + Router
            -> /mission_start ->  MissionLoop
                                    PiCameraSource (picamera2)
                                        -> YoloVision (ultralytics)
                                            -> WorldState
                                                -> GemmaMissionControl
                                                    -> MissionDecision (MOVE)
                                                        -> Router.dispatch
                                                            -> SafetyGate
                                                                -> PiHardwareController
                                                                    -> GPIO pulse
            -> /status -> hardware state + mission_loop telemetry
            -> /stop   -> halts the loop AND drops both pins LOW

Bench-safe by design — same as `pi_bench_demo`. The only motion
primitive is `make_move_handler` driving the bench `moving_pin` HIGH
for ~100ms. No motors, no propellers. The loop is **only** allowed to
dispatch `MOVE` (ADR-0010); ARM / DISARM / STOP stay operator-driven
through Telegram so an LLM hallucination cannot arm or disarm the
device.

`/stop` is the master kill: it halts the mission loop **and** drives
both pins LOW unconditionally. ADR-0006 (SafetyGate) and ADR-0010
(MissionLoop) are the canonical references.

If `FREEMOTION_HARDWARE` is not ``"pi"``, `make_controller_from_config`
falls back to a `MockHardwareController` so the demo runs on any host
for development. Same for the camera — a missing `picamera2` causes
`PiCameraSource` to be offline; the demo logs and exits with a
non-zero code rather than starting a useless loop.

See `examples/pi_closed_loop_demo/README.md` for the operator
walkthrough.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any, Optional

from freemotion.agent import (
    Agent,
    MissionLoop,
    make_arm_handler,
    make_capabilities_handler,
    make_disarm_handler,
    make_mission_start_handler,
    make_move_handler,
    make_ping_handler,
    make_status_handler,
    make_stop_handler,
)
from freemotion.config import Config
from freemotion.hardware import (
    HardwareController,
    SafetyGate,
    make_controller_from_config,
)
from freemotion.mission_control import (
    MissionPolicy,
    make_mission_from_config,
)
from freemotion.protocol import CommandName
from freemotion.router import Router
from freemotion.vision import (
    PiCameraSource,
    VisionBackend,
    make_vision_from_config,
)
from freemotion.world import WorldState

LOG = logging.getLogger("freemotion.pi_closed_loop_demo")


def build_router_without_loop(
    cfg: Config,
    *,
    controller: HardwareController,
    on_stop: Any,
) -> Router:
    """Register every command **except** ``mission_start``.

    The mission loop and the router need each other (the loop calls
    `router.dispatch(...)`; `mission_start` needs the loop), so we
    register everything that doesn't need the loop here, then add the
    `mission_start` handler in `attach_mission_loop`. This is the same
    pattern `make_capabilities_handler(cfg, router)` already uses.

    `on_stop` is the composite "kill everything" callback — typically
    a closure that stops the mission loop *first* (no tick can race a
    fresh dispatch), then drives the controller pins LOW. Exceptions
    from either are swallowed by `make_stop_handler` — `/stop` always
    acks.
    """
    router = Router(
        device_id=cfg.device_id,
        denied_commands=cfg.denied_commands,
    )
    router.register(CommandName.PING, make_ping_handler(cfg))
    router.register(
        CommandName.CAPABILITIES, make_capabilities_handler(cfg, router)
    )
    router.register(
        CommandName.STOP,
        make_stop_handler(cfg, on_stop=on_stop),
    )
    router.register(CommandName.ARM, make_arm_handler(cfg, controller))
    router.register(CommandName.DISARM, make_disarm_handler(cfg, controller))
    router.register(CommandName.MOVE, make_move_handler(cfg, controller))
    return router


def attach_mission_loop(
    router: Router,
    *,
    cfg: Config,
    controller: HardwareController,
    mission_loop: MissionLoop,
    default_intent: str,
) -> None:
    """Register the loop-aware handlers (`status`, `mission_start`).

    `status` is registered here (not in `build_router_without_loop`)
    so its closure captures the real `mission_loop`. The result is a
    single `/status` reply that carries both hardware state
    (`telemetry.controller`) and mission-loop state
    (`telemetry.mission_loop`).
    """
    router.register(
        CommandName.STATUS,
        make_status_handler(
            cfg, controller=controller, mission_loop=mission_loop
        ),
    )
    router.register(
        CommandName.MISSION_START,
        make_mission_start_handler(
            cfg, mission_loop=mission_loop, default_intent=default_intent
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Free Motion pi_closed_loop_demo"
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("FREEMOTION_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO)",
    )
    parser.add_argument(
        "--tick-interval",
        type=float,
        default=float(
            os.environ.get("FREEMOTION_MISSION_TICK_INTERVAL_S", "1.0")
        ),
        help=(
            "Seconds between mission-loop ticks "
            "(default: 1.0; env FREEMOTION_MISSION_TICK_INTERVAL_S)"
        ),
    )
    parser.add_argument(
        "--default-intent",
        default=os.environ.get(
            "FREEMOTION_DEFAULT_INTENT", "follow person"
        ),
        help=(
            "Intent string used when /mission_start is sent without args "
            "(default: 'follow person'; env FREEMOTION_DEFAULT_INTENT)"
        ),
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
            "FREEMOTION_HARDWARE=%r; pi_closed_loop_demo expects 'pi'. "
            "Falling back to a mock controller; no GPIO will be driven.",
            cfg.hardware_profile,
        )

    # Camera. PiCameraSource is fail-offline: a missing picamera2 or a
    # camera that won't `start()` leaves `cam.available == False` and
    # capture returns None. We don't start the agent in that state —
    # the loop's whole job is to act on perception, and silently
    # running a perception-blind loop on real hardware is the wrong
    # default.
    cam = PiCameraSource()
    if not cam.available:
        LOG.error(
            "PiCameraSource is offline. Install picamera2 with "
            "`pip install -e .[picam]` on a Raspberry Pi running "
            "Bullseye or newer, and confirm the camera is wired in."
        )
        return 2

    # Vision. `make_vision_from_config` lazy-imports YoloVision when
    # FREEMOTION_VISION_BACKEND=yolo, otherwise returns MockVision (a
    # useful escape hatch for off-Pi development). The Pi camera is
    # injected as the frame source.
    vision: VisionBackend = make_vision_from_config(
        cfg, frame_source=cam
    )
    if not vision.available:
        # YoloVision sets available=False if ultralytics is missing or
        # the model file can't load. A mock vision is always available.
        LOG.error(
            "VisionBackend %r is offline. For YOLO: install with "
            "`pip install -e .[yolo]` and confirm the model file is "
            "reachable. The closed loop refuses to start without "
            "perception.",
            getattr(vision, "name", "?"),
        )
        cam.close()
        return 3

    # Mission control. Lazy-imports GemmaMissionControl when
    # FREEMOTION_MISSION_BACKEND=gemma; otherwise MockMissionControl
    # (rule-based, no model load). Same fail-offline contract: a
    # broken Gemma load yields `mission.available == False` but
    # `plan(...)` still returns idle MissionDecisions instead of
    # raising — see ADR-0008. So an unavailable mission backend is
    # warned about, not fatal.
    mission: MissionPolicy = make_mission_from_config(cfg)
    if not getattr(mission, "available", True):
        LOG.warning(
            "MissionPolicy %r is offline (model load failed?). "
            "The loop will run but every plan() will return idle. "
            "This is safe — no MOVE will be dispatched.",
            getattr(mission, "name", "?"),
        )

    # World state.
    world = WorldState()

    # Hardware. Same wiring as pi_bench_demo: real PiHardwareController
    # behind SafetyGate so dry_run blocks actuation regardless of any
    # per-command override.
    inner = make_controller_from_config(cfg)
    if cfg.hardware_profile == "pi" and not inner.available:
        LOG.warning(
            "PiHardwareController is offline (RPi.GPIO not importable, or "
            "GPIO setup failed). The agent will still run; arm/move will "
            "return False and /status will report connected: false."
        )
    controller = SafetyGate(inner, cfg.safety_default)
    LOG.info(
        "SafetyGate active: safety=%s; arm/move %s",
        cfg.safety_default.value,
        "refused (dry_run)"
        if cfg.safety_default.value == "dry_run"
        else "permitted",
    )

    # Build the router first (without `mission_start` / `status`),
    # then build the loop with the real router, then attach the
    # loop-aware handlers. This avoids the loop-router circular wiring
    # without leaking placeholders into either object.
    def _stop_everything() -> None:
        # Order matters: loop FIRST (no tick can race), THEN controller.
        try:
            mission_loop.stop()  # noqa: F821 - bound below before any /stop fires
        except Exception:  # pragma: no cover - defensive
            LOG.warning("mission_loop.stop raised; ignoring", exc_info=True)
        try:
            controller.stop()
        except Exception:  # pragma: no cover - defensive
            LOG.warning("controller.stop raised; ignoring", exc_info=True)

    router = build_router_without_loop(
        cfg, controller=controller, on_stop=_stop_everything
    )

    mission_loop = MissionLoop(
        vision=vision,
        mission=mission,
        world=world,
        router=router,
        cfg=cfg,
        tick_interval_s=args.tick_interval,
    )

    attach_mission_loop(
        router,
        cfg=cfg,
        controller=controller,
        mission_loop=mission_loop,
        default_intent=args.default_intent,
    )

    LOG.info(
        "pi_closed_loop_demo wired: vision=%s mission=%s tick=%.2fs "
        "default_intent=%r",
        getattr(vision, "name", "?"),
        getattr(mission, "name", "?"),
        args.tick_interval,
        args.default_intent,
    )

    agent = Agent(config=cfg, router=router)
    try:
        agent.run()
    finally:
        graceful_shutdown(
            mission_loop=mission_loop,
            controller=controller,
            cam=cam,
            inner=inner,
        )

    return 0


def graceful_shutdown(
    *,
    mission_loop: Any,
    controller: Any,
    cam: Any,
    inner: Any,
) -> None:
    """Step 3: idempotent, ordered teardown for SIGINT / SIGTERM / `/stop`.

    Order matters because the closed loop has overlapping resources:

    1. **Mission loop first.** A still-ticking loop must not dispatch
       a fresh MOVE *after* the camera or the controller has been
       torn down. `MissionLoop.stop()` sets the stop event and joins
       the worker thread within `join_timeout_s`; if the worker is
       hung mid-`mission.plan()`, the helper logs and continues —
       the daemon thread will be reaped on process exit.
    2. **Hardware controller stop.** Drives both pins LOW
       unconditionally. `SafetyGate.stop()` always passes through
       (ADR-0006), so even in `dry_run` this drops the bench LEDs.
       Independent from `mission_loop.stop()` so a SIGTERM during a
       hung tick still drops the pins.
    3. **Camera close.** Idempotent (ADR-0009). Releases libcamera
       resources so the next process start finds the camera free.
    4. **Inner controller cleanup.** `PiHardwareController.cleanup()`
       releases `RPi.GPIO` (M4). Mock controllers don't implement
       `cleanup()`; the helper checks `hasattr` to stay polymorphic.

    Every step swallows its own exceptions: a single broken layer
    cannot block the rest of the teardown. The function is **safe
    to call from any thread, including a signal handler context**,
    because every underlying primitive (`Event.set`, `Thread.join`,
    GPIO writes, picamera2.close) is itself signal-safe in practice.
    """
    try:
        mission_loop.stop()
    except Exception:  # pragma: no cover - defensive
        LOG.warning("mission_loop.stop raised; ignoring", exc_info=True)
    try:
        controller.stop()
    except Exception:  # pragma: no cover - defensive
        LOG.warning("controller.stop raised; ignoring", exc_info=True)
    try:
        cam.close()
    except Exception:  # pragma: no cover - defensive
        LOG.warning("camera.close raised; ignoring", exc_info=True)
    cleanup = getattr(inner, "cleanup", None)
    if callable(cleanup):
        try:
            cleanup()
        except Exception:  # pragma: no cover - hardware-specific
            LOG.warning(
                "controller cleanup raised; ignoring", exc_info=True
            )


if __name__ == "__main__":
    raise SystemExit(main())
