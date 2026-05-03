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
| **Mission control** | Goal + perception → next action. `MissionPolicy` Protocol + `MockMissionControl` + `WorldStateSnapshot` input shipped (M3 partial); Gemma small adapter pending. |
| **Vision** | On-device perception. `VisionBackend` Protocol + `MockVision` (M3) + `YoloVision` (post-M4, behind `[yolo]` extra and `FREEMOTION_VISION_BACKEND=yolo`). |
| **World state** | Shared "what's true now" — `WorldStateSnapshot` + `WorldState` (M3, shipped). |
| **Hardware adapter** | Per-platform actuators (Pi GPIO, Jetson, ESP32, Arduino). `HardwareController` Protocol + `MockHardwareController` (M2) + `PiHardwareController` + `make_controller_from_config` factory shipped (M4). Jetson / ESP32 / Arduino on the M5 roadmap. |
| **Safety** | Modes, hard stops, rate limits, watchdogs. `SafetyMode` (M1), per-command deny list (M2), `SafetyGate` controller wrapper enforcing `cfg.safety_default` as the device-level floor (M4). Rate limits / watchdogs deferred. |

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

Now also shipped under M2:

- **Per-command deny list** — `Config.denied_commands` (env: `FREEMOTION_DENIED_COMMANDS`), enforced in `Router.dispatch`. Refused commands return `error.code = "denied_by_policy"`. `stop` is always exempt. See [ADR-0004](docs/decisions.md#adr-0004--per-command-allowdeny-allow-by-default-explicit-deny-list-stop-always-exempt--2026-05-03).

Still to do under M2 (tracked in [`docs/issues/m2-m3.md`](docs/issues/m2-m3.md)):

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

7. **Shared world state** — `freemotion/world/` with `WorldStateSnapshot` (immutable read view) and `WorldState` (lock-protected wrapper). Five fields: `target`, `current_state`, `confidence`, `last_seen`, `next_action`. `MissionPolicy.plan` now takes `WorldStateSnapshot` directly. See [ADR-0005](docs/decisions.md#adr-0005--world-state-v1-narrow-lock-protected-snapshot-shaped--2026-05-03).
8. **End-to-end loop demo** — [`examples/local_sim_demo.py`](examples/local_sim_demo.py) closes the M3 loop on mocks: intent → vision → world → mission_control → router → hardware → world. No setup, no hardware, no Telegram, no model download. Runs in CI as a smoke test. Long-form walkthrough in [`docs/demo.md`](docs/demo.md).

Still to do under M3 (tracked in [`docs/issues/m2-m3.md`](docs/issues/m2-m3.md)):

- **`YoloVision` adapter** behind `FREEMOTION_VISION_BACKEND=yolo` and a `pip install -e .[yolo]` extra.
- **`GemmaMissionControl` adapter** behind `FREEMOTION_MISSION_BACKEND=gemma` and a `pip install -e .[gemma]` extra.

### M4 — First real hardware proof (shipped)

Goal: ship one safe, repeatable real-hardware demo where Free Motion drives a Raspberry Pi end-to-end. **Bench rig only** — GPIO indicator pins, no motor drivers, no propellers, no actuated platform. Real motion lands later behind explicit safety modes.

What's now in the repo:

1. **`PiHardwareController`** ([`freemotion/hardware/pi.py`](freemotion/hardware/pi.py)) — bench-safe `HardwareController` for Pi GPIO. `armed_pin` HIGH while armed (default BCM 27); `moving_pin` pulsed HIGH for ~100 ms on each successful `move()` (default BCM 22). `RPi.GPIO` is imported lazily; tests inject a `FakeGPIO`. Hardware exceptions are caught — `arm`/`move` return `False`, `stop` always swallows. The agent loop never crashes on hardware faults.
2. **`make_controller_from_config(cfg)`** factory — selects `PiHardwareController` for `FREEMOTION_HARDWARE=pi` (lazy import, so non-Pi hosts stay clean) and `MockHardwareController` everywhere else. Unknown profiles log a warning.
3. **`SafetyGate`** ([`freemotion/hardware/safety.py`](freemotion/hardware/safety.py), ADR-0006) — `HardwareController` wrapper that fixes `cfg.safety_default` at the controller boundary. In `dry_run`, `arm()` and `move()` refuse without ever calling the inner controller; `disarm()` and `stop()` always pass through. **Device default is the floor:** a per-command `safety=bench` against a `dry_run` device is refused. `state()` exposes the active safety mode under `controller.safety` so `/status` carries it.
4. **`examples/pi_bench_demo/`** ([README](examples/pi_bench_demo/README.md), [systemd unit](examples/pi_bench_demo/systemd/freemotion-pi-bench-demo.service)) — first real hardware Free Motion device. Wires `Config.from_env` → `make_controller_from_config` → `SafetyGate` → `Router` → `Agent` → Telegram. Registers exactly seven commands: `/ping`, `/capabilities`, `/status`, `/arm`, `/move`, `/stop`, `/disarm`. Falls back to mock when `FREEMOTION_HARDWARE != pi`.
5. **`docs/pi-hardware.md`** — canonical Pi architecture + bench-flow walkthrough: what's real, what's mocked, the safety contract, and how to graduate from `local_sim_demo` → `mock_drone` → `pipe_check` → `pi_bench_demo`.
6. **Two new ADRs:** [ADR-0004](docs/decisions.md#adr-0004--per-command-allowdeny-allow-by-default-explicit-deny-list-stop-always-exempt--2026-05-03) (deny list, `stop` exempt) and [ADR-0006](docs/decisions.md#adr-0006--safetygate-enforce-safetymode-at-the-hardware-boundary-dry_run-is-the-floor--2026-05-03) (gate semantics).
7. **CI** — import smoke covers `pi_bench_demo` and `PiHardwareController`'s lazy-import path on a non-Pi GitHub runner.

**M4 contracts (every one is covered by tests):**

- `dry_run` cannot actuate `arm` or `move`. The handler refuses on `cmd.safety`; the gate refuses on `cfg.safety_default`. Verified with a call counter on a wrapped controller.
- `bench` allows the bench-safe primitive (GPIO output to indicator pins). The Pi controller does not expose motor primitives — that's a deliberate M5+ boundary.
- `stop` always passes through. Exempt from the deny list (ADR-0004) and from the SafetyGate (ADR-0006). `PiHardwareController.stop()` does not acquire the controller lock, so it succeeds mid-`move()`.
- Hardware unavailable returns a protocol-shaped reply. Missing `RPi.GPIO`, failed setup, runtime GPIO errors all surface as `unsafe_in_mode`. Agent loop keeps running.

174 tests pass on every push; 22 cover the Pi controller (via `FakeGPIO`), 14 cover the safety gate.

What did **not** ship under M4 (deliberately narrow):

- Motor or ESC drivers (M5+).
- Free flight or uncontrolled motion (M5+).
- Per-platform support beyond the Pi (Jetson / ESP32 / Arduino — M5).
- YOLO / Gemma adapters (post-M4 priorities).

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

Past work (shipped):

1. ~~`docs/protocol.md` — contract first, code follows.~~ (M1)
2. ~~`freemotion/protocol/` — typed envelopes + tests.~~ (M1)
3. ~~`freemotion/agent/` on Pi.~~ (M2)
4. ~~`/status` and `/capabilities` as routed commands.~~ (M2)
5. ~~Mission control stub.~~ (M3)
6. ~~Vision stub.~~ (M3)
7. ~~World state v1.~~ (M3)
8. ~~One real hardware demo on Pi (`PiHardwareController` + `SafetyGate` + `pi_bench_demo`).~~ (M4)

Past work (shipped, post-M4):

9. ~~`YoloVision` adapter behind `FREEMOTION_VISION_BACKEND=yolo` and `pip install -e .[yolo]`.~~ See ADR-0007 in [`docs/decisions.md`](docs/decisions.md).

Next, in priority order:

10. **`GemmaMissionControl` adapter** behind `FREEMOTION_MISSION_BACKEND=gemma` and a `pip install -e .[gemma]` extra. Same `MissionPolicy` Protocol; `MockMissionControl` is the structural reference.
11. **Jetson Nano port** (M5). Same `HardwareController` Protocol; new adapter class + example. Heavier on-device vision unlocks once it's there.
12. **ESP32 / Arduino bridges** (M5).
13. **Rate limits, watchdogs, link-loss fail-safe** (Safety module continued). Bench rig is the test bed; bumped from M4 to keep the milestone narrow.

## What success looks like

A new contributor lands on the repo and can answer five questions in under five minutes:

1. What is Free Motion?
2. What already works?
3. What gets built next?
4. Where do I contribute?
5. What demo proves it’s real?

When all five are obvious, the project is alive.
