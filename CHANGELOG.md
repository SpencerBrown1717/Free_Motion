# Changelog

All notable changes to Free Motion are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0 minor versions may break interfaces; the protocol version is tracked separately under [`docs/protocol.md`](docs/protocol.md).

## [Unreleased]

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
