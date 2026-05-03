# pipe_check (M0)

The smallest end-to-end Free Motion demo and the **reference adopter** of the runtime. It proves the **OpenClaw → Telegram → device** pipe works on the machine in front of you, with no motion, no vision, and no models.

You DM a Telegram bot. Your Pi (or laptop) replies through the [v0 protocol](../../docs/protocol.md), routed by `freemotion.agent`. Optionally an LED blinks.

The example contributes only an LED adapter and `led_on` / `led_off` handlers; everything else (`ping`, `stop`, `status`, `capabilities`, message parsing, auth, Telegram I/O) comes from `freemotion/`.

## What it does

- `/ping` → `pong`
- `/status` → hostname, OS, architecture, GPIO state, current safety mode
- `/capabilities` → list of commands this device implements
- `/led on` / `/led off` → drives a GPIO pin on a Pi (no-op elsewhere; refused in `dry_run`)
- `/disarm`, `/stop` → set state to `idle`
- Any other text starting with `/` is parsed as slash sugar
- Any text starting with `{` is parsed as a JSON [command envelope](../../docs/protocol.md#command-envelope)
- Anything else → echoed back with your `chat_id` (use this once to find your id)

## 1. Make a Telegram bot

1. DM **@BotFather** on Telegram and send `/newbot`.
2. Pick a name and a username.
3. Save the bot token BotFather gives you. Treat it like a password.

## 2. Configure secrets on the device

If you followed [docs/pi-setup.md](../../docs/pi-setup.md) §4, you already have `~/.config/freemotion.env`. If not, create it now:

```bash
mkdir -p ~/.config
install -m 600 /dev/null ~/.config/freemotion.env
```

Edit the file:

```ini
TELEGRAM_BOT_TOKEN=123456:ABC-your-token-here

# Optional, recommended after the first message:
# TELEGRAM_ALLOWED_CHAT_IDS=11111111,22222222

# Optional; only used on a Pi with an LED wired to a BCM pin:
# FREEMOTION_LED_PIN=17

# Optional; defaults to hostname:
# FREEMOTION_DEVICE_ID=pi-bench-01

# Optional; default is dry_run (safest). Set to "bench" so /led actuates:
# FREEMOTION_SAFETY_DEFAULT=bench
```

Load it into the current shell:

```bash
set -a && source ~/.config/freemotion.env && set +a
```

## 3. Install

From the **repo root** (one editable install handles both the `freemotion` package and pipe_check's deps):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
# On a Pi only, if you want the LED:
pip install RPi.GPIO
```

## 4. Run

```bash
python examples/pipe_check/pipe_check.py
```

Then in Telegram, find your bot and send `/ping`. You should get back `pong` within a second or two. Send anything not-slash-not-JSON and the bot will echo it along with your `chat_id` — copy that id into `TELEGRAM_ALLOWED_CHAT_IDS` and restart the script to lock the bot down to just you.

To exercise the JSON path, paste this whole line as a single Telegram message (the device replies with a JSON envelope):

```json
{"v":0,"id":"00000000-0000-0000-0000-000000000001","ts":"2026-01-01T00:00:00Z","from":"manual-test","cmd":"ping","args":{},"safety":"dry_run"}
```

## 5. Optional: autostart on the Pi

A user-level systemd unit is provided so the bot comes up after reboot.

```bash
mkdir -p ~/.config/systemd/user
cp examples/pipe_check/systemd/freemotion-pipe-check.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now freemotion-pipe-check
# So it runs even when you are not logged in:
sudo loginctl enable-linger "$USER"
```

Logs:

```bash
journalctl --user -u freemotion-pipe-check -f
```

The unit assumes the repo lives at `~/src/Free_Motion` and the venv at `~/src/Free_Motion/.venv`. Edit the service file if your paths differ.

## Wiring the LED (optional)

Any 3.3 V-tolerant LED + ~330 Ω resistor between a BCM GPIO pin and ground works. Default pin in the docs is **BCM 17** (physical pin 11). Set `FREEMOTION_LED_PIN` to whatever pin you used. Also set `FREEMOTION_SAFETY_DEFAULT=bench` (or send `safety: "bench"` in a JSON envelope), otherwise `/led on` is logged but not actuated, by design.

## Safety

This demo does not move anything that can hurt you. Once you go beyond echo-and-LED, read [SAFETY.md](../../SAFETY.md) before any code can drive motors, ESCs, or props.

## Troubleshooting

- **`TELEGRAM_BOT_TOKEN is not set`** — you didn't `source` the env file in this shell.
- **Bot stays silent** — check `journalctl --user -u freemotion-pipe-check -f` (or the foreground console) for connection errors. Most often it's a typo in the token.
- **`unauthorized`** — your `TELEGRAM_ALLOWED_CHAT_IDS` doesn't include your chat id. Temporarily unset it, send any message, copy the id from the echo, and add it.
- **`GPIO not available on this host`** — expected on macOS or a non-Pi Linux box; install `RPi.GPIO` only on a Pi.
- **`/led` says "dry_run: would turn led on"** — `safety_default` is `dry_run`. Set `FREEMOTION_SAFETY_DEFAULT=bench` to actuate.
