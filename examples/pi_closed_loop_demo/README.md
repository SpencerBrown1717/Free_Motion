# pi_closed_loop_demo (Step 2)

The first **end-to-end** Free Motion device. A Raspberry Pi runs the standard runtime, plus a background mission loop that ties every shipped component into one closed loop:

```
Telegram
  -> Agent + Router
       -> /mission_start ->  MissionLoop  (background thread)
                               PiCameraSource
                                 -> YoloVision
                                      -> WorldState
                                           -> GemmaMissionControl
                                                -> MissionDecision (MOVE)
                                                     -> Router.dispatch
                                                          -> SafetyGate
                                                               -> PiHardwareController
                                                                    -> GPIO pulse
       -> /status -> hardware state + mission_loop telemetry in one reply
       -> /stop   -> halts the loop AND drops both pins LOW (always)
```

This demo is **bench-safe by design** — same as `pi_bench_demo`. The only motion primitive is the bench `moving_pin` HIGH-pulse for ~100ms. The mission loop is **only** allowed to dispatch `MOVE` (per ADR-0010); ARM / DISARM / STOP stay operator-driven through Telegram so an LLM hallucination can never arm or disarm the device. Real actuation lands later, behind explicit safety modes. Read [SAFETY.md](../../SAFETY.md) before wiring anything beyond two LEDs.

## What the demo supports

| Slash command | Effect |
|---|---|
| `/ping` | round-trip liveness check |
| `/capabilities` | the device's full command set |
| `/status` | host info, safety mode, controller telemetry, **mission_loop telemetry** (running, intent, tick_count, last_decision, failure counters) |
| `/arm` | drives `armed_pin` HIGH; controller marks itself armed (refused in `dry_run`) |
| `/move x y z` | one-shot operator move; pulses `moving_pin`; refused if not armed |
| `/disarm` | drives `armed_pin` LOW; controller marks itself idle |
| `/mission_start [intent]` | starts the closed-loop mission with `intent` (default: `follow person`). Refused in `dry_run`. Idempotent: re-issuing while a mission is active is a no-op. |
| `/stop` | unconditional master kill. Halts the mission loop **and** drops both pins LOW. Cannot be denied by `FREEMOTION_DENIED_COMMANDS`. |

JSON envelopes per [docs/protocol.md](../../docs/protocol.md) are accepted on the same channel; replies come back as serialized envelopes when the input was JSON.

## What you need

- Raspberry Pi 4 or 5 (3 works but YOLO + Gemma is slow). Zero 2 W is too small for Gemma.
- Raspberry Pi OS Bookworm.
- Python 3.10+.
- Camera Module wired in (CSI ribbon) — see [docs/pi-camera.md](../../docs/pi-camera.md).
- Two LEDs + two ~330 Ω resistors, on the same pins as `pi_bench_demo` (default BCM 27 = `armed`, BCM 22 = `moving`).
- A Telegram bot token + your `chat_id`. See [docs/pi-setup.md](../../docs/pi-setup.md).
- Disk + RAM for the YOLO model (~6 MB) and the Gemma model (~5 GB on first download).

## Install

On the Pi:

```bash
git clone https://github.com/<you>/Free_Motion.git ~/src/Free_Motion
cd ~/src/Free_Motion
python -m venv .venv
source .venv/bin/activate
pip install -e .[yolo,gemma,picam]
```

The `[picam]` extra is Pi-only (`picamera2` is not on macOS). The `[gemma]` extra pulls `transformers` and `torch`. On a fresh Pi this can take ten minutes and several gigabytes — that's expected.

## Configure

Create `~/.config/freemotion.env`:

```ini
TELEGRAM_BOT_TOKEN=123456:abc
TELEGRAM_ALLOWED_CHAT_IDS=123456789       # your chat_id; comma-separated for multiple
FREEMOTION_DEVICE_ID=pi-closed-loop-01
FREEMOTION_HARDWARE=pi
FREEMOTION_SAFETY_DEFAULT=bench           # dry_run blocks all actuation; bench permits the bench primitive
FREEMOTION_VISION_BACKEND=yolo
FREEMOTION_MISSION_BACKEND=gemma
# Optional:
# FREEMOTION_PI_ARMED_PIN=27
# FREEMOTION_PI_MOVING_PIN=22
# FREEMOTION_DENIED_COMMANDS=move          # block move at the router; mission_loop's MOVE dispatches will all reply denied_by_policy
# FREEMOTION_MISSION_TICK_INTERVAL_S=1.0
# FREEMOTION_DEFAULT_INTENT=follow person
```

Set `FREEMOTION_SAFETY_DEFAULT=dry_run` for a dry-run test on the bench: every `MOVE` (operator or loop-dispatched) is logged with "would move" and no GPIO pin moves. `mission_start` itself is refused in `dry_run` (see ADR-0010) — you'll need `bench` to run the loop.

## Run from the command line

```bash
source .venv/bin/activate
python examples/pi_closed_loop_demo/pi_closed_loop_demo.py
```

Expected log lines:

```
SafetyGate active: safety=bench; arm/move permitted
pi_closed_loop_demo wired: vision=yolo mission=gemma tick=1.00s default_intent='follow person'
device_id=pi-closed-loop-01 safety_default=bench hardware=pi known=arm,capabilities,disarm,mission_start,move,ping,status,stop
starting long polling
```

