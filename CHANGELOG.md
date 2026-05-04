# Changelog

All notable changes to Free Motion are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0 minor versions may break interfaces; the protocol version is tracked separately under [`docs/protocol.md`](docs/protocol.md).

## [Unreleased]

### Added (Step 5 — `pi_follow_bench`, the named repeatable Pi benchmark: frozen 10-step protocol, frozen JSON artifact, three failure injections)

- **`examples/pi_follow_bench/`** — the named, repeatable Pi benchmark. Drives the locked Pi reference architecture ([`docs/pi-reference.md`](docs/pi-reference.md)) through a fixed 10-step command sequence (`/ping`, `/capabilities`, `/status`, `/arm`, `/mission_start <intent>`, observe, `/status`, `/stop`, `/disarm`, `/status`), applies fixed pass/fail criteria, and emits a stable JSON artifact for each run. Runner core in `benchmark.py`; CLI in `pi_follow_bench.py`; one-shot systemd unit in `systemd/freemotion-pi-follow-bench.service`. v1 scope per ADR-0013.
  - **Direct router dispatch, not Telegram.** Builds the same router `pi_closed_loop_demo` builds (via the same `build_router_without_loop` and `attach_mission_loop` helpers); dispatches `Command` envelopes through `router.dispatch(cmd)`. Round-tripping through Telegram would couple the benchmark to network latency and python-telegram-bot's retries — none of which are part of the locked Pi contract. The router is the same call path Telegram drives; dispatching directly through it exercises the deny list, the SafetyGate, the controller, and the mission loop **identically**, minus the transport.
  - **Two modes:** `--mode=ci` wires `MockHardwareController` + `MockVision(scripted=[<one person scene>])` + `MockMissionControl` for a deterministic harness that runs in ~1s on a CI runner with no Pi, no models, no env vars. `--mode=bench` wires `Config.from_env()` + the real adapters exactly the way `pi_closed_loop_demo.main()` wires them, so what passes in CI is genuinely the same code path the Pi runs.
  - **Three failure injections:** `--inject=camera_offline` (vision returns empty scenes — no detections, world goes stale, MOVE skipped), `--inject=mission_offline` (policy `available=False`, `plan()` returns idle every tick — no MOVE dispatched, no exceptions), `--inject=vision_drop_after_n` (vision raises after N successful scenes — `vision_failures` grows, MOVE dispatched in the clean window, world goes stale). The benchmark always runs all 10 steps under every inject; what changes is the *expected outcome*, not the protocol. Universal contracts (no crash, `/stop` returns ok, pins LOW at end, loop reads idle after stop, capabilities match locked surface) **must hold under every inject** — that's the safety story.
  - **Frozen pass/fail criteria** (8 first-class flags + the `success` rollup). Universal: `all_commands_ok`, `capabilities_match_locked_surface`, `loop_reached_running` + `loop_ticks_met`, `loop_stopped_clean`, `pins_low_at_end`. Mode-aware: `no_unexpected_failures` (zero counters in clean mode; bounded counters per inject), `move_dispatches_observed` + `min_move_dispatches_required`. The rollup is `success = AND(every flag)`. The artifact carries each flag and each counter separately so a failed run points at the offending contract.
  - **Frozen JSON artifact (schema v1).** Documented field-by-field in [`docs/pi-benchmark.md` §3](docs/pi-benchmark.md). Default location: `~/.cache/freemotion/results/pi_follow_bench-<mode>[-<inject>]-<utc-stamp>.json`. Override with `--output PATH`; pass `--output -` for stdout. New fields are additive (readers must tolerate them); removing or renaming requires bumping the schema version.
  - **Operator knobs (CLI flags only, not protocol):** `--hold` (observation window, default 5.0s), `--tick-interval` (default 1.0s), `--stale-world-timeout` (default 5.0s), `--min-loop-ticks` (default 1), `--min-move-dispatches` (default 0; recommended 1 for CI clean runs), `--intent` (default `"follow person"`), `--vision-drop-after` (default 3). Each tunable is recorded in the artifact's `config_summary` / top-level fields so a future reader can tell what was tuned.
  - **`view` subcommand:** `pi_follow_bench view <path>` reads a previously written artifact and pretty-prints the same human-readable summary the runner emits with `--print-human`. The view is computed from the JSON only — works on any machine with a copy of the artifact.
