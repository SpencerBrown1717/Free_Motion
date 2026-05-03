# Pi hardware

How real Free Motion runs on a Raspberry Pi today. The canonical reference for **the hardware adapter, the safety floor, and the bench demo that wires them together** — i.e. the M4 sub-path of the larger Pi reference architecture.

> **Looking for the full Pi reference architecture?** This page covers M4 (controller + safety gate + bench demo). The full closed-loop reference — the canonical Pi path that adds camera, YOLO, world state, Gemma, and the mission loop on top — is locked in [`docs/pi-reference.md`](pi-reference.md). Read that first if you want the big picture.

For the OS-level prep (flashing, SSH, virtualenv, secrets file), see [`pi-setup.md`](pi-setup.md). For the runtime layers (Config / Router / Agent / handlers), see [`pi-runtime.md`](pi-runtime.md). This doc fills the gap between them: the hardware adapter, the safety floor, and the bench demo that wires them together.

## What's real on the Pi today (M4)

| Layer | What ships now | Status |
|---|---|---|
| Hardware adapter | [`PiHardwareController`](../freemotion/hardware/pi.py) — bench-safe GPIO controller (BCM 27 / 22 default) with lazy `RPi.GPIO` import | shipped (M4 Phase 1) |
| Hardware factory | `make_controller_from_config(cfg)` selects `PiHardwareController` for `FREEMOTION_HARDWARE=pi`, mock otherwise | shipped (M4 Phase 1) |
| Reference example | [`examples/pi_bench_demo/`](../examples/pi_bench_demo/) — full Pi runtime over `Config.from_env` → `make_controller_from_config` → `Router` → `Agent` → Telegram | shipped (M4 Phase 2) |
| Safety floor | [`SafetyGate`](../freemotion/hardware/safety.py) — `HardwareController` wrapper that enforces `cfg.safety_default`. `dry_run` refuses `arm`/`move`; `disarm`/`stop` always pass through. | shipped (M4 Phase 3) |
| Status path | `/status` carries hardware-backed telemetry (pin numbers, position, `last_move_ts`, `connected`, active `safety` mode) | shipped |
| Failure replies | Hardware errors are caught at the controller; handlers return protocol-shaped `unsafe_in_mode` / `internal` replies. Agent loop never crashes on hardware faults. | shipped |

## What's optional vs. mocked

| Component | Status | Where it lives |
|---|---|---|
| **YOLO vision** | Shipped (post-M4) behind `[yolo]` extra and `FREEMOTION_VISION_BACKEND=yolo`. Mock is the default for off-Pi development. | [`freemotion/vision/yolo.py`](../freemotion/vision/yolo.py), [`docs/models.md`](models.md), [ADR-0007](decisions.md) |
| **Gemma mission control** | Shipped (post-M4) behind `[gemma]` extra and `FREEMOTION_MISSION_BACKEND=gemma`. Mock is the default for off-Pi development. | [`freemotion/mission_control/gemma.py`](../freemotion/mission_control/gemma.py), [`docs/models.md`](models.md), [ADR-0008](decisions.md) |
| **Pi camera (live frames)** | Shipped (Step 1) behind `[picam]` extra. Wired into `YoloVision` via the `frame_source` seam. | [`freemotion/vision/picamera.py`](../freemotion/vision/picamera.py), [`docs/pi-camera.md`](pi-camera.md), [ADR-0009](decisions.md) |
| **Background closed loop** | Shipped (Step 2) — `MissionLoop` ties camera + YOLO + world + Gemma + dispatch into one tested primitive. Hardened in Step 3 (stale-world refusal, degraded summary, hung-tick handling). | [`freemotion/agent/mission_loop.py`](../freemotion/agent/mission_loop.py), [`docs/pi-closed-loop.md`](pi-closed-loop.md), [ADR-0010](decisions.md), [ADR-0011](decisions.md) |
| **Higher autonomy** | Out of scope by design ([ADR-0003](decisions.md)). The protocol returns one `MissionDecision` per call, not a plan tree. | n/a |
| **Other hardware** | M5: Jetson Nano → ESP32 → Arduino. Same `HardwareController` Protocol; new adapter classes per platform. The Pi reference path ([`docs/pi-reference.md`](pi-reference.md)) is the M5 baseline. | future |

