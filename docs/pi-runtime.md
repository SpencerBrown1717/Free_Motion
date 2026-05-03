# Pi runtime

How to write a Free Motion device on top of `freemotion.config`, `freemotion.router`, and `freemotion.agent`. Aimed at contributors who can read Python and just want to ship something.

> **Looking for the canonical Pi reference architecture?** This page covers **building** a device on the runtime. The locked contract for what a Pi Free Motion device *is* — supported commands, hardware path, model path, env-var contract, safety contract — lives in [`docs/pi-reference.md`](pi-reference.md). Read both: this page tells you how to build, that one tells you what to conform to.

## TL;DR

A device is **three things plus your hardware**:

```python
from freemotion.agent import Agent, make_ping_handler
from freemotion.config import Config
from freemotion.protocol import CommandName
from freemotion.router import Router

cfg = Config.from_env()
router = Router(device_id=cfg.device_id)
router.register(CommandName.PING, make_ping_handler(cfg))
Agent(config=cfg, router=router).run()
```

That's a complete (if minimal) device. Add handlers for the commands you care about, attach hardware where you have it, and you're done. Everything else in this document is detail.

## Mental model

```text
Telegram message
    │
    ▼
Agent (transport I/O, auth, classify, log)
    │
    ▼
parse_slash / parse_command_json   ──▶  Command (typed)
    │
    ▼
Router.dispatch(cmd)
    │
    ▼
your handler                       ──▶  Reply (typed)
    │
    ▼
serialize_reply  /  reply.message  ──▶  back to Telegram
```

The shape of a `Command` and `Reply` is fixed by [docs/protocol.md](protocol.md). The agent and router are the thinnest possible layer between Telegram and your code.

## The three layers

### 1. `Config` — read once, frozen forever

A frozen dataclass populated from environment variables.

```python
from freemotion.config import Config
cfg = Config.from_env()        # uses os.environ
cfg = Config.from_env(env={…}) # for tests
```

Fields and the env vars that fill them:

| Field | Env var | Required | Default |
|---|---|---|---|
| `token` | `TELEGRAM_BOT_TOKEN` | yes | — |
| `allowed_chat_ids` | `TELEGRAM_ALLOWED_CHAT_IDS` (CSV) | no | empty (open) |
| `device_id` | `FREEMOTION_DEVICE_ID` | no | `socket.gethostname()` |
| `safety_default` | `FREEMOTION_SAFETY_DEFAULT` | no | `dry_run` |
| `led_pin` | `FREEMOTION_LED_PIN` (BCM int) | no | `None` |
| `hardware_profile` | `FREEMOTION_HARDWARE` | no | `host` (suggested values: `host`, `mock`, `pi`) |
| `pi_armed_pin` | `FREEMOTION_PI_ARMED_PIN` (BCM int) | no | `None` (controller default: BCM 27) |
| `pi_moving_pin` | `FREEMOTION_PI_MOVING_PIN` (BCM int) | no | `None` (controller default: BCM 22) |
| `vision_backend` | `FREEMOTION_VISION_BACKEND` | no | `mock` (also accepts `yolo`; see [`docs/models.md`](models.md)) |
| `mission_backend` | `FREEMOTION_MISSION_BACKEND` | no | `mock` (also accepts `gemma`; see [`docs/models.md`](models.md)) |
| `enabled_features` | `FREEMOTION_FEATURES` (CSV) | no | empty |
| `denied_commands` | `FREEMOTION_DENIED_COMMANDS` (CSV) | no | empty (no commands denied) |