- **`docs/pi-benchmark.md`** — the frozen benchmark protocol. Documents the 10-step sequence, every pass/fail criterion, the JSON artifact schema field-by-field, the allowed variance across runs (timestamps and counter values vary; `success` and every `criteria.*` boolean must agree across runs in the same mode + inject), the operator tunables, the inject-to-failure-mode mapping, and the move-to-M5 rule (M5 Phase 1 ships when a Jetson rig produces a `pi_follow_bench`-shaped artifact).
- **`examples/pi_follow_bench/README.md`** — the operator runbook. Install, run, view, interpret. Includes the verification checklist for promoting a Pi bench rig to "ready for benchmark" and the standard `jq` / `diff` patterns for comparing runs.
- **CI** — import smoke now also covers `examples/pi_follow_bench/benchmark.py` and `examples/pi_follow_bench/pi_follow_bench.py`. Tests run `pi_follow_bench --mode=ci` and each `--inject` mode on every push.
- **ADR-0013** — locks the Step 5 design rationale: why direct router dispatch (not Telegram), why the 10-step sequence is what it is, why the criteria are what they are, why three injects (not five, not zero), why the artifact schema is the public contract, why the benchmark reuses `pi_closed_loop_demo`'s router-build helpers (architectural honesty by construction), why Step 5 ships a small amount of new code contained to `examples/`, why CI runs `--mode=ci` only (no hosted Pi runner), and why this benchmark is the M5 Phase 1 acceptance test.

