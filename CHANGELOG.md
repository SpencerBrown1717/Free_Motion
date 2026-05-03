# Changelog

All notable changes to Free Motion are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0 minor versions may break interfaces; the protocol version is tracked separately under [`docs/protocol.md`](docs/protocol.md).

## [Unreleased]

### Added (Step 1 — Pi live camera integration: `PiCameraSource` + `pi_camera_demo`)

- **`PiCameraSource`** (`freemotion/vision/picamera.py`) — canonical Pi camera frame producer for the existing `YoloVision(frame_source=...)` seam. v1 scope per ADR-0009:
  - Callable like a frame producer: ``cam()`` returns the latest frame as a numpy array, or ``None`` on any failure. Drops straight into `YoloVision` without a wrapper.
  - Backed by ``picamera2`` (the modern libcamera-based stack on Pi OS Bookworm and newer). USB webcams stay un-wrapped — `cv2.VideoCapture(0).read`-shaped lambdas already work and a wrapper would be cargo-culting `PiCameraSource`'s lifecycle quirks.
  - **Lazy `picamera2` import** inside `__init__`. Module imports cleanly on a host without the optional dep. Construction failures (import missing, camera busy, configure raises, start raises) flip the source offline (`available is False`, `cam()` returns `None`); the agent loop is unaffected. The constructor calls `stop()` + `close()` on the partial camera handle so re-running the demo after a failed start doesn't trip "camera busy."
  - **Per-call capture failures don't latch the source offline.** A single failed capture returns `None` for that tick, increments `cam.capture_failures`, and the next call retries. The counter is exposed as a property so `/status` (Step 2) can surface it without scraping logs.
  - **`close()` is idempotent and never raises.** Underlying exceptions are caught and logged; subsequent `close()` calls hit the same flag and return.
  - **Per-call capture does not acquire the source's lock.** A slow `capture_array()` cannot block `close()`, `available`, or `capture_failures` readers. Architectural pre-requisite for "Step 1 acceptance criterion: `/status` still works while camera is active."
  - Resolution fixed at construction (`(640, 480)` default — the YOLO nano sweet spot for Pi 4 CPU). Build a new source if you need a different resolution.
  - `picam_factory` injection seam for tests: 16 of 17 source tests run without `picamera2` via `_FakePicam`. The 17th is a `pytest.importorskip("picamera2")` smoke that runs only when `[picam]` is installed (and only verifies the import path, not real camera open — that requires real hardware).
- **`examples/pi_camera_demo/`** — standalone Pi camera + YOLO loop. No Telegram, no router, no agent, no hardware controller. Boots `PiCameraSource` → `YoloVision` → prints person detections per tick. Exits cleanly on SIGINT / SIGTERM with `cam.close()`. Exit codes: `0` clean, `2` camera offline, `3` YOLO offline. Includes operator README, autostart systemd unit. 6 of 23 new tests cover the demo's exit-code contract and lifecycle (camera offline, YOLO offline, max-ticks loop, README/systemd unit presence).
- **`pyproject.toml`** — new `[picam]` extra (`picamera2>=0.3`). Base install stays stdlib + `python-telegram-bot`.
- **CI** — import smoke now also covers `from freemotion.vision import PiCameraSource` and `import pi_camera_demo` to confirm the lazy-import discipline holds on a non-Pi GitHub runner.
- **Docs** — `docs/decisions.md` ADR-0009 locks the v1 design (picamera2-backed, callable producer not a backend, transient-failure tolerant, no-USB scope, no-thread synchronous capture, lock-free per-call). New `docs/pi-camera.md` is the canonical reference: setup, what the source does, what it doesn't do, the failure model, USB webcam alternative, troubleshooting, and where it fits in the closed-loop architecture (Step 2).