Then in Telegram:

```
/ping                 -> pong
/status               -> ... mission: idle ...
/arm                  -> armed
/mission_start        -> mission started: intent='follow person'
/status               -> ... mission: running (intent='follow person'), tick_count: 7, ... last_dispatched: move ...
/stop                 -> stopped         (mission halts AND both pins drop LOW)
/disarm               -> disarmed
```

## Run as a systemd service

```bash
mkdir -p ~/.config/systemd/user
cp examples/pi_closed_loop_demo/systemd/freemotion-pi-closed-loop-demo.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now freemotion-pi-closed-loop-demo.service
journalctl --user -u freemotion-pi-closed-loop-demo.service -f
```

The unit reads `~/.config/freemotion.env`, runs out of `~/src/Free_Motion/.venv`, and restarts on failure with a 5-second backoff.

## What the loop actually does each tick

1. `PiCameraSource()` is invoked (callable) and returns one frame.
2. `YoloVision.scene()` runs inference; returns `Detection[]`.
3. The top-3 detections by confidence are written into `WorldState`. The highest-confidence detection wins as `target`.
4. `GemmaMissionControl.plan(intent=..., scene=..., world=...)` returns one `MissionDecision`.
5. If `decision.next_command == MOVE`, the loop dispatches a `Command` through the router with `safety = cfg.safety_default`. The router runs the deny-list, the move handler, the SafetyGate, and the controller in that order — same path as a Telegram-driven `/move`.
6. The loop sleeps on a stop event for `tick-interval` seconds, then ticks again.

If `vision.scene()`, `mission.plan()`, or `router.dispatch()` raises, the loop logs, increments the matching failure counter, and keeps going. The thread cannot crash. `/stop` interrupts the sleep immediately — you don't wait out the next tick.

## Verification checklist

A quick bench rig sanity test:

1. `/status` reports `mission: idle` (loop not running).
2. `/arm` lights the `armed` LED.
3. `/mission_start` reports `mission started: intent='follow person'`. The `armed` LED stays HIGH.
4. Stand in front of the camera. Within ~2 ticks, `/status` reports `mission: running ... last_dispatched: move`. The `moving` LED pulses on each successful MOVE.
5. Step out of frame. Gemma stops dispatching MOVE; `last_dispatch_message` reports the no-target idle reply. The `moving` LED stops pulsing.
6. `/stop` — both LEDs drop LOW. `/status` reports `mission: idle` and `armed: no`.
7. Repeat. The whole cycle should be repeatable without restarting the service.

If `vision.available` was False at startup, the demo refused to launch (exit code 3). If `picamera2` was missing, exit code 2. The agent never starts a perception-blind loop on real hardware.

## Failure modes (graceful)

| Failure | What you see |
|---|---|
| Camera dropped a frame | log: `mission_loop: vision.scene() raised: ...`; `vision_failures` ticks up; loop continues. |
| YOLO inference error | same; YoloVision swallows internally and returns no detections; loop continues. |
| Gemma OOM / inference error | log: `mission.plan() raised: ...`; `mission_failures` ticks up; idle decision; no MOVE dispatched. |
| `dry_run` mode | `mission_start` refused with `unsafe_in_mode`. Operator `/move` returns "would move". Mission loop never starts. |
| `denied_commands=move` | router refuses every MOVE (operator and loop-dispatched). `last_dispatch_ok=false`, `dispatch_failures` ticks up. The loop keeps running so a future config change can re-enable it without a restart. |
| LLM hallucinates `next_command=stop` (or arm/disarm) | log: `ignoring out-of-scope next_command='stop'`. The loop dispatches MOVE and only MOVE. |

## How this compares to the older demos

| Demo | Telegram | YOLO | Gemma | Pi camera | Pi GPIO | Background loop |
|---|---|---|---|---|---|---|
| `pipe_check` | yes | no | no | no | LED only | no |
| `mock_drone` | yes | no | no | no | mock | no |
| `local_sim_demo` | no (in-proc) | mock | mock | no | mock | yes (in-proc) |
| `pi_bench_demo` | yes | no | no | no | yes | no |
| `pi_camera_demo` | no | yes | no | yes | no | yes (in-proc) |
| **`pi_closed_loop_demo`** | **yes** | **yes** | **yes** | **yes** | **yes** | **yes (background thread)** |

This demo is the canonical reference for what a Free Motion device looks like end-to-end. Step 4 of the roadmap will lock that in as the official architecture before any Jetson work begins.

## Related docs

- [SAFETY.md](../../SAFETY.md) — bench rules, what counts as bench-safe.
- [docs/pi-hardware.md](../../docs/pi-hardware.md) — `PiHardwareController` + `SafetyGate`.
- [docs/pi-camera.md](../../docs/pi-camera.md) — `PiCameraSource` setup.
- [docs/pi-closed-loop.md](../../docs/pi-closed-loop.md) — canonical closed-loop architecture, env vars, failure model.
- [docs/decisions.md](../../docs/decisions.md) — ADR-0010 (`MissionLoop`), ADR-0009 (camera), ADR-0008 (Gemma), ADR-0007 (YOLO), ADR-0006 (SafetyGate).
