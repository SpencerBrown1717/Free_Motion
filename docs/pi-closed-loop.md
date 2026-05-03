# Pi closed loop (Step 2)

The canonical end-to-end Free Motion device on Raspberry Pi. This page is the source of truth for **how every shipped piece composes into one runtime** — `pi_bench_demo` is the bench-only runtime; `pi_camera_demo` is the perception-only runtime; **this** is the full thing.

> **Status.** Step 2 of the Pi-first lockdown plan. The reference example is `examples/pi_closed_loop_demo/`; the loop primitive is `freemotion.agent.MissionLoop`. The architecture is locked at the v1 surface described here. Step 3 (failure hardening) and Step 4 (reference architecture lock) build on top.

## Architecture at a glance

```
                   Telegram
                       |
                       v
            +----------+----------+
            |  freemotion.agent   |
            |        Agent        |   (main thread)
            +----------+----------+
                       |
                       v
            +----------+----------+
            |   freemotion.router |
            |       Router        |
            +----+-----+-----+----+
                 |     |     |
       +---------+     |     +-----------------+
       |               |                       |
       v               v                       v
   /ping etc.      /arm /move /disarm     /mission_start  ----+
                       |                       (starts ----+  |
                       v                        loop)      |  |
                  +----+----+                              |  |
                  |SafetyGate|                              |  |
                  +----+----+                              |  |
                       |                                    |  |
                       v                                    |  |
              +--------+--------+                           |  |
              |PiHardwareController|                        |  |
              +--------+--------+                           |  |
                       |  GPIO writes (armed, moving)       |  |
                       v                                    |  |
                       o                                    |  |
                                                            |  |
            +-------------------------------------+         |  |
            |  freemotion.agent.MissionLoop       |  <------+  |
            |  (background thread)                |            |
            |                                     |            |
            |  PiCameraSource()                   |            |
            |    -> YoloVision.scene()            |            |
            |        -> WorldState.see()          |            |
            |            -> mission.plan()        |            |
            |                -> dispatch(MOVE) ---+----+       |
            +-------------------------------------+    |       |
                                                       |       |
                              dispatched MOVE goes ----+       |
                              back through the same Router     |
                              (deny list -> SafetyGate -> Pi)  |
                                                               |
                              /stop ------------------------>--+
                                  (halts the loop AND drops
                                   both pins LOW, unconditionally)
```

`/status` reports the union of `controller.state()` and `MissionLoop.state()` in one reply, so a single Telegram message gives an operator the full closed-loop view.

## Components and where they're documented

| Component | Module | Doc |
|---|---|---|
| Telegram transport | `freemotion.agent.Agent` | [docs/protocol.md](protocol.md) |
| Command dispatch | `freemotion.router.Router` | [docs/architecture.md](architecture.md), ADR-0004 |
| Hardware (GPIO) | `freemotion.hardware.PiHardwareController` | [docs/pi-hardware.md](pi-hardware.md) |
| Safety floor | `freemotion.hardware.SafetyGate` | [docs/pi-hardware.md](pi-hardware.md), ADR-0006 |
| Camera | `freemotion.vision.PiCameraSource` | [docs/pi-camera.md](pi-camera.md), ADR-0009 |
| Vision (YOLO) | `freemotion.vision.YoloVision` | [docs/models.md](models.md), ADR-0007 |
| World state | `freemotion.world.WorldState` | [docs/architecture.md](architecture.md) |
| Mission control (Gemma) | `freemotion.mission_control.GemmaMissionControl` | [docs/models.md](models.md), ADR-0008 |
| Mission loop | `freemotion.agent.MissionLoop` | this doc, ADR-0010 |

## Supported command surface (v1)

| Slash | JSON `cmd` | Effect | Refused in `dry_run`? |
|---|---|---|---|
| `/ping` | `ping` | round-trip liveness | no |
| `/capabilities` | `capabilities` | list registered handlers | no |
| `/status` | `status` | host + safety + controller + mission_loop | no |
| `/arm` | `arm` | drive `armed_pin` HIGH | yes |
| `/disarm` | `disarm` | drive `armed_pin` LOW | no |
| `/move x y z` | `move` | one-shot operator move; pulse `moving_pin` | yes (logs "would move", no GPIO) |
| `/mission_start [intent]` | `mission_start` | start the background loop with `intent` (default: `follow person`) | yes |
| `/stop` | `stop` | unconditional master kill (loop + pins) | no — always passes through |

