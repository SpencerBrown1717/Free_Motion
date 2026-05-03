# pi_bench_demo (M4 Phase 2)

The **first real hardware Free Motion device**. A Raspberry Pi runs the standard runtime (`Config.from_env` → `Router` → `Agent` → Telegram), wired through the new `PiHardwareController`. State transitions (`armed`, `moving`) are reflected on **real GPIO output pins** so `/status` carries hardware-backed telemetry, not in-memory bookkeeping.

This demo is **bench-safe by design**. The Pi controller drives only two output pins. Connect them to LEDs (or opto-isolated relay indicators). No motor drivers, no propellers, no actuated platform. Real actuation lands later, behind explicit safety modes. If you're tempted to wire a motor to these pins, read [SAFETY.md](../../SAFETY.md) first.

## What the demo supports

The runtime registers exactly six commands plus `/ping`:

| Slash command | Effect |
|---|---|
| `/ping` | round-trip liveness check |
| `/capabilities` | the device's full command set (used by OpenClaw to register devices) |
| `/status` | host info, safety mode, controller telemetry (`armed`, `position`, `pins`, `last_move_ts`, `connected`) |
| `/arm` | drives `armed_pin` HIGH; controller marks itself armed (refused in `dry_run`) |
| `/move x y z` | pulses `moving_pin` HIGH for ~100 ms then back LOW; updates internal position; refused if not armed (logged-only in `dry_run`) |
| `/stop` | unconditional — drops both pins LOW, marks idle. **Always succeeds**; cannot be denied by `FREEMOTION_DENIED_COMMANDS`. |
| `/disarm` | drives `armed_pin` LOW; controller marks itself idle |

JSON envelopes per [docs/protocol.md](../../docs/protocol.md) are accepted on the same channel; replies come back as serialized envelopes when the input was JSON.

## What you need

- Raspberry Pi 3, 4, or 5 (any model with GPIO works; Zero 2 W is fine).
- Raspberry Pi OS Bookworm or later.
- Python 3.10+.
- Two LEDs + two ~330 Ω resistors, **or** two opto-isolated indicator inputs.
- A Telegram bot token. See [docs/pi-setup.md §4](../../docs/pi-setup.md).

## 1. Wire the bench rig

The Pi controller defaults to **BCM 27** (`armed_pin`) and **BCM 22** (`moving_pin`). Override with `FREEMOTION_PI_ARMED_PIN` / `FREEMOTION_PI_MOVING_PIN` if you'd rather use other pins.

```text
BCM 27 (physical pin 13) ──[ 330 Ω ]── (anode) LED_armed (cathode) ── GND
BCM 22 (physical pin 15) ──[ 330 Ω ]── (anode) LED_moving (cathode) ── GND
```

Pi GPIO pins are 3.3 V. Anything you connect must be 3.3 V tolerant. Do not drive a motor, an ESC, or anything that can move from these pins.

## 2. Install

From the **repo root** on the Pi:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install RPi.GPIO
```

The `RPi.GPIO` install is the only Pi-only step. The `freemotion` package itself is hardware-free.

## 3. Configure

If you followed [docs/pi-setup.md](../../docs/pi-setup.md) §4, you already have `~/.config/freemotion.env` (locked to mode 600). If not, create it now:

```bash
mkdir -p ~/.config
install -m 600 /dev/null ~/.config/freemotion.env
```

Edit it:

```ini
# Required.
TELEGRAM_BOT_TOKEN=123456:ABC-your-token-here

# Lock the bot to specific chats. Leave blank for the first DM, then
# fill in the chat_id the bot echoes back and restart.
TELEGRAM_ALLOWED_CHAT_IDS=11111111

# Friendly name shown in /status output.
FREEMOTION_DEVICE_ID=pi-bench-01

# Pick the Pi controller. Anything else falls back to mock.
FREEMOTION_HARDWARE=pi

# Default is dry_run (safest). Set to bench so /arm and /move actuate.
# Stay on dry_run until your wiring is verified.
FREEMOTION_SAFETY_DEFAULT=bench

# Optional pin overrides. Defaults are 27 / 22.
# FREEMOTION_PI_ARMED_PIN=27
# FREEMOTION_PI_MOVING_PIN=22

# Optional per-command deny list (CSV of wire command names). Refused
# at the router with error.code = "denied_by_policy". `stop` cannot be
# denied — it is always honored.
# FREEMOTION_DENIED_COMMANDS=move
```

Load it into the current shell:

```bash
set -a && source ~/.config/freemotion.env && set +a
```

## 4. Run

```bash
python examples/pi_bench_demo/pi_bench_demo.py
```

You should see something like:

```text
2026-05-03 11:30:00 INFO freemotion.config: device id resolved to pi-bench-01
2026-05-03 11:30:00 INFO freemotion.agent: starting Telegram polling
```

Open Telegram, find your bot, and run through the tour below.

## 5. The tour

```text
You: /capabilities
Bot: capabilities: arm, capabilities, disarm, move, ping, status, stop

