# Changelog

All notable changes to Free Motion are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0 minor versions may break interfaces; the protocol version is tracked separately under [`docs/protocol.md`](docs/protocol.md).

## [Unreleased]

### Added (M4 Phase 4 — docs polish, M4 done)

- **New `docs/pi-hardware.md`** — canonical Pi architecture + bench-flow walkthrough. Covers what's real on the Pi today (controller, factory, bench demo, safety gate, status path, failure replies), what's still mocked (YOLO, Gemma, higher autonomy, broader hardware), the three-layer refusal architecture (router deny → handler safety → gate floor), the safety-mode truth table, the wiring/install/env-var/run-and-verify recipe, the four safety guarantees, the four-example comparison, and what comes next.
- **`docs/pi-runtime.md`** refreshed: env-var table now lists `pi_armed_pin` / `pi_moving_pin`; "Hardware: when you actually have some" section now documents `PiHardwareController`, `make_controller_from_config`, and the `SafetyGate(make_controller_from_config(cfg), cfg.safety_default)` wiring pattern; example list reordered with `pi_bench_demo` as the canonical Pi reference; pitfalls section calls out the gate.
- **`GETTING_STARTED.md`** rewritten around two paths: laptop-no-hardware (60-second `local_sim_demo.py` + `mock_drone` pointer) and real-Pi-bench-rig (full M4 recipe with wiring, env vars, command tour, expected hardware effects), plus a four-point safety-guarantees summary.
- **`README.md`** refreshed: test badge bumped to 174; CTA now links the Pi graduation path; stack table adds a Safety row and marks `PiHardwareController` real; current-status section moves Pi controller out of "mocked" and into "shipped"; repo tour adds `pi_bench_demo/` and `pi-hardware.md`; safety section adds the SafetyGate floor; contributing paths reframed around YOLO/Gemma adapters and the `HardwareController` Protocol.
- **`ROADMAP.md`** refreshed: Hardware adapter and Safety rows in the modules table updated; the M4 section is now a "shipped" entry covering Phases 1–4 with all four contracts called out and tests cited; "what to build next" reordered with M0–M4 struck through and YOLO / Gemma / Jetson / ESP32 / Arduino / safety extras lined up after.

**M4 done.** A contributor can now read [`docs/pi-hardware.md`](docs/pi-hardware.md) to understand the Pi bench architecture, [`docs/pi-setup.md`](docs/pi-setup.md) + [`GETTING_STARTED.md`](GETTING_STARTED.md) to set up the Pi, [`examples/pi_bench_demo/README.md`](examples/pi_bench_demo/README.md) to run the bench demo, and the four safety guarantees (in `pi-hardware.md`, `GETTING_STARTED.md`, and `README.md`) to know exactly what is safe vs. not safe. Next priorities are `YoloVision`, `GemmaMissionControl`, then M5 broader hardware.

### Added (M4 Phase 3 — safety-mode enforcement on real hardware)

- **`SafetyGate`** (`freemotion/hardware/safety.py`) — `HardwareController` wrapper that enforces a fixed `SafetyMode` at the controller boundary. In `dry_run`, `arm()` and `move()` refuse without ever calling the inner controller; `disarm()` and `stop()` always pass through (depowering is always safe; `stop` is the unconditional `ADR-0004` hard-stop). In `bench` / `live`, every method passes through. `state()` is decorated with the active `safety` field so `/status` exposes the runtime's effective safety floor without wiring `Config` into the status handler.
- **Wired into `examples/pi_bench_demo/`** — `main()` now constructs `SafetyGate(make_controller_from_config(cfg), cfg.safety_default)` so the device's `FREEMOTION_SAFETY_DEFAULT` is the **floor**: a per-command `safety=bench` override against a `FREEMOTION_SAFETY_DEFAULT=dry_run` device is refused at the gate, surfaced as `unsafe_in_mode`. `cleanup()` still routes to the inner Pi controller via `gate.inner`.
- **`docs/decisions.md` ADR-0006** — locks the gate semantics: composition over inheritance, fixed-at-construction, device default is the floor (not the ceiling), depowering paths (`disarm`, `stop`) always pass through, `state()` carries the active safety mode. `mock_drone` and `pipe_check` are intentionally not retrofitted (no real actuation to gate).
- **Tests** — `tests/test_safety_gate.py` (14): protocol satisfaction, `state()` surfaces safety, `dry_run` blocks `arm`/`move` without inner calls, `dry_run` passes `disarm`/`stop` through, `bench`/`live` pass everything through, `state()` returns independent dicts (no shared-mutation bugs), and an integration test wiring `make_arm_handler` over the gate to verify a per-command `safety=bench` override on a `dry_run` device surfaces `unsafe_in_mode`. `tests/test_pi_bench_demo.py` (+3): floor-blocks-override, status carries `controller.safety`, `/stop` still works through a `dry_run` gate. **174 tests pass.**