`/stop` halts the mission loop **first**, then drops both controller pins LOW. Both steps' exceptions are swallowed by `make_stop_handler` so `/stop` always acks. Even with `denied_commands=stop` configured, `Router` and `Config` both strip `stop` from the deny set — `/stop` is exempt by construction.

## The loop body (one tick)

```python
# pseudocode — the actual implementation is freemotion/agent/mission_loop.py:_tick
def tick():
    scene = vision.scene()                      # () -> VisionResult
    update_world(scene)                         # top-3 by conf, lowest first
    snapshot = world.snapshot()
    decision = mission.plan(intent=intent,      # MissionDecision
                            scene=scene,
                            world=snapshot)
    world.update(next_action=decision.next_command.value)
    if decision.next_command is CommandName.MOVE:
        cmd = Command(cmd=MOVE,
                      args=decision.args,
                      safety=cfg.safety_default,  # SafetyGate floor
                      sender="mission_loop")
        reply = router.dispatch(cmd)            # full deny-list + SafetyGate + handler
        record(reply)
    # decisions other than MOVE are logged and ignored (ADR-0010)
    sleep_on_stop_event(tick_interval_s)        # /stop interrupts immediately
```

Every layer is fail-isolated. `vision.scene()` raising increments `vision_failures` and the loop continues with an empty scene. `mission.plan()` raising increments `mission_failures` and the loop treats the tick as idle. `router.dispatch()` raising or returning `ok=false` increments `dispatch_failures`. The thread cannot crash.

## Environment variables

The closed-loop runtime reads everything `pi_bench_demo` reads, plus:

| Variable | Default | Effect |
|---|---|---|
| `FREEMOTION_VISION_BACKEND` | `mock` | `yolo` to wire `YoloVision` (lazy-imports `ultralytics`); `mock` for off-Pi development |
| `FREEMOTION_MISSION_BACKEND` | `mock` | `gemma` to wire `GemmaMissionControl` (lazy-imports `transformers`); `mock` to use the rule-based policy |
| `FREEMOTION_MISSION_TICK_INTERVAL_S` | `1.0` | seconds between mission-loop ticks; set higher to reduce CPU on Pi 3 |
| `FREEMOTION_DEFAULT_INTENT` | `follow person` | intent string used when `/mission_start` is sent without args |