You: /status
Bot: device: pi-bench-01
     hardware: pi
     system: Linux ...
     safety: bench
     freemotion: 0.1.0
     uptime_s: 12
     armed: no

You: /arm
Bot: armed                     # LED_armed turns on (BCM 27 -> HIGH)

You: /status
Bot: ...
     armed: yes

You: /move 1 0 0
Bot: moved (1.0, 0.0, 0.0)     # LED_moving flashes briefly

You: /status
Bot: ...                       # telemetry now shows position [1.0, 0.0, 0.0]
                               # and last_move_ts is set

You: /stop
Bot: stopped                   # both LEDs OFF, controller idle

You: /disarm
Bot: disarmed                  # LED_armed already off, no-op on the wire
```

A few important behaviors to verify on the bench:

- **`/move` without `/arm`** → reply: `unsafe_in_mode`, no LED pulse. The Pi controller refuses the call.
- **`/arm` while `safety=dry_run`** → reply: `unsafe_in_mode`, **no GPIO write**. The handler refuses before touching the controller.
- **`/move` while `safety=dry_run`** → reply: `dry_run: would move (...)`, **no GPIO write**. Logged-only by design.
- **`/stop` while `arm` / `move` are denied** → still works. Stop is exempt from `FREEMOTION_DENIED_COMMANDS` (per ADR-0004 in [docs/decisions.md](../../docs/decisions.md)).
- **Pull the `RPi.GPIO` rug** (e.g. uninstall it before launch) → demo still starts; the controller logs `RPi.GPIO unavailable; PiHardwareController is offline` and `/status` reports `connected: false`. `/arm` and `/move` return refusals; `/stop` still acks.

If any of the above doesn't hold on your bench, file an issue — that's the contract.

## 6. Optional: autostart on the Pi

A user-level systemd unit ships next to this README:

```bash
mkdir -p ~/.config/systemd/user
cp examples/pi_bench_demo/systemd/freemotion-pi-bench-demo.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now freemotion-pi-bench-demo
sudo loginctl enable-linger "$USER"   # so it runs across logout
```

Tail logs:

```bash
journalctl --user -u freemotion-pi-bench-demo -f
```

The unit assumes the repo lives at `~/src/Free_Motion` and the venv at `~/src/Free_Motion/.venv`. Edit the file if your paths differ.

## Safety

This demo intentionally cannot move anything that could hurt you. Once you go beyond bench LEDs, **read [SAFETY.md](../../SAFETY.md)** before any code can drive motors, ESCs, or props. The default `FREEMOTION_SAFETY_DEFAULT=dry_run` exists for a reason; flip it to `bench` only after confirming wiring.

## Troubleshooting

- **`TELEGRAM_BOT_TOKEN is not set`** — you didn't `source ~/.config/freemotion.env` in this shell.
- **Bot stays silent** — check the foreground log (or `journalctl --user -u freemotion-pi-bench-demo -f`). Most often a typo in the token.
- **`unauthorized chat`** — your chat id isn't in `TELEGRAM_ALLOWED_CHAT_IDS`. Temporarily clear it, DM the bot, copy the chat id from the echo, paste it back, restart.
- **`/arm` always refused** — check `FREEMOTION_SAFETY_DEFAULT`. `dry_run` refuses `arm` by design.
- **`/move` says `not armed`** — call `/arm` first.
- **`/move` says `dry_run: would move (...)`** — set `FREEMOTION_SAFETY_DEFAULT=bench` (after verifying wiring).
- **`/status` shows `connected: false`** — `RPi.GPIO` failed to import or `setup()` raised. Check `pip show RPi.GPIO` and that you're not running as a non-`gpio`-group user.
- **LEDs stuck ON after Ctrl-C** — the agent's `finally` calls `controller.cleanup()`. If the process was killed (`kill -9`), pins keep their last state until the next process resets them. `systemctl --user restart freemotion-pi-bench-demo` clears it.

## How this differs from the other examples

| Example | Hardware | Use it when |
|---|---|---|
| `examples/pipe_check/` | Pi GPIO LED only | Smallest possible end-to-end check on a Pi (M0). |
| `examples/mock_drone/` | None (in-memory mock) | You want the full Free Motion command set on a laptop. |
| `examples/local_sim_demo.py` | None | You want to see the M3 mission/vision/world loop run end-to-end on mocks. |
| **`examples/pi_bench_demo/`** | **Pi GPIO via `PiHardwareController`** | **You're proving the runtime on real hardware (M4).** |
