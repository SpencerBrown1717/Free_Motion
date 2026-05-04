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
| **Agent / runtime** | Long-running service on the device: receive → validate → route → reply. (foundation shipped, M2.) `MissionLoop` background closed loop (capture → infer → world → plan → MOVE) shipped Step 2; only dispatches MOVE per ADR-0010. Step 3 hardens the loop against real-world failure: stale-world refusal, per-stage consecutive counters with `degraded` summary, hung-tick handling, ordered `graceful_shutdown` (ADR-0011, [docs/pi-failure-modes.md](docs/pi-failure-modes.md)). Step 4 locks the canonical Pi reference architecture as the M5 baseline (ADR-0012, [docs/pi-reference.md](docs/pi-reference.md)). Step 5 ships `pi_follow_bench` — the named, repeatable benchmark with a frozen 10-step protocol, frozen JSON artifact, and three failure injections (camera offline, mission offline, vision drop) (ADR-0013, [docs/pi-benchmark.md](docs/pi-benchmark.md)). |
| **Mission control** | Goal + perception → next action. `MissionPolicy` Protocol + `MockMissionControl` + `WorldStateSnapshot` input shipped (M3); `GemmaMissionControl` shipped post-M4 behind `[gemma]` extra and `FREEMOTION_MISSION_BACKEND=gemma`. |
| **Vision** | On-device perception. `VisionBackend` Protocol + `MockVision` (M3) + `YoloVision` (post-M4, behind `[yolo]` extra and `FREEMOTION_VISION_BACKEND=yolo`) + `PiCameraSource` live frame producer (Step 1, behind `[picam]` extra). |
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

Real adapters for both interfaces shipped post-M4:

- **`YoloVision`** behind `FREEMOTION_VISION_BACKEND=yolo` and `pip install -e .[yolo]`. See ADR-0007.
- **`GemmaMissionControl`** behind `FREEMOTION_MISSION_BACKEND=gemma` and `pip install -e .[gemma]`. See ADR-0008.

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

Goal: grow beyond Pi **without changing the contract**. Priority unchanged: Jetson Nano → ESP32 → Arduino. The Pi reference architecture ([`docs/pi-reference.md`](docs/pi-reference.md), Step 4 lock) is the M5 baseline — every M5 port keeps the protocol, command surface, world-state shape, mission-decision shape, safety semantics, status semantics, and failure model identical, and only differs on the hardware-specific seams listed in §10 of that doc.

What gets built:

1. **Phase 1 — Jetson Nano** (heavier on-device vision). New `JetsonHardwareController`, new Jetson camera adapter, new factory branch. Existing `HardwareController` / `VisionBackend` Protocols unchanged.
2. **Phase 2 — ESP32 bridge** (sensors, peripherals, UART/SPI to a heavier host).
3. **Phase 3 — Arduino bridge** (simple actuators, low-level timing).

Deliverables (per phase, mirroring the Pi structure):

- `freemotion/hardware/<platform>.py` — controller adapter
- `examples/<platform>_closed_loop_demo/` — closed-loop reference
- `docs/<platform>-reference.md` — Step-4-style lock for that platform
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
10. ~~`GemmaMissionControl` adapter behind `FREEMOTION_MISSION_BACKEND=gemma` and `pip install -e .[gemma]`.~~ See ADR-0008 in [`docs/decisions.md`](docs/decisions.md).
11. ~~`PiCameraSource` live-camera adapter + `examples/pi_camera_demo/` standalone demo.~~ Step 1 of the Pi-first lockdown. See ADR-0009 in [`docs/decisions.md`](docs/decisions.md).
12. ~~**Step 2 — Pi full closed loop.** `MissionLoop` (background `capture → infer → world → plan → MOVE`) + `examples/pi_closed_loop_demo/` (Telegram → live YOLO → `WorldState` → Gemma → bench-safe hardware action → `/status`). Loop only ever dispatches MOVE (ADR-0010); ARM/DISARM/STOP stay operator-driven through Telegram. `/mission_start` is refused in `dry_run`; `/stop` halts the loop *and* drops both pins LOW unconditionally. Camera/YOLO/Gemma failures all degrade to idle without crashing the loop. See [ADR-0010](docs/decisions.md) and [docs/pi-closed-loop.md](docs/pi-closed-loop.md).~~
13. ~~**Step 3 — Real-world failure-mode hardening.** Survivability over capability. Stale-world timeout refuses MOVE on outdated perception (Gemma cannot act on a 30s-old world). Per-stage consecutive counters drive a `degraded` flag with a human-readable reason in `/status`; recovery is automatic when the failing stage stops failing. Hung-`mission.plan()` no longer leaks zombie threads — `stop()` preserves `_thread` so a fresh `start()` refuses, and `start()` reaps the dead orphan when the worker exits. `graceful_shutdown(...)` runs the demo teardown in a tested, ordered, exception-tolerant sequence. Every failure (camera unplugged, vision drop, mission hang, repeated dispatch fail, SIGTERM, restart) is contracted in [docs/pi-failure-modes.md](docs/pi-failure-modes.md) and covered by 31 new tests. See [ADR-0011](docs/decisions.md).~~
14. ~~**Step 4 — Pi reference architecture lock.** [`docs/pi-reference.md`](docs/pi-reference.md) is the single source of truth: canonical Pi path is `examples/pi_closed_loop_demo/`; supported command surface is the eight commands `/ping /capabilities /status /arm /disarm /move /mission_start /stop` (frozen — anything else needs a protocol bump per ADR-0002); hardware path is BCM 27 / 22 indicator pins via `PiHardwareController` and Pi camera via `PiCameraSource` (frozen); model path is `PiCameraSource → YoloVision → WorldState → GemmaMissionControl → SafetyGate → PiHardwareController` (frozen); env-var contract is locked across required / recommended / optional / demo-only / constructor-only knobs (every variable maps to a real code path); safety contract spells out twelve numbered guarantees; status contract pins the `controller` and `mission_loop` telemetry shape; failure model points to [`docs/pi-failure-modes.md`](docs/pi-failure-modes.md). M5 Jetson port target is **same contract, different hardware** — the must-keep list and allowed-to-differ list are explicit. See [ADR-0012](docs/decisions.md).~~

