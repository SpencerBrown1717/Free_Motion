"""Free Motion local sim demo: closes the M3 loop on mocks.

Run:

    python examples/local_sim_demo.py

No hardware. No Telegram. No env vars. No model download. Just the
real protocol, router, and handlers wired to the mock backends:

    intent -> vision -> mission_control -> protocol -> router -> hardware -> state

Why this exists:

- A stranger should be able to clone the repo and see Free Motion run
  in under a minute, with no setup beyond `pip install -e .`.
- Every layer used here is the same code path a real device runs.
  Only the backends change: MockVision -> YoloVision,
  MockMissionControl -> GemmaMissionControl, MockHardwareController ->
  PiHardwareController. See docs/models.md for the swap path.
- It's a shared reference for tests, demos, and contributors. If this
  script breaks, something fundamental broke.
"""

from __future__ import annotations

import sys

from freemotion.agent.builtins import (
    make_arm_handler,
    make_disarm_handler,
    make_move_handler,
    make_stop_handler,
)
from freemotion.config import Config
from freemotion.hardware import MockHardwareController
from freemotion.mission_control import MockMissionControl
from freemotion.protocol import (
    Command,
    CommandName,
    SafetyMode,
    new_id,
    serialize_command,
    serialize_reply,
)
from freemotion.router import Router
from freemotion.vision import Detection, MockVision, VisionResult
from freemotion.world import WorldState


def _hr(label: str = "") -> None:
    bar = "─" * 72
    if label:
        print(f"\n{bar}\n  {label}\n{bar}")
    else:
        print(bar)


def _build_config() -> Config:
    # Direct construction is supported for tests and demos. `from_env`
    # is the runtime path. We pass safety=BENCH so arm/move actually
    # execute against the mock; DRY_RUN would short-circuit them.
    return Config(
        token="local-sim",
        device_id="local-sim",
        hardware_profile="mock",
        safety_default=SafetyMode.BENCH,
    )


def _build_router(config: Config, controller: MockHardwareController) -> Router:
    router = Router(device_id=config.device_id)
    router.register(
        CommandName.STOP, make_stop_handler(config, on_stop=controller.stop)
    )
    router.register(CommandName.ARM, make_arm_handler(config, controller))
    router.register(
        CommandName.DISARM, make_disarm_handler(config, controller)
    )
    router.register(CommandName.MOVE, make_move_handler(config, controller))
    return router


def _dispatch(
    router: Router,
    name: CommandName,
    args: dict,
    *,
    sender: str,
) -> None:
    cmd = Command(
        cmd=name,
        args=dict(args),
        sender=sender,
        safety=SafetyMode.BENCH,
        correlation_id=new_id(),
    )
    print(f"  -> command : {serialize_command(cmd)}")
    reply = router.dispatch(cmd)
    print(f"  <- reply   : {serialize_reply(reply)}")


def main() -> int:
    config = _build_config()
    controller = MockHardwareController()
    router = _build_router(config, controller)
    world = WorldState()

    vision = MockVision(
        scripted=[
            VisionResult(
                detections=(
                    Detection(
                        label="person",
                        confidence=0.92,
                        bbox=(0.45, 0.35, 0.20, 0.45),
                    ),
                )
            ),
            VisionResult(detections=()),
            VisionResult(detections=()),
            VisionResult(detections=()),
            VisionResult(detections=()),
        ]
    )
    mission = MockMissionControl()

    _hr("setup: arm the device")
    _dispatch(router, CommandName.ARM, {}, sender="local-sim")
    world.update(current_state="armed")
    print(f"  state    : {controller.state()}")
    print(f"  world    : {world.snapshot()}")

    intents = [
        "follow person",
        "follow person",
        "party time",
        "stop",
        "disarm",
    ]

    for i, intent in enumerate(intents, start=1):
        scene = vision.scene()

        # Vision -> world: record what we just saw (highest-confidence
        # detection wins). Demonstrates the vision -> world hop the same
        # way a real device would do it.
        for det in sorted(
            scene.detections, key=lambda d: d.confidence, reverse=True
        )[:1]:
            world.see(det.label, confidence=det.confidence)

        snapshot = world.snapshot()
        decision = mission.plan(intent=intent, scene=scene, world=snapshot)

        _hr(f"tick {i}: intent = {intent!r}")
        print(f"  vision   : {len(scene.detections)} detection(s)")
        for det in scene.detections:
            print(
                f"             - {det.label} "
                f"(conf={det.confidence:.2f}, bbox={det.bbox})"
            )
        next_label = (
            decision.next_command.value if decision.next_command else "idle"
        )
        print(
            f"  mission  : next={next_label}  "
            f"args={dict(decision.args)}  "
            f"conf={decision.confidence:.2f}"
        )
        print(f"             reason: {decision.reason}")

        if decision.next_command is None:
            world.update(next_action=None)
            print("  router   : skipped (idle)")
        else:
            world.update(next_action=decision.next_command.value)
            _dispatch(
                router,
                decision.next_command,
                decision.args,
                sender="local-sim",
            )
            # Reflect the post-dispatch hardware state into world.
            ctl = controller.state()
            world.update(
                current_state="moving"
                if ctl["armed"] and decision.next_command == CommandName.MOVE
                else ("armed" if ctl["armed"] else "idle")
            )

        print(f"  state    : {controller.state()}")
        print(f"  world    : {world.snapshot()}")

    _hr("done")
    print(
        "Same loop a real device runs.\n"
        "Swap path: docs/models.md\n"
        "Architecture: docs/architecture.md\n"
        "Try the Telegram path: examples/mock_drone/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
