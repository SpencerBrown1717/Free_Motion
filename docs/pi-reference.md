# Pi reference architecture (Step 4 — locked)

This is the **single source of truth** for what a Free Motion device on a Raspberry Pi *is*. Step 4 of the Pi-first lockdown plan: name the canonical Pi path; freeze the supported command set, hardware path, model path, env-var contract, safety contract, status contract, and failure model; define what M5 Jetson must keep identical and what is allowed to differ.

> **Status.** Locked. Locking decisions are recorded in [ADR-0012](decisions.md). Step 5 shipped the named benchmark — [`pi_follow_bench`](../examples/pi_follow_bench/) — that verifies this exact contract on a real rig with a fixed 10-step protocol and a stable JSON artifact ([ADR-0013](decisions.md), [`docs/pi-benchmark.md`](pi-benchmark.md)). M5 Phase 1 (Jetson Nano) ports this contract to a different SoC; the acceptance test is a Jetson rig producing a `pi_follow_bench`-shaped artifact. Nothing in M5 is allowed to drift the surfaces frozen here without an ADR.

---

## 1. The canonical Pi path

There is **one** official Pi reference demo:

> **[`examples/pi_closed_loop_demo/`](../examples/pi_closed_loop_demo/)** — Telegram → Pi camera → YOLO → world state → Gemma → SafetyGate → Pi GPIO → `/status`.

`pi_bench_demo` is the M4 hardware-only sub-path used for verifying the controller and SafetyGate without perception or mission control. `pi_camera_demo` is the Step 1 perception-only sub-path used for verifying camera + YOLO without Telegram or hardware. Both are scaffolding; the closed-loop demo is what a Free Motion device looks like end-to-end.

```
                            CANONICAL Pi PATH
                                  │
                                  v
        examples/pi_closed_loop_demo/pi_closed_loop_demo.py
                                  │
   ┌──────────────────────────────┼──────────────────────────────┐
   │                              │                              │
   v                              v                              v
pi_bench_demo                pi_camera_demo                   (subset of)
(controller +                (camera +                       pi_closed_loop_demo
 SafetyGate only)             YOLO only)                      = the canonical
                                                                  reference
```

If a contributor asks "what's a Pi Free Motion device?" the answer is `examples/pi_closed_loop_demo/`. Other demos exist to debug pieces of it.

---

## 2. Supported command surface (frozen)

The Pi reference path supports **exactly these eight commands**. Anything else is out of scope for v1; new commands require a protocol bump per [ADR-0002](decisions.md).

| Slash form | JSON `cmd` | Effect | Refused in `dry_run`? | Loop-dispatchable? |
|---|---|---|:-:|:-:|
| `/ping` | `ping` | round-trip liveness | no | no |
| `/capabilities` | `capabilities` | list registered handlers | no | no |
| `/status` | `status` | host + safety + controller + mission_loop | no | no |
| `/arm` | `arm` | drive `armed_pin` HIGH | yes | **no** (operator only) |
| `/disarm` | `disarm` | drive `armed_pin` LOW | no | **no** (operator only) |
| `/move x y z` | `move` | one-shot operator move; pulse `moving_pin` | yes (logs "would move") | **yes** (the only one) |
| `/mission_start [intent]` | `mission_start` | start the background loop | yes | n/a |
| `/stop` | `stop` | unconditional master kill | no — always passes through | **no** (operator only) |

**Out of scope on the Pi reference path** (deliberately not supported, not undocumented):

- `led_on` / `led_off` — `pipe_check`-only commands. Do not register them on the closed-loop device. The closed-loop demo does not register them, and `/capabilities` will not advertise them.
- Multi-step plans, free-form autonomy, tool use — out of scope by [ADR-0003](decisions.md). The mission control layer returns one structured `MissionDecision` per call; nothing more.
- Free flight, motor drivers, ESCs, propellers — out of scope until a future hardware adapter and a future safety mode ship together. The Pi reference is **bench-only** by construction.

**Loop dispatch is restricted to MOVE** ([ADR-0010](decisions.md)). Mission policies (Mock, Gemma, future) can return any `CommandName`; the loop only forwards MOVE. ARM, DISARM, STOP stay strictly operator-driven via Telegram so an LLM hallucination cannot arm or disarm the device.