Past work (shipped, Pi-first lockdown):

15. ~~**Step 5 — One repeatable Pi benchmark demo.** [`examples/pi_follow_bench/`](examples/pi_follow_bench/) — the named, operator-runnable Pi benchmark. Drives the locked Pi reference architecture through a fixed 10-step command sequence (`/ping`, `/capabilities`, `/status`, `/arm`, `/mission_start <intent>`, observe, `/status`, `/stop`, `/disarm`, `/status`), applies fixed pass/fail criteria, and emits a stable JSON artifact (schema v1) for each run. Two modes: `--mode=ci` (deterministic mock chain, ~1s on a CI runner) and `--mode=bench` (real-Pi stack via `Config.from_env`). Three failure injections (`camera_offline`, `mission_offline`, `vision_drop_after_n`); universal contracts (no crash, `/stop` returns ok, pins LOW at end, loop reads idle after stop, capabilities match locked surface) hold under every inject. Frozen protocol in [`docs/pi-benchmark.md`](docs/pi-benchmark.md); operator runbook in [`examples/pi_follow_bench/README.md`](examples/pi_follow_bench/README.md). See [ADR-0013](docs/decisions.md).~~

Next, in priority order — **M5 Jetson Nano port** (same contract, different hardware):

16. **M5 Phase 1 — Jetson Nano port.** Same contract, different hardware. The "must remain identical" surfaces (protocol, command set, world state shape, mission decision shape, safety semantics, status semantics, `/stop` ordering, failure model) are listed in [`docs/pi-reference.md`](docs/pi-reference.md) §10. The "allowed to differ" surfaces (the controller adapter, the camera adapter, the hardware factory, model tuning, systemd unit, OS prep doc) are also there. Acceptance is `examples/jetson_closed_loop_demo/` running the canonical command set against real Jetson hardware while every contract in [`docs/pi-reference.md`](docs/pi-reference.md) §6 holds **and** the Jetson rig produces a `pi_follow_bench`-shaped artifact (renamed `jetson_follow_bench` allowed; the schema, sequence, and criteria are not — see [ADR-0013](docs/decisions.md)).
17. **M5 Phase 2 — ESP32.** Sensor / actuator coprocessor pattern over UART/SPI to a heavier host. Constraint set defined when this ships.
18. **M5 Phase 3 — Arduino.** Simple actuators, low-level timing. Constraint set defined when this ships.
19. **Rate limits, watchdogs, link-loss fail-safe** (Safety module continued). Bench rig is the test bed; bumped from M4 to keep the milestone narrow.

Move-to-M5 gate (now satisfied, pending hardware): **A Raspberry Pi can receive a Telegram command, run live YOLO, update world state, get one Gemma decision, execute one bench-safe action, and report status back reliably — and survive the real world — and a contributor can stand up the same architecture on Jetson without guessing because the contract is locked and a repeatable benchmark exists to prove it.** Step 2 made the path real on the bench; Step 3 hardened it against environmental failure ([`docs/pi-failure-modes.md`](docs/pi-failure-modes.md)); Step 4 locked the reference architecture and the Jetson port target ([`docs/pi-reference.md`](docs/pi-reference.md)); Step 5 shipped the named benchmark ([`docs/pi-benchmark.md`](docs/pi-benchmark.md)). M5 Phase 1 ships when a Jetson rig passes the same benchmark.

## What success looks like

A new contributor lands on the repo and can answer five questions in under five minutes:

1. What is Free Motion?
2. What already works?
3. What gets built next?
4. Where do I contribute?
5. What demo proves it’s real?

When all five are obvious, the project is alive.
