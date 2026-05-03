# Getting started

Two paths. Pick the one that matches what you have on your desk.

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

## Path B — real Raspberry Pi bench rig (M4)

The first **real hardware** Free Motion device. The Pi runs the standard runtime; LED indicators (or opto-isolated relay inputs) reflect real GPIO state changes through `PiHardwareController`. Bench-safe by construction — no motors, no propellers, no actuated platform.

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

## Safety guarantees

The runtime makes four contracts about hardware actuation. Read [`docs/pi-hardware.md`](docs/pi-hardware.md) for the full version; the short form:

1. **`dry_run` cannot actuate.** Both the handler (`cmd.safety`) and the `SafetyGate` (`cfg.safety_default`) refuse `arm` and `move`. The default mode is `dry_run`. Stay there until your wiring is verified.
2. **`bench` allows the bench-safe primitive.** Today that's GPIO output to indicator pins. The Pi controller doesn't expose motor primitives; that's a deliberate M5+ boundary.
3. **`stop` always works.** Exempt from the deny list (ADR-0004) and from the `SafetyGate` (ADR-0006). Cannot be denied by `FREEMOTION_DENIED_COMMANDS`. Survives mid-`move()`.
4. **Hardware unavailable → protocol-shaped reply.** Missing `RPi.GPIO`, failed setup, runtime GPIO errors all return `unsafe_in_mode` from the handler, never crash the agent.

[`SAFETY.md`](SAFETY.md) is the operator-side complement. Read it before any code drives motors, ESCs, or props.

## Where to read next

- **Architecture, controller, gate, bench flow:** [`docs/pi-hardware.md`](docs/pi-hardware.md)
- **Building your own device on the runtime:** [`docs/pi-runtime.md`](docs/pi-runtime.md)
- **Wire format:** [`docs/protocol.md`](docs/protocol.md)
- **What ships when:** [`ROADMAP.md`](ROADMAP.md)
- **Why things are the way they are:** [`docs/decisions.md`](docs/decisions.md)