`/stop` is **unconditional**: exempt from the deny list ([ADR-0004](decisions.md)), exempt from the SafetyGate ([ADR-0006](decisions.md)), composed at the demo level with `mission_loop.stop()` first then controller pins LOW ([ADR-0011](decisions.md)).

---

## 3. Supported hardware path (frozen)

| Layer | Reference choice | Module / file |
|---|---|---|
| Pi target | Pi 4 (4 GB+) or Pi 5. Pi 3 supported with caveats; Pi Zero 2 W is too small for the Gemma load. | n/a |
| OS | Raspberry Pi OS Bookworm or newer. The legacy `picamera` (mmal) stack is **not** supported. | [`docs/pi-setup.md`](pi-setup.md) |
| GPIO library | `RPi.GPIO` (BCM mode), lazy-imported. | [`freemotion/hardware/pi.py`](../freemotion/hardware/pi.py) |
| `armed_pin` | BCM 27 (physical 13). HIGH while armed, LOW otherwise. | `PiHardwareController._armed_pin` |
| `moving_pin` | BCM 22 (physical 15). Pulsed HIGH for ~100 ms on each successful `move()`. | `PiHardwareController._moving_pin` |
| Bench-safe primitive | GPIO output to two indicator pins. **No PWM, no motor drivers, no propellers.** | `PiHardwareController.move()` |
| Camera | Raspberry Pi Camera Module (CSI ribbon) via `picamera2` / libcamera. USB webcams are supported but go through `cv2.VideoCapture(0).read`-shaped lambdas, **not** through `PiCameraSource`. | [`freemotion/vision/picamera.py`](../freemotion/vision/picamera.py) |
| Camera resolution | `(640, 480)` default — the YOLO-nano sweet spot for Pi 4 CPU. Construct a new source for any other resolution. | `PiCameraSource(resolution=(640, 480))` |
| Cleanup ordering | mission_loop.stop → controller.stop → cam.close → inner.cleanup. | `pi_closed_loop_demo.graceful_shutdown` |
| Cleanup trigger | SIGTERM / SIGINT (handled by `app.run_polling`'s default signal handlers); `/stop` for in-band master kill. | demo `try/finally` |

Wiring detail and photos are in [`docs/pi-hardware.md`](pi-hardware.md). Camera setup and USB-webcam alternative are in [`docs/pi-camera.md`](pi-camera.md).

**Out of scope on the Pi reference hardware path** (do not ship in M5 ports either, unless an ADR explicitly raises the v1 surface):

- I²C / SPI / UART / PWM. Future motion primitives may need these, but they are not part of the v1 surface.
- Multi-camera setups. The reference is one camera. A multi-camera adapter is a separate `frame_source` design problem and is not blocking the Pi → Jetson port.
- External GPS / IMU / encoders. World state is shaped today around what perception provides; sensor fusion is a future ADR, not Step 4.

---

## 4. Supported model path (frozen)

The Pi reference path is:

```
PiCameraSource → YoloVision → WorldState → GemmaMissionControl → SafetyGate → PiHardwareController
```

Wired by `pi_closed_loop_demo.main()`:

| Stage | Reference adapter | Wired by |
|---|---|---|
| Camera | `PiCameraSource()` ([ADR-0009](decisions.md)) | construction in `main()` |
| Vision | `YoloVision(frame_source=cam)` via `make_vision_from_config(cfg, frame_source=cam)` when `FREEMOTION_VISION_BACKEND=yolo` ([ADR-0007](decisions.md)) | `freemotion.vision.make_vision_from_config` |
| World state | `WorldState()` (M3) | construction in `main()` |
| Mission control | `GemmaMissionControl()` via `make_mission_from_config(cfg)` when `FREEMOTION_MISSION_BACKEND=gemma` ([ADR-0008](decisions.md)) | `freemotion.mission_control.make_mission_from_config` |
| Loop primitive | `MissionLoop(...)` ([ADR-0010](decisions.md), hardened by [ADR-0011](decisions.md)) | construction in `main()` |
| Safety floor | `SafetyGate(inner, cfg.safety_default)` ([ADR-0006](decisions.md)) | construction in `main()` |
| Controller | `PiHardwareController` via `make_controller_from_config(cfg)` when `FREEMOTION_HARDWARE=pi` (M4) | `freemotion.hardware.make_controller_from_config` |

**Default model choices**:

- YOLO weights: `yolov8n.pt` (~6 MB, person class only, confidence 0.25). Override via `YoloVision(model=..., classes=..., confidence_threshold=...)`.
- Gemma weights: `google/gemma-2-2b-it`. Override via `GemmaMissionControl(model=...)`.

**Fallback and offline behavior** (every layer fails offline, never crashes):

| Layer | Missing dep / load failure | Mid-run failure | Demo behavior at boot |
|---|---|---|---|
| Camera | `picamera2` not installed → `available=False` | per-call `None` increments `cam.capture_failures`; source stays available; full disconnect requires service restart | exit code `2` if not available |
| Vision | `ultralytics` not installed → `YoloVision.available=False` | per-call empty `VisionResult`; loop sees no detections | exit code `3` if not available |
| Mission | `transformers` not installed → `GemmaMissionControl.available=False` | adapter swallows raises; returns idle `MissionDecision`; loop counts `mission_failures` and dispatches no MOVE | warn-and-continue (no exit code) — an offline mission yields a perception-blind but safe loop |
| Hardware | `RPi.GPIO` not installed → `PiHardwareController.available=False` | controller refuses `arm`/`move`; `stop` is no-op; agent stays alive | warn — falls back to mock |

The fallback chain is **intentionally asymmetric**: camera and vision are required for a meaningful closed loop (the demo refuses to start without them), mission control and hardware are not (a degraded device is preferable to an off device). The asymmetry is recorded in [ADR-0010](decisions.md) (`/mission_start` is refused in `dry_run` for the same reason).

---

## 5. Environment variables (frozen)

Every variable below maps to a real code path. No undocumented variable is required. Values that are not set fall back to documented defaults; bad values warn and fall back without crashing.

### Required for the closed-loop demo

| Variable | Purpose | Read by |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather. **Without this, `Config.from_env()` raises `SystemExit`.** | [`freemotion/config/config.py`](../freemotion/config/config.py) |

### Strongly recommended

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_ALLOWED_CHAT_IDS` | empty (open to all) | CSV of chat IDs the bot will respond to. An empty value means the bot accepts every chat — the agent logs a loud warning. |
| `FREEMOTION_HARDWARE` | `host` | Set to `pi` to wire `PiHardwareController`. Anything else falls back to mock. |
| `FREEMOTION_SAFETY_DEFAULT` | `dry_run` | Pi reference uses `bench` for the indicator-LED bench rig. **Stay on `dry_run` until wiring is verified.** |

### Optional — backend selection

| Variable | Default | Values | Effect |
|---|---|---|---|
| `FREEMOTION_VISION_BACKEND` | `mock` | `mock`, `yolo` | `yolo` lazy-imports `ultralytics` and wires `YoloVision`. The closed-loop demo passes `frame_source=PiCameraSource()` through the factory. |
| `FREEMOTION_MISSION_BACKEND` | `mock` | `mock`, `gemma` | `gemma` lazy-imports `transformers` and wires `GemmaMissionControl`. |

### Optional — pin overrides and friendly metadata

| Variable | Default | Purpose |
|---|---|---|
| `FREEMOTION_DEVICE_ID` | `socket.gethostname()` | Friendly device name in `/status`. |
| `FREEMOTION_PI_ARMED_PIN` | `27` (BCM) | Override the armed indicator pin. |
| `FREEMOTION_PI_MOVING_PIN` | `22` (BCM) | Override the moving indicator pin. |
| `FREEMOTION_LED_PIN` | unset | Used only by `pipe_check`; not used by the closed-loop reference. |
| `FREEMOTION_FEATURES` | empty | CSV of feature names surfaced under `/capabilities` `features`. |
| `FREEMOTION_DENIED_COMMANDS` | empty | CSV of wire command names refused at the router with `denied_by_policy`. `stop` is exempt unconditionally. |

### Optional — closed-loop demo only (read in `pi_closed_loop_demo.py`, not in `Config`)

| Variable | Default | Purpose |
|---|---|---|
| `FREEMOTION_LOG_LEVEL` | `INFO` | Python logging level for the demo. Also accepted as `--log-level`. |
| `FREEMOTION_MISSION_TICK_INTERVAL_S` | `1.0` | Seconds between mission-loop ticks. Higher reduces CPU on Pi 3. Also accepted as `--tick-interval`. |
| `FREEMOTION_DEFAULT_INTENT` | `follow person` | Intent string used when `/mission_start` is sent without arguments. Also accepted as `--default-intent`. |

### Tuning knobs (constructor-only, not env-driven)

These are tuning parameters for the v1 contract, not operational knobs. To change them in the field, fork the demo or wrap `MissionLoop` directly.

| Knob | Default | Constructor arg |
|---|---|---|
| stale-world timeout | `5.0` s | `MissionLoop(stale_world_timeout_s=...)` |
| degraded threshold | `5` consecutive | `MissionLoop(degraded_threshold=...)` |
| join timeout (hung-tick) | `2.0` s | `MissionLoop(join_timeout_s=...)` |

---

## 6. Safety contract (frozen)

The Pi reference path provides the following safety guarantees. Every one is covered by tests in `tests/`.

### Hardware-level (M4 + Step 3)

1. **Default mode is `dry_run`.** Both `Config.from_env` and the protocol default to `dry_run`. Actuation requires an explicit operator decision (`FREEMOTION_SAFETY_DEFAULT=bench` and a process restart).
2. **`dry_run` cannot actuate `arm` or `move`.** Two-tier refusal:
   - Handler tier: `cmd.safety == dry_run` short-circuits with `unsafe_in_mode` (or, for `move`, returns `ok` with a `dry_run: would move (...)` message and no controller call).
   - SafetyGate tier ([ADR-0006](decisions.md)): refuses `arm` / `move` when `cfg.safety_default == dry_run` regardless of `cmd.safety`. **Device default is the floor**, not the ceiling.
3. **`bench` allows the bench-safe primitive only.** Today that means GPIO output to indicator pins. The Pi controller does not expose motor primitives — that's a deliberate M5+ boundary.
4. **`stop` always passes through.** Exempt from the deny list ([ADR-0004](decisions.md)); exempt from the SafetyGate ([ADR-0006](decisions.md)); the handler does not gate on `cmd.safety`. `stop()` does not acquire the controller lock so it succeeds mid-`move()`.
5. **Hardware unavailable returns a protocol-shaped reply.** Missing `RPi.GPIO`, failed setup, runtime GPIO errors all return `unsafe_in_mode` from the handler. The agent loop never crashes on hardware faults.

### Loop-level (Step 3, [ADR-0011](decisions.md))

6. **`mission_start` is refused in `dry_run`.** A perception-blind loop on real hardware that produces zero actuation wastes camera cycles and confuses operators reading `/status`. Per [ADR-0010](decisions.md), the cleaner contract is no loop in `dry_run`.
7. **The loop dispatches MOVE and only MOVE.** `ARM`, `DISARM`, `STOP`, `STATUS`, `CAPABILITIES`, `PING`, `MISSION_START` are not loop-dispatchable. An LLM hallucination cannot arm, disarm, or kill the loop.
8. **Stale-world refusal.** `mission.plan()` may emit MOVE; the loop **skips** the dispatch when `world_age_s` exceeds `stale_world_timeout_s` (default 5 s). The skip increments `stale_world_skips` (a separate signal class from `dispatch_failures`) and clears automatically on the next non-empty scene. Gemma cannot act on a 30-second-old world.
9. **Degraded state is a visibility signal, not a self-stop.** Per-stage consecutive failure counters flip a `degraded` flag with a human-readable reason; the operator decides whether to `/stop`. Auto-stopping the loop on degraded would have made false-positive degradation destructive.
10. **Hung-tick handling.** When `mission.plan()` blocks past `join_timeout_s`, `stop()` preserves `_thread` so a fresh `start()` refuses (no zombie thread leak) and clears `_intent` so `/status` reads idle. Hardware controller is stopped first, **before** loop-stop; the pins are LOW even when the worker is hung.
11. **`/stop` composes loop-stop with controller-stop.** Loop-first ordering means no in-flight tick can dispatch MOVE *after* the controller has been stopped. `make_stop_handler` swallows callback exceptions, so `/stop` always acks even if the loop is wedged or a GPIO write fails.
12. **`graceful_shutdown` ordering on SIGTERM.** mission_loop.stop → controller.stop → cam.close → inner.cleanup. Each step swallows its own exceptions; a single broken layer cannot block the rest of the teardown.

The full operator-facing failure reference is [`docs/pi-failure-modes.md`](pi-failure-modes.md). The hardware-level safety reference is [`docs/pi-hardware.md`](pi-hardware.md).

---

## 7. Status contract (frozen)

`/status` returns a single `Reply` envelope on the Pi reference path. The shape is stable; new fields are additive (per [ADR-0002](decisions.md), additive fields don't bump the protocol version).

### Top-level

```json
{
  "v": 0,
  "sender": "<device_id>",
  "state": "idle | armed | moving | error",
  "ok": true,
  "telemetry": { ... },
  "message": "<human-readable summary, multi-line>",
  "correlation_id": "<echoed>",
  "ts": "<ISO 8601 UTC>"
}
```

### `telemetry.controller`

```json
{
  "armed": false,
  "position": [0.0, 0.0, 0.0],
  "last_move_ts": null,
  "armed_pin": 27,
  "moving_pin": 22,
  "connected": true,
  "safety": "dry_run | bench | live",
  "available": true
}
```

### `telemetry.mission_loop` (when `mission_loop` is wired)

```json
{
  "running": false,
  "stop_requested": false,
  "intent": null,
  "tick_count": 0,
  "vision_failures": 0,
  "mission_failures": 0,
  "dispatch_failures": 0,
  "consecutive_vision_failures": 0,
  "consecutive_mission_failures": 0,
  "consecutive_dispatch_failures": 0,
  "stale_world_skips": 0,
  "degraded": false,
  "degraded_reason": "",
  "world_stale": false,
  "world_age_s": null,
  "stale_world_timeout_s": 5.0,
  "last_decision": null,
  "last_dispatched": null,
  "last_dispatch_ok": null,
  "last_dispatch_message": "",
  "started_at": null,
  "uptime_s": 0
}
```

### Human-readable summary format

The `message` field includes one line per subsystem, derived from the same telemetry. The mission-loop line is built by `_format_mission_loop_line` (see [`freemotion/agent/builtins.py`](../freemotion/agent/builtins.py)):

```
mission: <running|idle> [DEGRADED: <reason>] [stale world: <age>s] [(intent='<intent>')]
```

The `[DEGRADED: ...]` badge persists across `/stop` so a post-mortem `/status` after a stop still surfaces the last-known reason. The `[stale world: ...]` badge is suppressed when the loop is idle (a stopped loop is not actively stale; the badge would be misleading).

### Stale-world semantics

`world_age_s` is `now − max(last_perception_ts, started_at)`. `world_stale` is `True` iff `world_age_s > stale_world_timeout_s`. While `world_stale=True` and `running=True`, the loop refuses to dispatch MOVE; `stale_world_skips` increments on each refusal. Recovery is automatic on the next non-empty scene.

### Degraded semantics

`degraded` is `True` iff at least one of `consecutive_vision_failures`, `consecutive_mission_failures`, or `consecutive_dispatch_failures` exceeds `degraded_threshold` (default 5). `degraded_reason` is a `;`-joined string of the responsible stages (e.g. `"vision_failures>=5 (7); dispatch_failures>=5 (12)"`). The flag clears automatically when every stage drops below threshold — recovery is observable, not just degradation.

---

## 8. Failure model (frozen)

Every environmental failure the runtime is contracted to survive is documented in [`docs/pi-failure-modes.md`](pi-failure-modes.md). Step 4 does not redefine them; it locks them as part of the reference architecture so a contributor knows they're not subject to change without an ADR.

| Failure | Behavior summary | Where documented |
|---|---|---|
| Camera unplugged mid-mission | `vision_failures` climbs; `[DEGRADED]` after 5; `world_stale=True` after timeout; MOVE skipped | [pi-failure-modes.md §1](pi-failure-modes.md) |
| Camera returns bad frames | `cam.capture_failures` increments; transient or persistent paths covered | [pi-failure-modes.md §2](pi-failure-modes.md) |
| YOLO offline mid-loop | `vision_failures` if raising; empty scenes if silent — both covered, both visible in `/status` | [pi-failure-modes.md §3](pi-failure-modes.md) |
| Gemma errors mid-tick | `mission_failures` climbs; idle decision; no MOVE dispatched | [pi-failure-modes.md §4](pi-failure-modes.md) |
| Gemma hangs in `transformers.generate()` | `/stop` returns within `join_timeout_s`; pins LOW; loop reads idle; restart-after-hang is the recovery path | [pi-failure-modes.md §4](pi-failure-modes.md), [ADR-0011](decisions.md) |
| OOM / resource pressure | per-stage `try/except` catches everything including `MemoryError`; systemd `Restart=on-failure` is the outer net | [pi-failure-modes.md §5](pi-failure-modes.md) |
| SIGTERM during a mission | `app.run_polling()` returns; `graceful_shutdown` runs in order; pins LOW; service stays down until restarted | [pi-failure-modes.md §6](pi-failure-modes.md) |
| Telegram / network drop | python-telegram-bot retries; loop is independent and keeps ticking | [pi-failure-modes.md §7](pi-failure-modes.md) |
| Stale world / empty room | `world_stale=True` after timeout; MOVE skipped; resumes the moment perception comes back | [pi-failure-modes.md §8](pi-failure-modes.md) |
| Repeated dispatch refusal | `dispatch_failures` climbs; `[DEGRADED]` after 5; loop stays alive so a config fix clears the streak automatically | [pi-failure-modes.md §9](pi-failure-modes.md) |
| Restart and recovery | `/mission_start` reaps a dead orphan thread; counters reset on every `start()`; clean-stop recovery is supported | [pi-failure-modes.md §10](pi-failure-modes.md) |
| Vision contract violation | non-`VisionResult` return counts as `vision_failures` rather than crashing | [pi-failure-modes.md §3](pi-failure-modes.md) |

The runbook ("what to do when it goes wrong") is in [pi-failure-modes.md → Operator runbook](pi-failure-modes.md#operator-runbook--what-to-do-when-it-goes-wrong).

---

## 9. Documentation alignment

The following documents all tell the same story about the Pi reference path. If a contributor finds drift between any pair, the **closed-loop demo source** (`examples/pi_closed_loop_demo/pi_closed_loop_demo.py`) and **this page** are the source of truth, in that order.

| Doc | Role |
|---|---|
| [README.md](../README.md) | Entry point. One-paragraph claim about what a Pi Free Motion device is, plus a pointer to this page and the demo. |
| [GETTING_STARTED.md](../GETTING_STARTED.md) | Four paths: laptop demo (Path A), bench rig (Path B), full closed loop (Path C), benchmark verification (Path D). Step-by-step from clone to passing benchmark. |
| [docs/pi-reference.md](pi-reference.md) | **This document.** The 10-point lock — command surface, hardware path, model path, env contract, safety contract, status contract, failure model, alignment, M5 port target. |
| [docs/pi-benchmark.md](pi-benchmark.md) | The frozen `pi_follow_bench` protocol: 10-step sequence, success criteria, JSON artifact schema. Step 5's lock; the **execution proof** for this page's contract. |
| [docs/pi-closed-loop.md](pi-closed-loop.md) | The architecture-level reference — how the components compose, the loop body in pseudocode, the supported command table, the loop-level failure handling. Step 2's lock; this page is its Step 4 superset. |
| [docs/pi-failure-modes.md](pi-failure-modes.md) | The environmental failure reference plus the operator runbook. Step 3's lock. |
| [docs/pi-hardware.md](pi-hardware.md) | The M4 hardware-only reference: controller, SafetyGate, bench flow. The "what's real on the Pi today" table. |
| [docs/pi-camera.md](pi-camera.md) | The Step 1 camera reference: `PiCameraSource`, the libcamera vs. USB-webcam decision, the failure model for the camera layer specifically. |
| [docs/pi-runtime.md](pi-runtime.md) | The contributor-facing reference for building **a Pi device on the runtime**, not specifically the closed-loop reference. The minimal-device recipe and the env-var table for `Config`. |
| [docs/pi-setup.md](pi-setup.md) | OS-level prep: flashing, SSH, virtualenv, secrets file. Independent of which demo you run. |
| [docs/models.md](models.md) | The vision + mission control swap path. The interfaces, the mocks, and the real adapters' install commands. |
| [examples/pi_follow_bench/README.md](../examples/pi_follow_bench/README.md) | The benchmark operator runbook. Install, run, view, interpret. The standard `jq` / `diff` patterns for comparing runs. |
| [ROADMAP.md](../ROADMAP.md) | Where Steps 4 and 5 live in the milestone story. Marks the Pi-first lockdown complete; M5 Phase 1 is Jetson with the contract this page locks and the benchmark on the next page. |
| [CHANGELOG.md](../CHANGELOG.md) | Per-step delta log. Steps 4 and 5 entries point back here. |
| [docs/decisions.md](decisions.md) | ADR ledger. ADR-0012 records the locking rationale; ADR-0013 records the benchmark rationale. |
| [docs/jetson-phase1.md](jetson-phase1.md) | The M5 Phase 1 bring-up plan — must-keep / allowed-to-differ / first target demo / acceptance gate. Mirrors §10 below for the operator. |
| [docs/jetson-mapping.md](jetson-mapping.md) | The M5 Phase 1 dependency / env-var / camera-path / model-runtime / unsupported-features mapping. The companion to `jetson-phase1.md`. |
| [docs/releases/v0.2.0.md](releases/v0.2.0.md) | The Pi-first lockdown release notes (`v0.2.0`). What is locked, what is still open, backward compatibility. |

---

## 10. M5 Jetson port target — same contract, different hardware

The Pi reference architecture is the **M5 baseline**. M5 Phase 1 (Jetson Nano) is allowed to differ only on the hardware-specific seams listed below. Everything else must remain bit-for-bit identical to the contract this page locks.

### Must remain identical on Jetson (and every future port)

| Surface | Why it must not drift |
|---|---|
| **Protocol** ([docs/protocol.md](protocol.md)) | A Jetson device is still a Free Motion device. The same OpenClaw client must drive both. Protocol drift breaks the federation. |
| **Command surface** (the 8 commands in §2) | Operators must not have to remember which commands are Pi vs. Jetson. Adding new commands requires a protocol bump per [ADR-0002](decisions.md). |
| **Loop dispatch scope (MOVE only)** | An LLM hallucination cannot arm or disarm the device, regardless of host. ([ADR-0010](decisions.md)) |
| **World state shape** ([ADR-0005](decisions.md)) | `target`, `current_state`, `confidence`, `last_seen`, `next_action`. New fields are additive; renames or removals require an ADR. |
| **Mission decision shape** ([ADR-0008](decisions.md)) | `next_command`, `args`, `reason`, `confidence`. Same as world state — additive only. |
| **Safety semantics** (the 12 contracts in §6) | `dry_run` is the floor. `stop` is unconditional. Loop-only-MOVE. Stale-world skip. Hung-tick handling. Every guarantee here must hold on Jetson. |
| **Status contract** (the telemetry shape in §7) | A Jetson `/status` must be parseable by the same client tooling that parses a Pi `/status`. New telemetry keys are additive; existing keys may not be removed or renamed. |
| **`/stop` ordering** | mission_loop.stop → controller.stop → cam.close → inner.cleanup. ([ADR-0011](decisions.md)) |
| **Failure model surface** (the 12 failures in §8) | A Jetson port that ignores e.g. stale-world skip is broken on this contract. Each failure must have an analog on the new platform. |

### Allowed to differ on Jetson (hardware-specific seams)

| Seam | What's free to change | Constraints |
|---|---|---|
| `HardwareController` adapter | New `JetsonHardwareController` class. Different GPIO library (Jetson.GPIO instead of RPi.GPIO), different pin map, possibly different bench primitives. | Must implement the existing `HardwareController` Protocol; `state()` must include the same keys plus any additive Jetson-only telemetry; `stop()` must remain unconditional and lock-free. |
| Camera adapter | New `JetsonCameraSource` class **or** the same `cv2.VideoCapture`-shaped lambda used for USB webcams. picamera2 is Pi-specific; Jetson has its own libcamera path or a USB CSI bridge. | Must be callable returning `np.ndarray` or `None`. Must support `close()` (idempotent). Must fail-offline at construction without crashing. |
| Hardware factory | `make_controller_from_config(cfg)` learns a new branch for `FREEMOTION_HARDWARE=jetson`. | Pi branch and host fallback must remain unchanged. |
| Vision/mission performance tuning | Larger YOLO weights (`yolov8s.pt` or larger) where the Jetson GPU can carry them; Gemma quantization choices; tick interval. | The `VisionBackend` and `MissionPolicy` interfaces don't change. Tuning is per-deployment, not per-platform. |
| systemd unit | New `freemotion-jetson-closed-loop-demo.service`. | Same Restart, EnvironmentFile, and graceful-shutdown ordering as the Pi unit. |
| OS prep | A new `docs/jetson-setup.md`. | Mirrors `docs/pi-setup.md` structure so the operator experience is parallel. |

### M5 Phase 1 acceptance criteria

A Jetson port is "done" when **all five** are true:

1. `examples/jetson_closed_loop_demo/` runs the canonical command set against real hardware.
2. Every contract in §6 holds — verifiable by running the existing test suite against a Jetson-mocked controller and a Jetson-mocked camera.
3. Every telemetry key in §7 is present in `/status`.
4. **A Jetson rig produces a `pi_follow_bench`-shaped artifact** — the named benchmark task from Step 5 ([`pi-benchmark.md`](pi-benchmark.md), [ADR-0013](decisions.md)) runs on Jetson with the same success criteria. Renaming the runner to `jetson_follow_bench` is allowed; the **schema, sequence, and criteria are not**. The benchmark is the operator-facing proof that the contract holds end-to-end on the new platform.
5. `docs/jetson-reference.md` exists, structured like this page, and explicitly references this page as the parent contract.

Anything beyond these criteria is additive and lives in a future ADR. Step 4 does not lock M5 Phase 2 (ESP32) or Phase 3 (Arduino) — those have their own constraint sets and will be defined when they ship.

---

## Move-to-M5 rule

You move to M5 only when **all 10 sections above are aligned in code, tests, and docs**, and Step 5 (the repeatable Pi benchmark demo) has shipped. Both gates are now satisfied: Step 4 is done; Step 5 shipped `pi_follow_bench` (ADR-0013, [`pi-benchmark.md`](pi-benchmark.md)). M5 Phase 1 (Jetson Nano) is the next milestone, gated on `pi_follow_bench` passing on a real Pi bench rig.

## Definition of done (Step 4)

A contributor can:

1. **Stand up the Pi reference path from docs alone.** Follow [GETTING_STARTED.md](../GETTING_STARTED.md) Path C, end up at a working `pi_closed_loop_demo`.
2. **Know exactly what is supported.** §2 (commands), §3 (hardware), §4 (models).
3. **Know exactly what is safe.** §6 (12 contracts) plus the runbook in [pi-failure-modes.md](pi-failure-modes.md).
4. **Know exactly what the closed loop is.** §4 (the chain) plus [pi-closed-loop.md](pi-closed-loop.md).
5. **Port the same contract to Jetson without guessing.** §10 (must-keep / allowed-to-differ).

When all five are obvious, Step 4 is done.

## Related

- [docs/pi-benchmark.md](pi-benchmark.md) — Step 5 lock (frozen `pi_follow_bench` protocol — sequence, criteria, artifact schema).
- [docs/pi-closed-loop.md](pi-closed-loop.md) — Step 2 lock (architecture + loop body + components).
- [docs/pi-failure-modes.md](pi-failure-modes.md) — Step 3 lock (environmental failures + runbook).
- [docs/pi-hardware.md](pi-hardware.md) — M4 lock (controller + safety gate + bench flow).
- [docs/pi-camera.md](pi-camera.md) — Step 1 lock (camera adapter).
- [docs/jetson-phase1.md](jetson-phase1.md) — M5 Phase 1 plan (must-keep / allowed-to-differ / first target / acceptance gate).
- [docs/jetson-mapping.md](jetson-mapping.md) — M5 Phase 1 dependency / env-var / camera / model / unsupported mapping.
- [docs/releases/v0.2.0.md](releases/v0.2.0.md) — the Pi-first lockdown release notes.
- [docs/decisions.md](decisions.md) — ADR ledger; ADR-0012 is the Step 4 lock rationale; ADR-0013 is the Step 5 benchmark rationale.
- [SAFETY.md](../SAFETY.md) — operator-side bench rules.
