# Getting started

Three paths. Pick the one that matches what you have on your desk.

> **Looking for the canonical Pi reference architecture?** Once you're past the laptop demo, [`docs/pi-reference.md`](docs/pi-reference.md) is the **single source of truth** for what a Pi Free Motion device is — the supported command surface, hardware path, model path, env-var contract, safety contract, status contract, failure model, and the M5 Jetson port target. This page gets you running; that page is the lock.

## Path A — laptop, no hardware (60 seconds)

The fastest "is this real?" check. Runs the M3 mission/vision/world loop end-to-end on mocks. No Telegram, no Pi, no model downloads.

```bash
git clone https://github.com/SpencerBrown1717/Free_Motion.git
cd Free_Motion
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -e .
python examples/local_sim_demo.py
```

Five ticks of `intent → vision → world → mission_control → router → hardware → world` print to stdout, with the wire envelope for every dispatched command. Same code path a real device runs; only the backends change.

Want a Telegram bot in the mix without hardware? Use [`examples/mock_drone/`](examples/mock_drone/) — same install, plus a bot token in `~/.config/freemotion.env`. Walkthrough in its [README](examples/mock_drone/README.md).

## Path B — Pi bench rig (M4 sub-path; controller + safety only)

For verifying the controller and SafetyGate **without** perception or mission control. Use this when you want to debug the GPIO and `/arm`/`/move`/`/stop` flow in isolation. Once it works, graduate to Path C.

### What you need

- A Raspberry Pi (3, 4, 5, or Zero 2 W) with Raspberry Pi OS Bookworm or later
- microSD card, reliable power, network access
- Two LEDs + two ~330 Ω resistors **or** two opto-isolated relay indicator inputs
- A Telegram bot token (from BotFather)

### Steps

1. **Flash the Pi and finish first boot.** Full walkthrough: [`docs/pi-setup.md`](docs/pi-setup.md).
2. **Clone and install on the Pi:**
   ```bash
   sudo apt update && sudo apt install -y git python3 python3-venv python3-pip
   git clone https://github.com/SpencerBrown1717/Free_Motion.git
   cd Free_Motion
   python3 -m venv .venv && source .venv/bin/activate
   pip install --upgrade pip
   pip install -e .
   pip install RPi.GPIO    # Pi-only
   ```
3. **Wire the bench rig.** Default pins are BCM 27 (`armed_pin`) and BCM 22 (`moving_pin`). LEDs in series with 330 Ω resistors to GND. **Do not drive motors from these pins.** Wiring detail: [`docs/pi-hardware.md`](docs/pi-hardware.md).
4. **Set the env vars.** Drop these into `~/.config/freemotion.env` (mode 600):
   ```ini
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token-here
   TELEGRAM_ALLOWED_CHAT_IDS=11111111
   FREEMOTION_DEVICE_ID=pi-bench-01
   FREEMOTION_HARDWARE=pi
   FREEMOTION_SAFETY_DEFAULT=bench
   ```
   Then load: `set -a && source ~/.config/freemotion.env && set +a`
5. **Run the demo:**
   ```bash
   python examples/pi_bench_demo/pi_bench_demo.py
   ```
6. **Verify the bench flow** end-to-end from Telegram:
   - `/capabilities` lists the seven commands
   - `/status` shows hardware-backed telemetry (`armed`, pins, position, `safety`)
   - `/arm` drives `armed_pin` HIGH (LED_armed ON)
   - `/move 1 0 0` pulses `moving_pin` HIGH for ~100 ms (LED_moving flashes), updates position
   - `/stop` drops both pins LOW
   - `/disarm` confirms idle

Full operator walkthrough (autostart, troubleshooting, exact replies for every command): [`examples/pi_bench_demo/README.md`](examples/pi_bench_demo/README.md).

## Path C — Pi reference architecture (the full closed loop)

**This is the canonical Pi path** ([`docs/pi-reference.md`](docs/pi-reference.md)). Telegram → Pi camera → YOLO → world state → Gemma → SafetyGate → Pi GPIO → `/status`. Background `MissionLoop` perceives, decides, and dispatches one bench-safe MOVE per tick. `/stop` is the unconditional master kill.

### What you need (in addition to Path B)

- The **Raspberry Pi Camera Module** (CSI ribbon). USB webcam works as an alternative — see [`docs/pi-camera.md`](docs/pi-camera.md).
- **Disk + RAM for the models:** ~6 MB for `yolov8n.pt`, ~5 GB for `gemma-2-2b-it` on first download. Pi 4 (4 GB+) or Pi 5 strongly recommended.