**376 tests pass on every push** (+2 skips when `[yolo]` and `[picam]` aren't installed) — up from 340 in Step 4. Test breakdown for Step 5 (36 new passing tests, all in `tests/test_pi_follow_bench.py`): module imports + locked-surface match + known-injects freeze + systemd unit shape (4); clean CI run passes every contract + artifact schema verification (2); each inject produces a passing artifact (3); output paths (`--output -` to stdout, auto-create parent dirs) (2); view round-trip + missing-file path (2); bad inputs (unknown mode, unknown inject, negative hold, bad min-move-dispatches, no subcommand) (5); inject-aware failure-bound parametrized matrix (16); CI stack registers the locked 8-command surface (2). All run on a CI runner without `RPi.GPIO`, `picamera2`, `ultralytics`, or `transformers`.

This is **Step 5** of the Pi-first lockdown — the gate for M5 Jetson is now in place. The Pi-first lockdown plan (Steps 1–5) is **complete**:

- ~~Step 1 — Pi live camera integration (`PiCameraSource` + `pi_camera_demo`).~~
- ~~Step 2 — Pi full closed loop (`MissionLoop` + `pi_closed_loop_demo`).~~
- ~~Step 3 — Real-world failure-mode hardening (stale-world refusal, degraded summary, hung-tick handling, ordered graceful shutdown).~~
- ~~Step 4 — Pi reference architecture lock (`docs/pi-reference.md`, "same contract, different hardware" target for Jetson).~~
- ~~Step 5 — One repeatable Pi benchmark demo (`pi_follow_bench`).~~

Next: **M5 Phase 1 (Jetson Nano)**, gated on `pi_follow_bench` passing on a real Pi bench rig. The acceptance test for the Jetson port is documented: a Jetson rig must produce a `pi_follow_bench`-shaped artifact (renamed `jetson_follow_bench` is allowed; the schema, sequence, and criteria are not).

### Added (Step 4 — Pi reference architecture lock: one canonical Pi path, frozen surfaces, M5 = "same contract, different hardware")

- **`docs/pi-reference.md`** — the single source of truth for what a Free Motion device on a Pi *is*. Locks ten surfaces in one document:
  - **Canonical Pi path:** `examples/pi_closed_loop_demo/` is named explicitly. `pi_bench_demo` and `pi_camera_demo` are sub-paths used to debug pieces in isolation, not "alternative main paths."
  - **Command surface (frozen):** the eight commands `/ping /capabilities /status /arm /disarm /move /mission_start /stop`. Anything else needs a protocol bump per ADR-0002. `/led_on` and `/led_off` are explicitly out of scope on the reference path. Loop dispatch is restricted to MOVE only.
  - **Hardware path (frozen):** Pi 4/5 (Pi 3 with caveats); Bookworm or newer; `RPi.GPIO` BCM mode; `armed_pin = 27`, `moving_pin = 22`; Pi camera via `picamera2` / libcamera; resolution `(640, 480)` default; cleanup ordering mission_loop → controller → cam → inner_cleanup. I²C/SPI/UART/PWM/multi-camera/external GPS or IMU explicitly out of scope.
  - **Model path (frozen):** `PiCameraSource → YoloVision → WorldState → GemmaMissionControl → SafetyGate → PiHardwareController`. Default models (`yolov8n.pt`, `gemma-2-2b-it`) and override surfaces named. Fallback asymmetry (camera/vision required → exit 2/3; mission/hardware fall back to mock) documented.
  - **Env-var contract (frozen):** five tiers — required (`TELEGRAM_BOT_TOKEN` only); strongly recommended; optional backend selection; optional pin/metadata overrides; demo-only (read in the demo, not `Config`); and constructor-only tuning knobs (deliberately not env-driven).
  - **Safety contract (frozen):** twelve numbered guarantees spanning the hardware tier (1–5) and the loop tier (6–12). Each guarantee maps to code and tests.
  - **Status contract (frozen):** `controller` and `mission_loop` telemetry shapes pinned; the human-readable mission line format (`mission: <state> [DEGRADED: ...] [stale world: ...] [(intent='...')]`) is locked.
  - **Failure model (frozen):** points to `docs/pi-failure-modes.md`; locks the failure list as part of the reference architecture so M5 ports know each failure must have an analog on the new platform.
  - **Documentation alignment table:** every doc that tells the same story is enumerated; the closed-loop demo source and `pi-reference.md` are the source-of-truth pair.
  - **M5 Jetson port target:** "same contract, different hardware." Explicit must-keep list (protocol, commands, world state shape, mission decision shape, safety semantics, status semantics, `/stop` ordering, failure model) and allowed-to-differ list (controller adapter, camera adapter, hardware factory branch, model tuning, systemd unit, OS prep doc). M5 Phase 2 (ESP32) and Phase 3 (Arduino) are deliberately not locked here.
- **ADR-0012** — locks the rationale: why one canonical demo, why eight commands, why the hardware/model paths are bench-only, why the env-var contract has constructor-only knobs, why the failure model is part of the lock, why M5 Phase 1 is "same contract, different hardware," why doc alignment is part of the lock not an afterthought, why Step 4 ships zero new code.
- **Doc alignment audit** across `README.md`, `GETTING_STARTED.md`, `docs/pi-runtime.md`, `docs/pi-hardware.md`, `docs/pi-camera.md`, `docs/pi-closed-loop.md`, `docs/pi-failure-modes.md`, `docs/models.md`, `examples/pi_closed_loop_demo/README.md`, `ROADMAP.md`, `CHANGELOG.md`. Stale "Step 4 will lock", "the canonical Pi reference (M4)", and "post-M4 priority" language removed. Every doc points at `pi-reference.md` for the locked contract.
- **`GETTING_STARTED.md`** — added Path C (Pi closed-loop, the canonical reference). Path A (laptop) and Path B (bench-only) framing kept; safety-guarantees section expanded from four contracts to six to surface the loop-level guarantees.
- **`docs/pi-runtime.md`** — Config env-var table now includes `FREEMOTION_VISION_BACKEND` and `FREEMOTION_MISSION_BACKEND` (previously missing). Stale "the canonical Pi reference (M4)" replaced with a three-tier example list (closed-loop is canonical; bench and camera are sub-paths).
- **`docs/pi-hardware.md`** — "What's still mocked" table replaced with "What's optional vs. mocked" reflecting that YoloVision, GemmaMissionControl, PiCameraSource, and the closed loop are all shipped. "What comes next" updated to the Step 5 → M5 Phase 1 sequence. Examples table now includes `pi_camera_demo` and `pi_closed_loop_demo`.

**Step 4 ships zero new code.** All eight commands were already registered, all twelve safety contracts were already enforced, all telemetry keys were already exposed, all failure paths were already covered by tests. **340 tests still pass** on every push (no change vs. Step 3); no fewer, no more. The audit was the verification — every env var documented exists in code, every command listed is registered, every telemetry key documented is in `state()`, every guarantee documented is exercised by the test suite.

This is **Step 4** of the Pi-first lockdown. Step 5 (the gate for M5 Jetson) is the only remaining gate:

- **Step 5 — One repeatable Pi benchmark demo.** Named task, fixed sequence, fixed success criteria, short runbook. Becomes the gate for M5 Phase 1 (Jetson Nano).

### Added (Step 3 — real-world failure-mode hardening: stale-world refusal, degraded summary, hung-tick handling, ordered graceful shutdown)

- **Stale-world refusal in `MissionLoop`.** New `stale_world_timeout_s` (default 5.0s) refuses to dispatch MOVE while the world is older than the timeout. `_last_perception_ts` is set only on a non-empty `VisionResult`; `world_age_s = now - max(last_perception_ts, started_at)`. When stale, the loop logs and skips (`stale_world_skips` increments) but does **not** count the skip as a `dispatch_failure` — it's a separate signal class. Recovery is automatic: a single non-empty scene resets the clock. ADR-0011 records why "act on the freshest perception or don't act at all" is the right safety floor for the LLM-driven case (Gemma cannot act on a 30s-old world).
- **Per-stage consecutive failure counters and a single `degraded` flag.** `consecutive_vision_failures`, `consecutive_mission_failures`, `consecutive_dispatch_failures` reset on the first success of that stage. Crossing `degraded_threshold` (default 5) on any one stage flips `degraded=True` with a `degraded_reason` string identifying the responsible stage(s). The transition is logged at `warning` level; the flag clears automatically when every stage drops below threshold. Degraded is a **visibility signal, not a self-stop** — the operator decides whether to `/stop`.
- **Hung-tick handling.** `mission.plan()` blocking past `join_timeout_s` (Gemma in CUDA) cannot be force-killed (Python provides no safe primitive). Instead `stop()` sets the event, waits, and if the worker is still alive: leaves `_thread` set so a subsequent `start()` refuses (no zombie thread leak), clears `_intent` and `_started_at` so `/status` reads as `mission: idle`, and logs the hang at `warning` level. The hardware controller is stopped *first* by the demo's composite `on_stop`, so the pins are LOW even when the worker is hung. When the hung tick eventually returns, the next `start()` reaps the now-dead thread automatically — restart-after-hang is the supported recovery path.
- **`start()` reaps a dead orphan thread before checking aliveness.** The new branch handles the live-hung → eventually-died-naturally → fresh-start case without operator intervention. Pre-Step 3, `_thread` was always cleared by `stop()`; post-Step 3, `start()` does the cleanup when it can.
- **Vision contract violations count as failures.** A vision backend returning anything other than `VisionResult` increments `vision_failures` rather than crashing the loop. Same defensive pattern ADR-0010 introduced for `mission.plan()` returning a non-`MissionDecision`.
- **`_format_mission_loop_line` helper** in `freemotion/agent/builtins.py` builds the human-readable `mission: ...` summary in `/status`: base state, optional `[DEGRADED: reason]` badge, optional `[stale world: 8.3s]` badge, optional `(intent='...')`. The structured equivalents already live in `telemetry.mission_loop`; this is the badge string an operator scanning Telegram can act on without re-parsing JSON. Stale-world badge is suppressed when the loop is idle (a stopped loop is not actively stale).
- **`stop_requested` exposed in `state()`.** Operators reading `/status` mid-`/stop` can distinguish "loop is idle" from "stop has been asked but the worker hasn't joined yet" — the disambiguator for the hung-tick case.
- **`graceful_shutdown(...)` helper** in `examples/pi_closed_loop_demo/pi_closed_loop_demo.py`. Replaces the inlined teardown in `main`'s `try/finally`. Order is the survivability contract: **mission_loop.stop FIRST**, then controller.stop, then cam.close, then inner.cleanup. Stopping the loop first means no in-flight tick can dispatch a fresh MOVE *after* the controller is stopped. Each step swallows its own exceptions so a single broken layer cannot block the rest of the teardown. The helper is **safe to call from any thread**, including a signal handler context, because every underlying primitive (`Event.set`, `Thread.join`, GPIO writes, `picamera2.close`) is already idempotent and signal-safe in practice.
- **`docs/pi-failure-modes.md`** — canonical operator-facing reference for every environmental failure the Pi closed loop is contracted to survive: camera unplugged, bad-frame storms, YOLO offline mid-loop, Gemma errors / hangs, OOM, SIGTERM, network drops, stale world, repeated dispatch failures, restart and recovery. Each failure has a four-row table (symptom / runtime behavior / `/status` signal / operator action) plus a one-page operator runbook section ("what to do when it goes wrong"). Cross-references ADRs 0006 / 0009 / 0010 / 0011.
- **ADR-0011** — locks the Step 3 design rationale: stale-world timeout, per-stage consecutive counters with degraded summary, hung-tick handling preserving `_thread`, `start()` reaping dead orphan threads, `graceful_shutdown` ordering, why camera-handle recovery and a network-drop watchdog are explicitly out of scope.

**340 tests pass on every push** (+2 skips when `[yolo]` and `[picam]` aren't installed). Test breakdown for Step 3 (31 new passing tests): 17 in `tests/test_mission_loop.py` (stale-world skip + recovery, world-age in `state()`, consecutive counters + reset on success, degraded transitions in/out, multi-stage degraded reason, dispatch-success clears degraded, restart-after-stop, reset-all-counters-on-start, hung-mission stop preserves thread reference, dead-orphan reap on start, vision contract violation, camera-unplugged-mid-loop), 7 in `tests/test_pi_closed_loop_demo.py` (`graceful_shutdown` ordering, exception tolerance per layer, idempotency, polymorphism over `inner.cleanup`, `[DEGRADED]` and `[stale world]` badges in `/status`), 7 in `tests/test_builtins.py` (`_format_mission_loop_line` formatting matrix). All run on a CI runner without `RPi.GPIO`, `picamera2`, `ultralytics`, or `transformers`.

This is **Step 3** of the Pi-first lockdown that gates all Jetson work. Steps 4 and 5 are still ahead:

- **Step 4 — Pi reference architecture lock.** One canonical Pi stack doc; supported commands, hardware path, model path, env-var set; docs match code exactly. The Pi path becomes copy-able to Jetson.
- **Step 5 — One repeatable Pi benchmark demo.** Named task, fixed sequence, fixed success criteria, short runbook. Becomes the gate for Jetson.

### Added (Step 2 — Pi full closed loop: `MissionLoop` + `pi_closed_loop_demo`)

- **`MissionLoop`** (`freemotion/agent/mission_loop.py`) — the runtime primitive that ties every shipped piece into one continuous loop on the Pi. v1 scope per ADR-0010:
  - Background daemon thread runs `vision.scene() → world.see(...) → mission.plan(intent, scene, world) → router.dispatch(...)` per tick. The router dispatches go through the same deny-list, `make_move_handler`, and `SafetyGate` that operator-driven `/move` already does — no hidden actuation paths.
  - **Only `MOVE` is dispatched from the loop.** Mock and Gemma policies can return any `CommandName`; everything other than MOVE is logged and ignored. ARM / DISARM / STOP stay strictly Telegram-driven so an LLM hallucination cannot arm or disarm the device.
  - **Per-stage failure isolation.** `vision.scene()`, `mission.plan()`, and `router.dispatch()` are each wrapped in their own try/except with their own counter (`vision_failures`, `mission_failures`, `dispatch_failures`). The thread's outer try/except is belt-and-suspenders — a surprise from any layer cannot kill the loop or the agent. Mission `plan()` returning a non-`MissionDecision` is normalized to idle.
  - **`/stop` interrupts mid-tick.** The tick wait is `threading.Event.wait(interval)`; `stop()` sets the event so a long tick interval (e.g. 60s) doesn't make `/stop` slow. The thread joins within `join_timeout_s` (default 2s).
  - **`start(intent=...)` is idempotent.** Re-issuing `/mission_start` while running returns "already running" rather than spawning a second thread or overwriting the intent. Same for `stop()` (no-op when never started, no-op when already stopped).
  - **World state ordering fix.** `WorldState.see(label)` overwrites `target` on every call (M3 semantic), so the loop processes the top-3 detections from lowest to highest confidence — last `see()` wins and the highest-confidence detection ends up as `target`. `last_seen` accumulates regardless of order.
  - **`state()` is the single telemetry seam.** Returns a dict shaped for `/status`: `running`, `intent`, `tick_count`, `last_decision`, `last_dispatched`, `last_dispatch_ok`, three failure counters, `started_at`, `uptime_s`. Holds the internal lock only briefly so callers from any thread (including a Telegram `/status` handler reading state while the loop is mid-tick) are cheap.
- **`MISSION_START` command** (`freemotion/protocol/envelopes.py`, `freemotion/protocol/codec.py`) — additive `CommandName` per ADR-0002, no protocol `v` bump. Slash sugar `/mission_start [intent...]` packs trailing tokens into `args["intent"]`; empty intent is permitted (the handler falls back to its `default_intent`). JSON envelopes use `args: {"intent": "..."}` directly.
- **`make_mission_start_handler(cfg, mission_loop=...)`** (`freemotion/agent/builtins.py`) — Telegram entry point for the mission loop. Refused in `dry_run` (per ADR-0010, no perception-blind loop on real hardware in dry_run). Re-issues while running return `"already running"` rather than spawning a second loop. Wraps `loop.start()` exceptions as `INTERNAL` replies so a misconfigured loop never crashes the handler.
- **`make_status_handler` extended** with optional `mission_loop=...`. When set, surfaces `mission_loop.state()` under `telemetry["mission_loop"]` and adds a one-line `mission: running (intent='...')` / `mission: idle` summary to the human-readable message. Existing call sites are unaffected.
- **`make_vision_from_config` extended** to accept an optional `frame_source=...`. The factory returns `YoloVision(frame_source=...)` when both `vision_backend=yolo` and a frame source are passed; otherwise the existing behavior. The closed-loop demo wires `PiCameraSource()` through this seam so `make_vision_from_config` keeps owning backend selection while the demo keeps owning camera lifecycle.
- **`examples/pi_closed_loop_demo/`** — first **end-to-end** Free Motion device. Wires `Config.from_env` → `PiCameraSource` → `make_vision_from_config(cfg, frame_source=cam)` → `make_mission_from_config(cfg)` → `WorldState` → `make_controller_from_config(cfg)` → `SafetyGate(cfg.safety_default)` → `Router` → `MissionLoop` → `Agent` → Telegram. Two-pass build (`build_router_without_loop` + `attach_mission_loop`) resolves the loop-vs-router circular wiring without leaking placeholders into either object. `/stop` is the master kill: composite `on_stop` callback halts the loop *first* (so no in-flight tick can dispatch a MOVE after the controller stops), then drives the controller pins LOW. Exit codes: `0` clean, `2` camera offline (no `picamera2`), `3` vision offline (no `ultralytics` or model unreachable). Includes operator README and autostart systemd unit.
- **CI** — import smoke now also covers `from freemotion.agent import MissionLoop` and `import pi_closed_loop_demo` to confirm the closed-loop wiring imports cleanly on a non-Pi GitHub runner without `picamera2`, `ultralytics`, `transformers`, or `RPi.GPIO`.
- **Docs** — `docs/decisions.md` ADR-0010 locks the v1 design (own-thread loop, MOVE-only dispatch, dry_run refusal, two-pass router build, fail-isolated stages, world-state ordering). New `docs/pi-closed-loop.md` is the canonical end-to-end reference: architecture diagram, supported command surface, env vars, the loop body in pseudocode, the full failure model, the `/status` shape, and the operator runbook. README's "Default stack vs. swappable stack" table now lists `MissionLoop`; "Current status" reflects Step 2 shipped and Step 3 in progress; "Repository tour" lists `pi_closed_loop_demo/` and `docs/pi-closed-loop.md`.

**309 tests pass on every push** (+2 skips when `[yolo]` and `[picam]` aren't installed). Test breakdown for Step 2: 22 in `tests/test_mission_loop.py` (lifecycle, scope filtering, every per-stage failure path, telemetry shape), 14 in `tests/test_pi_closed_loop_demo.py` (router shape, master-kill `/stop`, mission_loop telemetry on `/status`, `dry_run` refusal, idempotent `/mission_start`, exit-code paths), 7 in `tests/test_builtins.py` (mission_start handler + status with loop), 6 in `tests/test_protocol.py` (`MISSION_START` slash + JSON parsing) — 49 new passing tests in this step.

This is **Step 2** of the Pi-first lockdown that gates all Jetson work. Steps 3–5 are still ahead:

- **Step 3 — Real-world failure-mode hardening.** Move from structural failures (already covered) to environmental failures: camera unplugged mid-mission, YOLO/Gemma OOM mid-tick, stale or empty world state for >N ticks, `/stop` arriving mid-`move()`, SIGTERM during a tick.
- **Step 4 — Pi reference architecture lock.** One canonical Pi stack doc; supported commands, hardware path, model path, env-var set; docs match code exactly. The Pi path becomes copy-able to Jetson.
- **Step 5 — One repeatable Pi benchmark demo.** Named task, fixed sequence, fixed success criteria, short runbook. Becomes the gate for Jetson.

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
