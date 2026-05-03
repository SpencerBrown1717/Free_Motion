"""Built-in command handlers for the Free Motion router.

Device-agnostic handlers ship here. Hardware-specific handlers (LED,
motors, cameras, etc.) live with the device that owns them, except for
the four motion handlers that operate on a `HardwareController`
abstraction (`arm`, `disarm`, `stop`, `move`) — those are general
enough to live with the framework.
"""

from __future__ import annotations

import platform
import socket
import time
from typing import Any, Callable, Dict, Optional

from freemotion import __version__
from freemotion.config import Config
from freemotion.hardware import HardwareController
from freemotion.protocol import (
    Command,
    Error,
    ErrorCode,
    Reply,
    SafetyMode,
)
from freemotion.router import Handler, Router

_BOOT_TS = time.time()


def _ok(
    config: Config,
    cmd: Command,
    *,
    state: str = "idle",
    message: str = "",
    telemetry: Optional[Dict[str, Any]] = None,
) -> Reply:
    return Reply(
        sender=config.device_id,
        state=state,
        ok=True,
        error=None,
        telemetry=telemetry or {},
        message=message,
        correlation_id=cmd.correlation_id,
    )


def _err(
    config: Config,
    cmd: Command,
    *,
    code: ErrorCode,
    message: str,
    state: str = "error",
) -> Reply:
    return Reply(
        sender=config.device_id,
        state=state,
        ok=False,
        error=Error(code=code, message=message),
        telemetry={},
        message=message,
        correlation_id=cmd.correlation_id,
    )


def make_ping_handler(config: Config) -> Handler:
    def handler(cmd: Command) -> Reply:
        return _ok(config, cmd, message="pong")

    return handler


def make_stop_handler(
    config: Config, *, on_stop: Optional[Callable[[], None]] = None
) -> Handler:
    """`stop` is honored unconditionally per protocol.

    `on_stop` is the device-local hook for "actually halt anything that
    can move." Exceptions in the hook are swallowed so `stop` never
    fails to ack.
    """

    def handler(cmd: Command) -> Reply:
        if on_stop is not None:
            try:
                on_stop()
            except Exception:
                pass
        return _ok(config, cmd, message="stopped")

    return handler


def make_status_handler(
    config: Config,
    *,
    gpio_available: bool = False,
    controller: Optional[HardwareController] = None,
) -> Handler:
    """Status with optional hardware-controller telemetry.

    `gpio_available` and `controller` are independent: a Pi with an LED
    and no controller passes `gpio_available=True`; a mock device with
    no GPIO passes `controller=mock`. Devices with both can pass both.
    """

    def handler(cmd: Command) -> Reply:
        uptime = max(0, int(time.time() - _BOOT_TS))
        message_parts = [
            f"device: {config.device_id}",
            f"hardware: {config.hardware_profile}",
            f"system: {platform.system()} {platform.release()}",
            f"machine: {platform.machine()}",
            f"safety: {config.safety_default.value}",
            f"freemotion: {__version__}",
            f"uptime_s: {uptime}",
        ]
        telemetry: Dict[str, Any] = {
            "device_id": config.device_id,
            "hardware": config.hardware_profile,
            "software_version": __version__,
            "safety_default": config.safety_default.value,
            "uptime_s": uptime,
            "hostname": socket.gethostname(),
            "gpio_available": gpio_available,
        }
        if controller is not None:
            ctl_state = controller.state()
            telemetry["controller"] = ctl_state
            armed = ctl_state.get("armed")
            if armed is not None:
                message_parts.append(f"armed: {'yes' if armed else 'no'}")
        else:
            message_parts.append(f"gpio: {'yes' if gpio_available else 'no'}")
        return _ok(
            config,
            cmd,
            message="\n".join(message_parts),
            telemetry=telemetry,
        )

    return handler


def make_capabilities_handler(config: Config, router: Router) -> Handler:
    """Self-description per docs/protocol.md#device-registration."""

    def handler(cmd: Command) -> Reply:
        cmds = router.known
        return _ok(
            config,
            cmd,
            message=f"capabilities: {', '.join(cmds)}",
            telemetry={
                "device_id": config.device_id,
                "hardware": config.hardware_profile,
                "software_version": __version__,
                "capabilities": cmds,
                "safety_default": config.safety_default.value,
            },
        )

    return handler


def make_arm_handler(config: Config, controller: HardwareController) -> Handler:
    def handler(cmd: Command) -> Reply:
        if cmd.safety == SafetyMode.DRY_RUN:
            return _err(
                config,
                cmd,
                code=ErrorCode.UNSAFE_IN_MODE,
                message="arm refused in dry_run; set safety to bench or live",
            )
        if not controller.arm():
            return _err(
                config,
                cmd,
                code=ErrorCode.UNSAFE_IN_MODE,
                message="arm refused by controller (battery? config?)",
            )
        return _ok(
            config,
            cmd,
            state="armed",
            message="armed",
            telemetry=controller.state(),
        )

    return handler


def make_disarm_handler(
    config: Config, controller: HardwareController
) -> Handler:
    def handler(cmd: Command) -> Reply:
        controller.disarm()
        return _ok(
            config,
            cmd,
            state="idle",
            message="disarmed",
            telemetry=controller.state(),
        )

    return handler


def make_move_handler(
    config: Config, controller: HardwareController
) -> Handler:
    def handler(cmd: Command) -> Reply:
        try:
            x = float(cmd.args.get("x", 0))
            y = float(cmd.args.get("y", 0))
            z = float(cmd.args.get("z", 0))
        except (TypeError, ValueError):
            return _err(
                config,
                cmd,
                code=ErrorCode.BAD_ARGS,
                message="move requires numeric x, y, z",
            )
        if cmd.safety == SafetyMode.DRY_RUN:
            return _ok(
                config,
                cmd,
                state="idle",
                message=f"dry_run: would move ({x}, {y}, {z})",
                telemetry=controller.state(),
            )
        if not controller.move(x, y, z):
            return _err(
                config,
                cmd,
                code=ErrorCode.UNSAFE_IN_MODE,
                message="move refused (not armed? insufficient battery?)",
            )
        return _ok(
            config,
            cmd,
            state="moving",
            message=f"moved ({x}, {y}, {z})",
            telemetry=controller.state(),
        )

    return handler