Phase 3 gate met: in `dry_run`, no path can actuate `arm`/`move` regardless of per-command safety overrides; `stop` always works (deny list and gate both bypassed); `/status` reflects the active safety mode. Phase 4 (docs polish — `docs/pi-hardware.md`, README, ROADMAP, GETTING_STARTED) is next.

### Added (M4 Phase 2 — bench demo)

- **`examples/pi_bench_demo/`** — the **first real hardware** Free Motion device. Wires `Config.from_env` → `make_controller_from_config` → `Router` → `Agent` → Telegram. Registers exactly the Phase 2 command set: `/ping`, `/capabilities`, `/status`, `/arm`, `/move`, `/stop`, `/disarm`. Falls back to a `MockHardwareController` (with a warning) when `FREEMOTION_HARDWARE` is not `"pi"`, so the demo also runs on a dev laptop. Calls `controller.cleanup()` from the agent's shutdown path.
- **`examples/pi_bench_demo/README.md`** — operator-grade walkthrough: required Pi model, wiring (BCM 27 / 22 default with a clear "do not drive motors from these pins" warning), install, every env var the runtime reads, exact command tour with expected replies, the safety / deny / dry-run behaviors to verify on the bench, systemd autostart, and a comparison against `pipe_check`, `mock_drone`, and `local_sim_demo`.
- **`examples/pi_bench_demo/systemd/freemotion-pi-bench-demo.service`** — user-level systemd unit mirroring the `pipe_check` pattern.
- **CI** — import smoke now also covers `pi_bench_demo` and the `PiHardwareController` lazy-import path on a non-Pi GitHub runner.
- **Tests** — `tests/test_pi_bench_demo.py` (8 tests): import smoke, exact Phase 2 command-set registration, `denied_commands` propagation, `stop` exempt-from-deny dispatch, `denied_by_policy` on a denied `arm`, `/stop` actually drives the controller back to idle, `/status` carries controller telemetry, `/move` in `dry_run` does not change position. **157 tests pass.**

Phase 2 gate met: the demo boots from documented env vars only, runs through `/capabilities` → `/status` → `/arm` → `/move` → `/stop` → `/disarm` end-to-end against either a real Pi or a mock fallback, and `/stop` always succeeds (including under `FREEMOTION_DENIED_COMMANDS=arm,move`). Phase 3 (safety-mode hardening on real hardware) is next.

### Added (M4 Phase 1 — first real hardware proof, controller foundation)

- **`PiHardwareController`** (`freemotion/hardware/pi.py`) — bench-safe `HardwareController` for Raspberry Pi. Each state transition flips a real GPIO pin: `armed_pin` HIGH while armed, `moving_pin` pulsed HIGH for `move_pulse_s` on each successful `move()`. `RPi.GPIO` is imported lazily; tests inject a `FakeGPIO` adapter via the `gpio` arg. Hardware exceptions are caught and logged: `arm()` / `move()` return `False` on failure, `stop()` always swallows. Per ADR-0004, `stop()` does not acquire the controller lock — it must succeed even mid-`move()`.
- **Hardware factory** — `make_controller_from_config(config)` in `freemotion.hardware` picks `PiHardwareController` for `FREEMOTION_HARDWARE=pi` (lazy import, so non-Pi hosts stay clean) and `MockHardwareController` everywhere else. Unknown profiles log a warning.
- **Config** — new `pi_armed_pin` / `pi_moving_pin` fields parsed from `FREEMOTION_PI_ARMED_PIN` / `FREEMOTION_PI_MOVING_PIN`. Empty / non-integer values fall back to the controller's defaults (BCM 27 / 22).
- **Tests** — `tests/test_pi.py` (22 tests) covers the controller via `FakeGPIO`: protocol satisfaction, GPIO setup, arm/disarm/stop/move happy paths, position accumulation, non-numeric `move` args, hardware failure paths (`arm` and `move` return `False`, `stop` swallows), offline-mode behavior when setup fails, `cleanup` releases pins, and the factory monkeypatches `RPi.GPIO` to construct a real `PiHardwareController` on a non-Pi host. CI runs without `RPi.GPIO`.

Still tracked under M4 in [`docs/issues/m2-m3.md`](docs/issues/m2-m3.md):