**Per-command deny list.** Listed wire command names are refused at dispatch time with `error.code = "denied_by_policy"`. Useful when a device is configured for one role (e.g. vision-only Pi) and should reject commands its handlers would otherwise execute. `stop` is exempt unconditionally — listing it issues a warning and is dropped. See [ADR-0004](decisions.md#adr-0004--per-command-allowdeny-allow-by-default-explicit-deny-list-stop-always-exempt--2026-05-03).

```bash
# refuse arm and move regardless of registered handlers
export FREEMOTION_DENIED_COMMANDS="arm,move"
```

Bad values are warned and fall back to defaults. Missing token raises `SystemExit`.

### 2. `Router` — pure dispatch

```python
from freemotion.router import Router
from freemotion.protocol import CommandName

router = Router(device_id=cfg.device_id)
router.register(CommandName.PING, my_handler)
reply = router.dispatch(cmd)   # no I/O, returns Reply
```

Properties of the router:

- **Total dispatch.** Unknown commands return an `unknown_cmd` reply. Handler exceptions are caught and surfaced as `internal` replies. Both preserve `cmd.correlation_id`.
- **`router.known`** is the sorted list of registered command names — the source of truth for `/capabilities`.
- **`register` is strict.** Duplicate registration raises `RouterError`.

A handler is just `Callable[[Command], Reply]`. Nothing more.

### 3. `Agent` — Telegram transport + the message lifecycle

```python
Agent(config=cfg, router=router).run()
```

The agent owns:

- Telegram long polling
- Classifying each message as `slash` / `json` / `plain` / `empty`
- `/start` and `/help` UX (returns `HELP_TEXT`)
- Auth via `Config.allowed_chat_ids`
- Calling the router and formatting the reply (slash → `reply.message`, JSON → serialized envelope)

The pure logic is the free function `freemotion.agent.handle_text(text, chat_id, config, router) -> str`. Tests should exercise that, not the Telegram side.

## Adding a new command

Two cases.

**A. Device-local command (most common).** No protocol change needed if the command name already exists in `CommandName`. Just write a handler factory and register it.

```python
from freemotion.agent import make_ping_handler  # for reference shape
from freemotion.protocol import Command, CommandName, Reply

def make_my_handler(config):
    def handler(cmd: Command) -> Reply:
        return Reply(
            sender=config.device_id,
            state="idle",
            ok=True,
            error=None,
            telemetry={},
            message="hello from my command",
            correlation_id=cmd.correlation_id,
        )
    return handler

router.register(CommandName.STATUS, make_my_handler(cfg))
```

**B. New protocol command.** Add the value to `CommandName`, extend `parse_slash` if it has slash sugar, update [docs/protocol.md](protocol.md), and write tests. This is **additive** — old clients that don't know the command will reject it as `unknown_cmd`, which is the correct behavior. No `v` bump.

## Building a minimal device

The smallest useful device, suitable for a laptop:

```python
import logging, os
from freemotion.agent import (
    Agent,
    make_capabilities_handler,
    make_ping_handler,
    make_status_handler,
    make_stop_handler,
)
from freemotion.config import Config
from freemotion.protocol import CommandName
from freemotion.router import Router

logging.basicConfig(level=os.environ.get("FREEMOTION_LOG_LEVEL", "INFO"))

cfg = Config.from_env()
router = Router(device_id=cfg.device_id)
router.register(CommandName.PING, make_ping_handler(cfg))
router.register(CommandName.STOP, make_stop_handler(cfg))
router.register(CommandName.STATUS, make_status_handler(cfg))
router.register(CommandName.CAPABILITIES, make_capabilities_handler(cfg, router))

Agent(config=cfg, router=router).run()
```

Set `TELEGRAM_BOT_TOKEN`, run it, DM the bot `/ping`. That's a real Free Motion device.

For richer examples, see:

- [`examples/pi_closed_loop_demo/`](../examples/pi_closed_loop_demo/) — **the canonical Pi reference architecture** (Step 4, [`docs/pi-reference.md`](pi-reference.md)). Full closed loop: `PiCameraSource` + `YoloVision` + `WorldState` + `GemmaMissionControl` + `MissionLoop` + `SafetyGate` + `PiHardwareController`.
- [`examples/pi_bench_demo/`](../examples/pi_bench_demo/) — bench-only sub-path (M4). `PiHardwareController` + `SafetyGate`, no perception or mission control. Useful for debugging GPIO in isolation.
- [`examples/pi_camera_demo/`](../examples/pi_camera_demo/) — perception-only sub-path (Step 1). `PiCameraSource` + `YoloVision`, no Telegram or hardware. Useful for debugging perception in isolation.
- [`examples/mock_drone/`](../examples/mock_drone/) — no hardware required, uses `MockHardwareController` for `arm`/`disarm`/`move`.
- [`examples/pipe_check/`](../examples/pipe_check/) — smallest end-to-end Pi check (M0); LED only.

## Hardware: when you actually have some

A `HardwareController` is the contract a "thing that can move" implements. Today the runtime ships:

- [`MockHardwareController`](../freemotion/hardware/mock.py) — deterministic in-memory mock for tests and dev.
- [`PiHardwareController`](../freemotion/hardware/pi.py) — bench-safe Raspberry Pi GPIO controller (M4). Drives `armed_pin` HIGH while armed, pulses `moving_pin` HIGH on `move()`. `RPi.GPIO` is imported lazily so the module is safe to import on any host.

The factory picks the right one for you:

```python
from freemotion.hardware import make_controller_from_config, SafetyGate

inner = make_controller_from_config(cfg)         # Pi or Mock based on cfg.hardware_profile
controller = SafetyGate(inner, cfg.safety_default)  # device-level safety floor
```

`SafetyGate` (M4 Phase 3) is a `HardwareController` wrapper that fixes the device's safety mode at construction. In `dry_run`, it refuses `arm()`/`move()` regardless of any per-command override; `disarm()`/`stop()` always pass through. Wire it once at startup and the rest of your handlers can stay focused on the happy path. See [`docs/pi-hardware.md`](pi-hardware.md) and [ADR-0006](decisions.md#adr-0006--safetygate-enforce-safetymode-at-the-hardware-boundary-dry_run-is-the-floor--2026-05-03).

If your device only needs a peripheral (an LED, a buzzer, a sensor), it's fine to keep that example-local — see [`examples/pipe_check/pipe_check.py`](../examples/pipe_check/pipe_check.py).

## Running on a Pi

The OS-level prep lives in [`docs/pi-setup.md`](pi-setup.md). The hardware adapter, safety gate, and bench-flow walk-through live in [`docs/pi-hardware.md`](pi-hardware.md). For the runtime itself:

- One canonical install command: `pip install -e .` from the repo root. Add `pip install RPi.GPIO` only on a Pi that drives GPIO.
- Secrets in `~/.config/freemotion.env` with `chmod 600`.
- Long-running: copy a systemd user unit (e.g. [`examples/pi_bench_demo/systemd/freemotion-pi-bench-demo.service`](../examples/pi_bench_demo/systemd/freemotion-pi-bench-demo.service) or [`examples/pipe_check/systemd/freemotion-pipe-check.service`](../examples/pipe_check/systemd/freemotion-pipe-check.service)) into `~/.config/systemd/user/`, `systemctl --user enable --now <unit>`, and `loginctl enable-linger "$USER"` so it survives reboots without an active login session.

## Testing your device

Three patterns, in order of value:

1. **Unit-test the handlers.** Pure functions; fastest feedback. Build a `Command`, call the handler, assert on the `Reply`. See [`tests/test_builtins.py`](../tests/test_builtins.py).
2. **Test the router wiring.** Build the router exactly as `main()` does, assert `router.known` matches the commands you advertise. See [`tests/test_pipe_check.py`](../tests/test_pipe_check.py).
3. **Test the message lifecycle.** Use `handle_text` directly with a mock `Config` and `Router`. No Telegram client needed. See [`tests/test_agent.py`](../tests/test_agent.py).

You should rarely need to mock `python-telegram-bot` itself.

## Common pitfalls

- **Forgetting `cmd.correlation_id` on the reply.** The protocol requires it; clients use it to match replies to their commands.
- **Raising in a handler.** Won't crash the agent (the router catches it), but produces an `internal` reply with the exception text. Prefer returning a structured `Reply` with an `Error`.
- **Actuating in `dry_run`.** Handlers that move hardware MUST check `cmd.safety` before doing anything physical. See `make_move_handler` for the canonical shape. The `SafetyGate` is a second line of defense, not a substitute.
- **Forgetting the `SafetyGate` in your own example.** The handler-level check gates `cmd.safety`; the gate gates `cfg.safety_default`. Both matter. Use `SafetyGate(make_controller_from_config(cfg), cfg.safety_default)` as the wiring pattern (see [`examples/pi_bench_demo/pi_bench_demo.py`](../examples/pi_bench_demo/pi_bench_demo.py)).
- **Loose `TELEGRAM_ALLOWED_CHAT_IDS`.** A bot with no allowlist replies to anyone. The agent logs a loud warning, but lock it down once you've found your chat id.

## Where to read next

- **Pi reference architecture (Step 4 lock):** [docs/pi-reference.md](pi-reference.md)
- Closed-loop architecture (Step 2): [docs/pi-closed-loop.md](pi-closed-loop.md)
- Environmental failure runbook (Step 3): [docs/pi-failure-modes.md](pi-failure-modes.md)
- Pi hardware (controller, gate, bench flow): [docs/pi-hardware.md](pi-hardware.md)
- Wire format: [docs/protocol.md](protocol.md)
- Why things are the way they are: [docs/decisions.md](decisions.md)
- Architecture overview: [docs/architecture.md](architecture.md)
- What's coming: [ROADMAP.md](../ROADMAP.md)
