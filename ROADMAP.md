# Roadmap

This is the intended order of attack. Higher items unblock real hardware sooner.

## Hardware platforms

1. **Raspberry Pi (first)**  
   Default edge device: Ubuntu or Raspberry Pi OS, Telegram in, local inference, motion out. All docs and the first demos assume a Pi.

2. **Jetson Nano (second)**  
   Same architecture, heavier GPU for faster YOLO and room to grow Gemma-style mission logic without fighting thermals as early.

3. **ESP32 (third)**  
   Lightweight bridge: sensors, actuators, coprocessors, and UART/SPI links to a heavier host when needed.

4. **Arduino (fourth)**  
   Simple, timing-sensitive I/O and motor drivers where a full Linux stack is unnecessary.

## Models and software priorities

These run in parallel with the hardware track above.

- **YOLO (vision)**  
  On-device perception: detection, tracking, simple scene context. Optimize Pi first, then tune for Jetson.

- **Gemma small (mission control)**  
  Task interpretation, next-step decisions, tight feedback loops, status back upstream to OpenClaw.

Explicit non-goal for the early roadmap: chasing every model family. Pi + YOLO + Gemma small + Telegram should stay the clear “happy path.”

## Named modules (the architecture, not folders yet)

The project is framed around six modules. Each milestone below lights one or more of them up.

| Module | Role |
|---|---|
| **Transport** | Move bytes between OpenClaw and the device. Telegram first; pluggable later. (shipped, M0) |
| **Protocol** | Command and reply envelopes, validation, versioning. (shipped, M1) |
| **Agent / runtime** | Long-running service on the device: receive → validate → route → reply. (foundation shipped, M2) |
| **Mission control** | Goal + perception → next action. `MissionPolicy` Protocol + `MockMissionControl` shipped (M3 partial); Gemma small adapter pending. |
| **Vision** | On-device perception. `VisionBackend` Protocol + `MockVision` shipped (M3 partial); YOLO adapter pending. |
| **Hardware adapter** | Per-platform actuators (Pi GPIO, Jetson, ESP32, Arduino). `HardwareController` Protocol + `MockHardwareController` shipped (M2). `PiHardwareController` pending. |
| **Safety** | Modes, hard stops, rate limits, watchdogs. Cuts across every other module. |

## Milestones

### M0 — Telegram pipe (shipped)

Repeatable Pi setup, secrets handling, no-motion pipe check.

- Telegram bot path works end-to-end.
- Optional GPIO LED proof on Pi.
- Tests, CI, systemd autostart.

Lives at [examples/pipe_check/](examples/pipe_check/).

### M1 — Lock down the transport (shipped)

Goal: a stable, structured contract between OpenClaw and any device.

What's now in the repo:

1. **Command envelope** — version, correlation id, sender, optional target, command, args, safety mode, timestamp.
2. **Reply envelope** — correlation id echo, ok/error, state, telemetry, message, timestamp.
3. **Minimal command set** — `ping`, `status`, `capabilities`, `led_on`, `led_off`, `arm`, `disarm`, `stop`.
4. **Slash sugar AND JSON** — both forms parse to the same internal `Command`.
5. **Auth and safety guardrails** — chat-id allowlist, three safety modes (`dry_run`, `bench`, `live`), unconditional `stop`. Per-command allow/deny moves to M2.

Deliverables (all shipped):

- [docs/protocol.md](docs/protocol.md) — v0 locked.
- `freemotion/protocol/` — typed envelopes, parser, serializer, slash sugar.
- `tests/test_protocol.py` — round-trip + error-path coverage.
- `examples/pipe_check/` migrated onto the protocol as the first adopter.

### M2 — Pi device runtime (foundation shipped)

Goal: make the Pi a real first-class target with one long-running service that owns the protocol.

What's now in the repo:

1. **Free Motion agent** — receive → validate → route → reply, with the pure logic in `handle_text` for testability.
2. **Config** — env-driven, frozen, single source of truth (token, allowlist, device id, safety default, hardware profile, enabled features).
3. **Command router** — typed handler registration, total dispatch (unknown commands and handler exceptions both return well-formed replies).
4. **Built-in handlers** — `ping`, `stop`, `status`, `capabilities`. `status` and `capabilities` carry structured telemetry per [docs/protocol.md](docs/protocol.md#device-registration).
5. **`pipe_check` is now the reference adopter** — ~120 lines, contributes only the GPIO LED hardware adapter and `led_on/led_off` handlers.

Now also shipped under M2 (foundation for M5):

- `freemotion/hardware/` with a `HardwareController` Protocol and `MockHardwareController`.
- New `move` command (additive to the protocol; no `v` bump).
- Built-in motion handlers (`make_arm_handler`, `make_disarm_handler`, `make_move_handler`) that operate on any `HardwareController`.
- `examples/mock_drone/` — second example, runs on any laptop with no hardware.
- `docs/pi-runtime.md` — operator + contributor guide for the runtime.

Still to do under M2 (tracked in [`docs/issues/m2-m3.md`](docs/issues/m2-m3.md)):

- Per-command allow/deny in `Config` + `Router`.
- `PiHardwareController`.
- Module hooks for future `motion` / `vision` / `mission_control` (lit up in M3).

### M3 — Mission and vision stubs (interfaces shipped, loop pending)

Goal: turn the runtime into the start of an AI motion stack with stubbed brains, so the loop is real before the models are.

What's now in the repo:

1. **Vision interface** — `VisionBackend` Protocol with `name`, `available`, `scene() -> VisionResult`. `Detection(label, confidence, bbox)` and `VisionResult(detections, ts)` are the carrier types.
2. **`MockVision`** — scripted, deterministic, cycles. Drives tests and demos.
3. **Mission control interface** — `MissionPolicy` Protocol with `plan(intent, scene, world) -> MissionDecision`. `MissionDecision` carries one `CommandName` + args + reason + confidence; `next_command=None` is "idle."
4. **`MockMissionControl`** — rule-based: `stop` / `disarm` / `follow person` / idle. The structural pattern Gemma will follow.
5. **`docs/models.md`** — interface contract, mock behavior, planned real adapters, swap path.
6. **ADR-0003** — interfaces + mocks now, real models behind feature flags later.

Still to do under M3 (tracked in [`docs/issues/m2-m3.md`](docs/issues/m2-m3.md)):

- **`YoloVision` adapter** behind `FREEMOTION_VISION_BACKEND=yolo` and a `pip install -e .[yolo]` extra.
- **`GemmaMissionControl` adapter** behind `FREEMOTION_MISSION_BACKEND=gemma` and a `pip install -e .[gemma]` extra.
- **Shared world state** in `freemotion/world/` — feeds the `world` arg of `MissionPolicy.plan`.
- **`examples/mock_follow_task/`** — closes the loop on mocks; one config flag flip away from running on YOLO + Gemma once those land.

### M4 — One real hardware demo (gated)

Goal: prove Free Motion does something physical. **Pick exactly one.**

Candidates:

1. LED + motor state-machine demo
2. Rover: forward, stop, status
3. Drone in bench mode: arm / disarm only, no flight
4. Person-follow simulation

Hard requirements:

- Full [SAFETY.md](SAFETY.md) sign-off for the chosen platform.
- Every code path that can move hardware respects `safety` mode and the unconditional `stop`.
- One short demo clip linked from the README.

Deliverables:

- `examples/<chosen>_demo/` with hardware list, wiring, run command, expected output
- `SAFETY.md` updates specific to the chosen platform
- Demo clip / GIF in [README.md](README.md)

### M5 — Expand hardware support

Goal: grow beyond Pi without losing focus. Priority unchanged: Jetson Nano → ESP32 → Arduino.

What gets built:

1. **Hardware abstraction** — common interface: `init`, `status`, `execute`, `stop`.
2. **Jetson Nano** support (heavier on-device vision).
3. **ESP32 bridge** (sensors, peripherals, UART/SPI to a heavier host).
4. **Arduino bridge** (simple actuators, low-level timing).

Deliverables:

- `freemotion/hardware/`
- `docs/hardware.md`
- Support matrix in the README

## What to build next, in exact order

1. [docs/protocol.md](docs/protocol.md) — contract first, code follows.
2. `freemotion/protocol/` — typed envelopes + tests.
3. `freemotion/agent/` on Pi (start of M2).
4. `/status` and `/capabilities` as routed commands through the agent.
5. Mission control stub (M3).
6. Vision stub (M3).
7. One real hardware demo (M4).
8. SAFETY.md updates for that demo.
9. Jetson Nano port (M5).

## What success looks like

A new contributor lands on the repo and can answer five questions in under five minutes:

1. What is Free Motion?
2. What already works?
3. What gets built next?
4. Where do I contribute?
5. What demo proves it’s real?

When all five are obvious, the project is alive.