- Phase 2 — `examples/pi_bench_demo/` end-to-end Telegram path on a real Pi.
- Phase 3 — Safety-mode enforcement on real hardware (`dry_run` non-actuating, `bench` allowed primitive, `stop` exempt).
- Phase 4 — Pi setup/runtime docs, README + ROADMAP refresh.

Still tracked from M2/M3:

- `YoloVision` adapter behind `FREEMOTION_VISION_BACKEND=yolo` (M3).
- `GemmaMissionControl` adapter behind `FREEMOTION_MISSION_BACKEND=gemma` (M3).

## [0.1.0-alpha] — 2026-05-03

The first runnable cut. Free Motion can be installed, demoed end-to-end on a laptop with no hardware, and extended.

### What works

- **Telegram transport** (M0). Bot path verified end-to-end on Raspberry Pi.
- **Protocol v0** (M1). Typed `Command` and `Reply` envelopes, slash sugar plus JSON, stdlib only. Versioned at `v: 0` per [`docs/protocol.md`](docs/protocol.md).
- **Device runtime** (M2). `freemotion.config`, `freemotion.router`, `freemotion.agent` compose into a long-running service. Built-in handlers for `ping`, `status`, `capabilities`, `stop`, `arm`, `disarm`, `move`.
- **Mock hardware** (M2). `HardwareController` Protocol with `MockHardwareController`. Lets contributors build the runtime without a Pi.
- **Per-command deny list** (M2). `FREEMOTION_DENIED_COMMANDS` (CSV) → `Config.denied_commands`, enforced in `Router.dispatch`. New `ErrorCode.DENIED_BY_POLICY`. `stop` is exempt unconditionally; listing it warns and drops it.
- **Vision and mission control interfaces** (M3 partial). `VisionBackend` and `MissionPolicy` Protocols, plus `MockVision` and `MockMissionControl`. The structural pattern YOLO and Gemma will follow.
- **World state v1** (M3). `freemotion/world/` ships `WorldStateSnapshot` (immutable) and `WorldState` (lock-protected wrapper). `MissionPolicy.plan(world=...)` is typed `WorldStateSnapshot`.
- **Three demos.** `examples/local_sim_demo.py` (no setup, threads world state through every tick), `examples/mock_drone/` (Telegram + mocks), `examples/pipe_check/` (real Pi).
- **124 tests** covering protocol, config, router (incl. deny policy), agent, builtins, hardware, vision, mission_control, world (incl. concurrency), pipe_check, and the local sim end-to-end.
- **CI** runs lint + import smoke + the full test suite on every push.

### What is mocked

These ship as deterministic mock backends only. Real adapters are tracked as separate issues:

- YOLO (real vision detection).
- Gemma small (real mission control).
- Pi hardware controller (real motor / autopilot link).

Swap path documented in [`docs/models.md`](docs/models.md). The interfaces are stable; real adapters won't change them.

### Known limitations

- No live hardware demo yet (M4 is gated on real hardware sign-off and a recorded demo clip).
- No Jetson, ESP32, or Arduino support (M5).
- Single transport (Telegram). The protocol is transport-agnostic; a second transport hasn't been written.
- `from` field is sender-only; multi-device fan-out (`to` required) deferred until needed.
- Allow lists are not implemented; only a deny list. Add only when a deployment actually needs deny-by-default.

### Architectural decisions

Recorded in [`docs/decisions.md`](docs/decisions.md):

- ADR-0001: protocol v0 — slash + JSON, optional `to`, sender-generated correlation id, stdlib only.
- ADR-0002: hardware abstraction starts now (small) and `move` is additive.
- ADR-0003: vision and mission control ship as interfaces + mocks; real model adapters land behind feature flags.
- ADR-0004: per-command allow/deny — allow by default, explicit deny list, `stop` always exempt, `denied_by_policy` is its own error code.
- ADR-0005: world state v1 — narrow (5 fields), lock-protected, snapshot-shaped; `MissionPolicy.plan` takes `WorldStateSnapshot` directly.

### Supported platforms

| Platform | Status |
|---|---|
| Laptop (macOS / Linux, Python 3.10+) | demos run via `examples/local_sim_demo.py` and `examples/mock_drone/` |
| Raspberry Pi 4 (Raspberry Pi OS / Ubuntu) | reference target; `examples/pipe_check/` verified |
| Jetson Nano | planned (M5) |
| ESP32 / Arduino | planned (M5) |

### Next milestone target

**M4** — one real hardware demo with full [`SAFETY.md`](SAFETY.md) sign-off.

[Unreleased]: https://github.com/SpencerBrown1717/Free_Motion/compare/v0.1.0-alpha...HEAD
[0.1.0-alpha]: https://github.com/SpencerBrown1717/Free_Motion/releases/tag/v0.1.0-alpha