The interfaces ship before the models. See [`models.md`](models.md) for the swap path and [ADR-0003](decisions.md#adr-0003--vision-and-mission-control-interfaces--mocks-now-real-models-behind-feature-flags-later--2026-05-03).

## Architecture

```text
Telegram message
    │
    ▼
Agent (auth, classify, log)
    │
    ▼
Router.dispatch(cmd)
    │  (denied_commands check; ADR-0004)
    ▼
make_arm_handler / make_move_handler / make_stop_handler / ...
    │  (cmd.safety check; per-command refusal)
    ▼
SafetyGate(controller, cfg.safety_default)         ← M4 Phase 3 floor
    │  (dry_run blocks arm/move; disarm/stop pass)
    ▼
PiHardwareController                                ← M4 Phase 1 GPIO
    │  (RPi.GPIO output to BCM 27 / 22)
    ▼
Bench rig: LEDs / opto-isolated indicators
```

Three independent layers can refuse a command:

1. **Router deny list** — refused as `denied_by_policy`, no handler invoked.
2. **Handler safety check** — `cmd.safety == dry_run` short-circuits with `unsafe_in_mode` (or, for `move`, returns ok with a `dry_run: would move (...)` message and no controller call).
3. **SafetyGate** — refuses `arm`/`move` when `cfg.safety_default == dry_run` regardless of `cmd.safety`. Returns `False` to the handler, which surfaces it as `unsafe_in_mode`.

`stop` bypasses all three. Per ADR-0004 it is exempt from the deny list; per ADR-0006 it bypasses the gate; the handler does not gate it on `cmd.safety`.

## The Pi controller

`PiHardwareController` (in [`freemotion/hardware/pi.py`](../freemotion/hardware/pi.py)) implements the [`HardwareController`](../freemotion/hardware/interface.py) Protocol against real GPIO. Two output pins:

| Pin | Default (BCM) | Behavior |
|---|---|---|
| `armed_pin` | 27 (physical 13) | HIGH while armed, LOW otherwise. Driven HIGH by `arm()`, LOW by `disarm()` and `stop()`. |
| `moving_pin` | 22 (physical 15) | Pulsed HIGH for `move_pulse_s` (default 100 ms) on each successful `move()`, then back to LOW. |

Override the defaults with `FREEMOTION_PI_ARMED_PIN` / `FREEMOTION_PI_MOVING_PIN`.

Design decisions worth knowing:

- **Bench-safe by construction.** GPIO output only — no PWM, no motor drivers, no propeller-spinning primitives. The "motion primitive" exists to prove the runtime path on real hardware. Real motion lands in M5+ behind explicit safety modes.
- **Lazy `RPi.GPIO` import.** The module imports cleanly on a non-Pi host (CI, dev laptop). If `RPi.GPIO` is missing or `setup()` raises, the controller stays offline: `available is False`, `arm`/`move` return `False`, `stop` is a no-op. The agent loop keeps running.
- **Hardware exceptions are absorbed.** Every GPIO write is wrapped; a failure logs and returns `False`. `stop()` deliberately does not acquire the controller lock — it must succeed even mid-`move()`. Setting `_armed = False` is atomic for a Python bool, and re-asserting LOW on the GPIO is idempotent.
- **`state()` reflects hardware-backed state.** Pin numbers, position accumulator, `last_move_ts`, `connected`. `/status` exposes all of it.
- **Test path runs CI-clean.** `tests/test_pi.py` (22 tests) covers the controller via an injected `FakeGPIO`. CI never needs `RPi.GPIO`.

## The safety floor

`SafetyGate` (in [`freemotion/hardware/safety.py`](../freemotion/hardware/safety.py)) is a `HardwareController` wrapper that fixes `cfg.safety_default` at construction time. It is the **single testable invariant** Phase 3 was built around: in `dry_run`, no path can call `arm()` or `move()` on the inner controller.

| Safety mode | `arm()` | `disarm()` | `stop()` | `move()` |
|---|---|---|---|---|
| `dry_run` | refused (gate) | passes through | passes through | refused (gate) |
| `bench`   | passes through | passes through | passes through | passes through |
| `live`    | passes through | passes through | passes through | passes through |

Why `disarm` and `stop` pass through in `dry_run`: depowering an actuator is always the safer direction. Refusing to drive a pin LOW offers no safety benefit and could leave the controller stuck armed.

The gate also stamps the active safety mode into `state()` under the `safety` key. `/status` surfaces it as `controller.safety`.

**Device default is the floor, not the ceiling.** A JSON command with `safety=bench` against a `FREEMOTION_SAFETY_DEFAULT=dry_run` device is rejected at the gate and surfaces as `unsafe_in_mode`. To actuate, change the device's default and restart. This inverts the historical permissive behavior on purpose: if an operator chose `dry_run`, an inbound command should not be able to override it. See [ADR-0006](decisions.md#adr-0006--safetygate-enforce-safetymode-at-the-hardware-boundary-dry_run-is-the-floor--2026-05-03).

## How to run the real bench flow

The full step-by-step (wiring photos, troubleshooting, systemd) lives at [`examples/pi_bench_demo/README.md`](../examples/pi_bench_demo/README.md). The minimal recipe here is enough to know what the moving parts look like before you read it.

### 1. Wire the rig

```text
BCM 27 (physical pin 13) ──[ 330 Ω ]── (anode) LED_armed (cathode) ── GND
BCM 22 (physical pin 15) ──[ 330 Ω ]── (anode) LED_moving (cathode) ── GND
```

Pi GPIO pins are 3.3 V. Anything you connect must be 3.3 V tolerant. **Do not drive a motor, an ESC, or anything that can move from these pins.**

### 2. Install on the Pi

```bash
git clone https://github.com/SpencerBrown1717/Free_Motion.git
cd Free_Motion
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install RPi.GPIO     # Pi-only; the freemotion package itself is hardware-free
```

### 3. Set the env vars

In `~/.config/freemotion.env` (mode 600):

```ini
# Required
TELEGRAM_BOT_TOKEN=123456:ABC-your-token-here

# Lock the bot to your chats. Leave blank for the first DM, then add the
# chat_id the bot echoes back and restart.
TELEGRAM_ALLOWED_CHAT_IDS=11111111

# Friendly name for /status output
FREEMOTION_DEVICE_ID=pi-bench-01

# Pick the Pi controller. Anything else falls back to mock.
FREEMOTION_HARDWARE=pi

# Floor for the SafetyGate. Default is dry_run (safest); set bench
# to actuate the indicator pins. Stay on dry_run until wiring is verified.
FREEMOTION_SAFETY_DEFAULT=bench

# Optional pin overrides (defaults shown)
# FREEMOTION_PI_ARMED_PIN=27
# FREEMOTION_PI_MOVING_PIN=22

# Optional per-command deny list. Refused at the router with
# error.code = "denied_by_policy". `stop` cannot be denied.
# FREEMOTION_DENIED_COMMANDS=move
```

Load it:

```bash
set -a && source ~/.config/freemotion.env && set +a
```

### 4. Run

```bash
python examples/pi_bench_demo/pi_bench_demo.py
```

### 5. The expected behavior, end-to-end

| You send | The runtime does | Telegram replies | Hardware effect |
|---|---|---|---|
| `/capabilities` | router lists registered handlers | `capabilities: arm, capabilities, disarm, move, ping, status, stop` | none |
| `/status` | `make_status_handler` reads `controller.state()` | host info + `armed: no`, `controller.safety: bench`, pins, position | none |
| `/arm` | handler accepts (safety=bench), gate passes through, controller drives `armed_pin` HIGH | `armed` | LED_armed ON |
| `/move 1 0 0` | handler accepts, gate passes through, controller pulses `moving_pin` HIGH for ~100 ms then LOW; position becomes `[1.0, 0.0, 0.0]` | `moved (1.0, 0.0, 0.0)` | LED_moving flashes |
| `/status` | `controller.state()` reflects new position and `last_move_ts` | telemetry shows `position: [1.0, 0.0, 0.0]`, `last_move_ts: <ts>` | none |
| `/stop` | router exempts `stop`; handler calls `controller.stop()`; both pins LOW | `stopped` | both LEDs OFF |
| `/disarm` | passes through gate; controller drives `armed_pin` LOW (no-op if already LOW) | `disarmed` | none |

## Safety guarantees

These are the contracts the runtime makes about hardware actuation. Every one is covered by tests in `tests/`.

1. **`dry_run` cannot actuate `arm` or `move`.** Both the handler (`cmd.safety` check) and `SafetyGate` (device-level floor) refuse. The inner controller is never called. Verified by `tests/test_safety_gate.py` with a call counter on a wrapped controller.
2. **`bench` allows the bench-safe primitive.** The current Pi controller exposes only GPIO output writes. `bench` permits `arm`/`move`; the controller refuses motion-driving primitives by **not implementing them**. When motor primitives land in M5+, expect a controller-level distinction between `bench` and `live`.
3. **`stop` always passes through.** Exempt from the router deny list (ADR-0004) and from the SafetyGate (ADR-0006). The handler does not gate on `cmd.safety`. `stop()` on the Pi controller does not acquire the controller lock, so it succeeds even mid-`move()`.
4. **Hardware unavailable → protocol-shaped reply.** If `RPi.GPIO` is missing or GPIO setup fails, the controller stays offline; `arm`/`move` return `False` and the handler surfaces it as `unsafe_in_mode`. The agent loop keeps running.
5. **Default safety is `dry_run`.** Both `Config.from_env` and the protocol default to `dry_run`. Actuation requires an explicit operator decision (`FREEMOTION_SAFETY_DEFAULT=bench` and a process restart).

[`SAFETY.md`](../SAFETY.md) is the operator-side of this story. Read it before any code can drive motors, ESCs, or props.

## Comparing the examples

| Example | Hardware | Use it when |
|---|---|---|
| [`examples/local_sim_demo.py`](../examples/local_sim_demo.py) | None (mocks) | You want to see the M3 mission/vision/world loop run end-to-end. No setup, no Telegram. |
| [`examples/mock_drone/`](../examples/mock_drone/) | None (`MockHardwareController`) | You want the full Free Motion command set on a laptop with a Telegram bot. |
| [`examples/pipe_check/`](../examples/pipe_check/) | Pi GPIO LED only | Smallest possible end-to-end check on a Pi (M0 reference). |
| [`examples/pi_bench_demo/`](../examples/pi_bench_demo/) | Pi GPIO via `PiHardwareController` + `SafetyGate` | You're debugging the M4 hardware path in isolation, **without** perception or mission control. |
| [`examples/pi_camera_demo/`](../examples/pi_camera_demo/) | Pi camera via `PiCameraSource` + `YoloVision` | You're debugging the perception path in isolation, **without** Telegram or hardware. |
| [`examples/pi_closed_loop_demo/`](../examples/pi_closed_loop_demo/) | **Pi GPIO + Pi camera + YOLO + Gemma + everything** | **You're running the canonical Pi reference architecture** ([`docs/pi-reference.md`](pi-reference.md)). |

## What comes next

The canonical Pi path is locked as of Step 4 ([`docs/pi-reference.md`](pi-reference.md)). The remaining gates before M5 (Jetson):

1. **Step 5 — One repeatable Pi benchmark demo.** A named bench task with a fixed command sequence and fixed success criteria. Becomes the gate for M5.
2. **M5 Phase 1 — Jetson Nano.** Same contract, different hardware. New `JetsonHardwareController` and a Jetson camera adapter; `HardwareController` Protocol unchanged. The "must-keep / allowed-to-differ" list is in [`docs/pi-reference.md`](pi-reference.md) §10.
3. **M5 Phase 2/3 — ESP32 / Arduino.** Bridge / coprocessor patterns. Lower priority than Jetson.

The interfaces stay frozen until at least one new platform ships and tells us what's missing.