### Steps

1. **Finish Path B first.** The bench rig must work end-to-end before adding perception and mission control on top — it's much easier to debug GPIO without YOLO and Gemma in the loop.
2. **Install the model extras on the Pi:**
   ```bash
   pip install -e .[picam,yolo,gemma]
   ```
   This pulls `picamera2`, `ultralytics` + `torch`, and `transformers`. They're all heavy; on a Pi 4 expect ~5–10 minutes. They're optional extras precisely so the base install stays tiny.
3. **Add the model env vars** to `~/.config/freemotion.env`:
   ```ini
   FREEMOTION_VISION_BACKEND=yolo
   FREEMOTION_MISSION_BACKEND=gemma
   ```
   Reload: `set -a && source ~/.config/freemotion.env && set +a`.
4. **Run the closed-loop demo:**
   ```bash
   python examples/pi_closed_loop_demo/pi_closed_loop_demo.py
   ```
   The first run downloads the YOLO weights and the Gemma weights to the Hugging Face cache. Subsequent runs are instant.
5. **Verify the closed loop** from Telegram:
   - `/status` reports `mission: idle` (loop not yet running).
   - `/arm` lights the `armed` LED.
   - `/mission_start` reports `mission started: intent='follow person'`.
   - Stand in front of the camera. Within ~2 ticks, `/status` reports `mission: running ... last_dispatched: move` and the `moving` LED pulses.
   - `/stop` — both LEDs drop LOW. `/status` reports `mission: idle` again.

Full operator walkthrough (failure modes, telemetry shape, runbook): [`examples/pi_closed_loop_demo/README.md`](examples/pi_closed_loop_demo/README.md). The architectural lock with every contract spelled out: [`docs/pi-reference.md`](docs/pi-reference.md). The runbook for when something goes wrong: [`docs/pi-failure-modes.md`](docs/pi-failure-modes.md).

## Safety guarantees

The runtime makes twelve contracts about hardware actuation and the closed-loop runtime. Read [`docs/pi-reference.md`](docs/pi-reference.md) §6 for the full list; the short form:

1. **`dry_run` cannot actuate.** Both the handler (`cmd.safety`) and the `SafetyGate` (`cfg.safety_default`) refuse `arm` and `move`. The default mode is `dry_run`. Stay there until your wiring is verified.
2. **`bench` allows the bench-safe primitive.** Today that's GPIO output to indicator pins. The Pi controller doesn't expose motor primitives; that's a deliberate M5+ boundary.
3. **`stop` always works.** Exempt from the deny list (ADR-0004) and from the `SafetyGate` (ADR-0006). Cannot be denied by `FREEMOTION_DENIED_COMMANDS`. Composed with `mission_loop.stop()` first then controller pins LOW (ADR-0011). Survives mid-`move()` and mid-`mission.plan()`.
4. **Hardware unavailable → protocol-shaped reply.** Missing `RPi.GPIO`, failed setup, runtime GPIO errors all return `unsafe_in_mode` from the handler, never crash the agent.
5. **Loop never acts on stale perception.** When `world_age_s` exceeds `stale_world_timeout_s` (default 5 s), MOVE is skipped regardless of what the policy emits — Gemma cannot drive the device on a 30-second-old world.
6. **Loop only ever dispatches MOVE.** ARM, DISARM, STOP stay strictly operator-driven via Telegram — an LLM hallucination cannot arm or disarm the device.

[`SAFETY.md`](SAFETY.md) is the operator-side complement. Read it before any code drives motors, ESCs, or props.

## Where to read next

- **Pi reference architecture (the lock):** [`docs/pi-reference.md`](docs/pi-reference.md)
- **End-to-end closed-loop architecture:** [`docs/pi-closed-loop.md`](docs/pi-closed-loop.md)
- **Operator runbook for environmental failures:** [`docs/pi-failure-modes.md`](docs/pi-failure-modes.md)
- **Pi hardware controller, safety gate, bench flow:** [`docs/pi-hardware.md`](docs/pi-hardware.md)
- **Building your own device on the runtime:** [`docs/pi-runtime.md`](docs/pi-runtime.md)
- **Wire format:** [`docs/protocol.md`](docs/protocol.md)
- **What ships when:** [`ROADMAP.md`](ROADMAP.md)
- **Why things are the way they are:** [`docs/decisions.md`](docs/decisions.md)