**260 tests pass on every push** (+2 skips when `[yolo]` and `[picam]` aren't installed). Test breakdown for the new code: 16 + 1 skip in `tests/test_pi_camera_source.py`, 6 in `tests/test_pi_camera_demo.py` — 22 new passing tests in this step.

This is **Step 1** of the Pi-first lockdown that gates all Jetson work. The next four steps:

- **Step 2 — Pi full closed loop.** Telegram → live YOLO → `WorldState` → Gemma → bench-safe hardware action → `/status` → repeat. `/stop` interrupts unconditionally.
- **Step 3 — Real-world failure-mode hardening.** Camera missing / YOLO unavailable / Gemma unavailable / stale world / stop-during-move / signal-interruption all return protocol-shaped replies; nothing actuates in `dry_run`.
- **Step 4 — Pi reference architecture lock.** One canonical Pi stack doc; supported commands, hardware path, model path, env-var set; docs match code exactly.
- **Step 5 — One repeatable Pi benchmark demo.** Named task, fixed sequence, fixed success criteria, short runbook. Becomes the gate for Jetson.

### Added (post-M4 — `GemmaMissionControl`, first real decision adapter)

- **`GemmaMissionControl`** (`freemotion/mission_control/gemma.py`) — `MissionPolicy` backed by an instruction-tuned Gemma model served through `transformers`. v1 scope per ADR-0008:
  - One inference per `plan()` call, returning a single `MissionDecision`. No multi-step plans, agent loops, or tool use — the v1 contract from ADR-0003 is preserved verbatim.
  - Output is parsed from a tolerant JSON-extraction step: find the first balanced `{...}` block, `json.loads` it, normalize unknown commands to `None`, default missing fields, clamp `confidence` to `[0, 1]`. Anything unparseable collapses to an idle decision with a clear reason; nothing crashes upstream.
  - **Lazy `transformers` import** inside `__init__`; module imports cleanly on a host without `[gemma]` installed. Construction failures (transformers absent, model load raises) flip the adapter offline (`available is False`); `plan()` returns idle decisions with the failure reason. Inference exceptions (`client.generate(...)` raising) are caught the same way.
  - Default model is `google/gemma-2-2b-it`; defaults `max_new_tokens=128`, `temperature=0.1`. Override on the constructor.
  - `_LLMClient` seam is a one-method duck type (`generate(prompt: str) -> str`); the default implementation wraps `transformers` with the Gemma chat template applied when the tokenizer ships one. Tests inject a `_FakeLLM`.
  - `build_prompt` and `parse_decision` are free functions, importable and unit-testable in isolation.
  - `next_command` resolves against `CommandName`'s wire values; new protocol commands automatically become available to the policy without code changes here. When `next_command=None`, `args` is wiped — args attached to a rejected action would mislead downstream.
- **Factory** — `make_mission_from_config(config)` returns `GemmaMissionControl()` for `FREEMOTION_MISSION_BACKEND=gemma`, `MockMissionControl()` everywhere else. Unknown values warn and fall back to mock.
- **Config** — new `mission_backend: str` field (default `"mock"`), parsed from `FREEMOTION_MISSION_BACKEND`. Only `mock` and `gemma` are valid in v1; unknowns warn and fall back. 3 new config tests.
- **`pyproject.toml`** — new `[gemma]` extra (`transformers>=4.40,<5`, `torch>=2`). Base install stays stdlib + `python-telegram-bot`.
- **CI** — import smoke now also covers `from freemotion.mission_control import GemmaMissionControl, make_mission_from_config` to confirm the lazy-import discipline holds on a runner without `[gemma]`.
- **Docs** — `docs/decisions.md` ADR-0008 locks the v1 design (transformers-backed, single decision, tolerant JSON parser, fail-offline, no real-dep smoke test, factory mirrors the YOLO precedent). `docs/models.md` flips the Mission Control section from "planned" to "shipped" with install/wire/factory examples and a mock-vs-Gemma comparison table.
- **No real-dep smoke test.** `transformers` is heavy enough that some installs hang or SIGFPE on `import transformers` in ways that even subprocess-isolated probes can't escape — the child can wedge in uninterruptible kernel state. The 37 structural tests in `tests/test_mission_gemma.py` cover the entire contract via injected fakes; CI's import-smoke step still imports the `freemotion.mission_control` module to confirm the lazy-import path stays clean. ADR-0008 records the rationale.

**238 tests pass on every push** (+1 skip when `[yolo]` isn't installed). Test breakdown for the new code: 37 in `tests/test_mission_gemma.py`, +3 in `tests/test_config.py`.

Next, in priority order:

- **Jetson Nano** (M5). Same `HardwareController` Protocol; new adapter class + example. Unlocks heavier on-device vision.
- **ESP32 / Arduino** (M5). Bridge / coprocessor patterns.
- **Rate limits, watchdogs, link-loss fail-safe** (Safety, post-M4 continued).

### Added (post-M4 — `YoloVision`, first real perception adapter)

- **`YoloVision`** (`freemotion/vision/yolo.py`) — `VisionBackend` backed by `ultralytics` YOLO. v1 scope per ADR-0007:
  - Person detection by default (`classes=frozenset({"person"})`); override with `classes=[...]`, or `classes=[]` to accept every label. Class ids without a name in the model fall back to their stringified id.
  - One model, one threshold (`yolov8n.pt`, `confidence=0.25` — both constructor args, both defaults match Ultralytics's CLI).
  - Caller-injected `frame_source: Callable[[], Any]`. The backend does not own the camera. Plug in `cv2.VideoCapture`, `picamera2`, MJPEG, or a directory of test frames without changing this file.
  - `min_interval_s` throttle as the "cheap `scene()`" contract; default `0.0` (no throttle).
  - bbox locked to `(x, y, w, h)` normalized 0..1, **top-left corner-based**. Ultralytics's center-based `xywhn` is converted internally and clamped to the unit square.
  - **Lazy `ultralytics` import** inside `__init__` so the module imports cleanly on a host without the optional dep. Hardware/inference exceptions are caught: `available is False` and `scene()` returns empty rather than crash. The agent loop never sees a vision-induced crash.
  - `yolo_factory` injection for tests: 24 of 25 yolo tests run without `ultralytics`/`torch` via `_FakeYOLO`. The 25th is a `pytest.importorskip("ultralytics")` smoke that runs only when `[yolo]` is installed.
- **Factory** — `make_vision_from_config(config)` returns `YoloVision()` for `FREEMOTION_VISION_BACKEND=yolo`, `MockVision()` everywhere else. Unknown values warn and fall back to mock.
- **Config** — new `vision_backend: str` field (default `"mock"`), parsed from `FREEMOTION_VISION_BACKEND`. Only `mock` and `yolo` are valid in v1; unknowns warn and fall back. 3 new config tests.
- **`pyproject.toml`** — new `[yolo]` extra (`ultralytics>=8.0,<9`). Base install stays stdlib + `python-telegram-bot`.
- **CI** — import smoke now also covers `from freemotion.vision import YoloVision, make_vision_from_config` to confirm the lazy-import discipline holds on a runner without `[yolo]`.
- **Docs** — `docs/decisions.md` ADR-0007 locks the v1 design (ultralytics, lazy imports, callable frame source, person-only default, corner-based bbox, `min_interval_s` cache contract, no camera plumbing in this module). `docs/models.md` flips the Vision section from "planned" to "shipped" with install/wire/factory examples.

**201 tests pass on every push** (+1 skip when `[yolo]` isn't installed). Test breakdown for the new code: 25 in `tests/test_vision_yolo.py`, +3 in `tests/test_config.py`.

Next, in priority order:

- **`GemmaMissionControl` adapter** behind `FREEMOTION_MISSION_BACKEND=gemma` and a `pip install -e .[gemma]` extra. Same `MissionPolicy` Protocol; `MockMissionControl` is the structural reference.
- **Jetson Nano** (M5).
- **ESP32 / Arduino** (M5).

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