`FREEMOTION_HARDWARE`, `FREEMOTION_PI_ARMED_PIN`, `FREEMOTION_PI_MOVING_PIN`, `FREEMOTION_SAFETY_DEFAULT`, `FREEMOTION_DENIED_COMMANDS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `FREEMOTION_DEVICE_ID` carry over unchanged from [docs/pi-runtime.md](pi-runtime.md).

## Failure model (graceful by design)

> **Step 3 update.** The table below lists the *structural* failure modes covered by the closed-loop wiring. The full *environmental* failure-mode reference — what happens when the camera is unplugged mid-mission, when YOLO drops, when Gemma hangs, when SIGTERM hits — lives in [docs/pi-failure-modes.md](pi-failure-modes.md). Step 3 added stale-world refusal, per-stage consecutive counters with a `degraded` summary, hung-tick handling, and `graceful_shutdown` ordering on top of what's described here. See [ADR-0011](decisions.md).


| Failure | What happens | What you see |
|---|---|---|
| `picamera2` not installed | source flips offline at `__init__`; demo refuses to start | exit code `2`, log: `PiCameraSource is offline.` |
| Camera busy / not wired | same as above | exit code `2` |
| `ultralytics` not installed | YoloVision flips offline at `__init__`; demo refuses to start | exit code `3`, log: `VisionBackend ... is offline.` |
| Camera drops a single frame | source returns `None`; YoloVision returns no detections; `cam.capture_failures` increments | log: `mission_loop: vision.scene() raised: ...`; loop continues |
| Gemma OOM / inference error | adapter returns idle decision; `mission_failures` increments | log: `mission.plan() raised: ...`; loop continues; no MOVE dispatched |
| Gemma adapter unavailable at boot | warning logged; loop runs but every `plan()` returns idle | nothing actuates; safe |
| `dry_run` mode | `mission_start` refused; `/move` (operator and loop-dispatched) reports "would move" | log: `mission_start refused in dry_run` |
| `denied_commands=move` | every MOVE (operator and loop-dispatched) is refused at the router with `denied_by_policy` | `last_dispatch_ok=false`; `dispatch_failures` increments; loop keeps running |
| LLM hallucinates `next_command=stop` (or arm/disarm) | logged and ignored; loop continues | log: `ignoring out-of-scope next_command='stop'` |
| `/stop` mid-tick | stop event interrupts the wait; thread exits within `join_timeout_s` (default 2s) | `mission_loop.is_running == False`; both pins LOW |
| Pi GPIO not available | `PiHardwareController.available == False`; arm/move return False | log: `PiHardwareController is offline`; `/status` reports `connected: false` |

## What `/status` looks like

A single `/status` carries:

```text
device: pi-closed-loop-01
hardware: pi
system: Linux 6.6.x
machine: aarch64
safety: bench
freemotion: 0.5.0
uptime_s: 1234
armed: yes
mission: running (intent='follow person')
```

Telemetry (JSON):

```json
{
  "device_id": "pi-closed-loop-01",
  "hardware": "pi",
  "safety_default": "bench",
  "uptime_s": 1234,
  "controller": {
    "armed": true,
    "position": [0.0, 0.0, 0.0],
    "pins": {"armed": "high", "moving": "low"},
    "last_move_ts": 1714754321.5,
    "connected": true,
    "safety": "bench"
  },
  "mission_loop": {
    "running": true,
    "intent": "follow person",
    "tick_count": 47,
    "vision_failures": 0,
    "mission_failures": 0,
    "dispatch_failures": 0,
    "last_decision": {
      "next_command": "move",
      "reason": "follow person centered",
      "confidence": 0.84
    },
    "last_dispatched": "move",
    "last_dispatch_ok": true,
    "last_dispatch_message": "moved {'x': 0.1, 'y': 0.0, 'z': 0.0}",
    "started_at": 1714754100.0,
    "uptime_s": 221
  }
}
```

## Operator runbook

1. Confirm the bench rig: two LEDs wired to the configured pins, camera ribbon seated, no actuated platform connected.
2. Set `FREEMOTION_SAFETY_DEFAULT=bench` (or `dry_run` for a smoke test where `mission_start` is intentionally refused).
3. Start the demo: `python examples/pi_closed_loop_demo/pi_closed_loop_demo.py` (or via the systemd unit).
4. Telegram: `/ping` → `pong`. `/status` → loop idle.
5. `/arm` → `armed` LED HIGH.
6. `/mission_start follow person` → loop running. Within ~2 ticks, `moving` LED begins pulsing on each MOVE the policy emits.
7. `/status` repeatedly to watch tick_count, last_decision, last_dispatched, and the failure counters.
8. `/stop` → both LEDs LOW, loop idle. `/disarm` → controller idle.

If the operator wants to abort instantly (not via Telegram): SIGINT or SIGTERM the process. The `try/finally` block in `main()` calls `mission_loop.stop()`, `cam.close()`, and the controller's `cleanup()` in order.

## Where to go next

- Step 3: real-world failure-mode hardening — the failures outside the runtime (camera unplugged mid-mission, network drop, OS suspend, GPIO oops) — currently in progress on the roadmap.
- Step 4: turn this doc into the canonical reference architecture before any Jetson / ESP32 / Arduino work begins.
- Step 5: a single named, repeatable benchmark task that becomes the gate for Jetson.

[docs/decisions.md](decisions.md) ADR-0010 records why the loop is its own object, why it only dispatches MOVE, why `mission_start` is refused in `dry_run`, and how the loop-vs-router circular wiring is resolved.
