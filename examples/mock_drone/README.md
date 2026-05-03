# mock_drone

A no-hardware Free Motion device. State lives entirely in memory. The wire format and command set are identical to a real device — only the `HardwareController` differs.

Use this when you want to:

- Contribute to Free Motion without owning a Pi.
- Demo the system on a laptop.
- Smoke-test a code change against the same protocol the real Pi sees.

## What it does

| Slash command | Effect |
|---|---|
| `/ping` | round-trip check |
| `/status` | host info + mock controller telemetry (armed, position, altitude, battery, connected) |
| `/capabilities` | the device's command set |
| `/arm` | mock controller transitions to `armed` (refused in `dry_run`, refused at low battery) |
| `/disarm` | back to `idle` |
| `/move x y z` | applies a relative offset to mock position; drains battery; refused if not armed |
| `/stop` | hard stop, always honored, returns to `idle` |

JSON envelopes per [docs/protocol.md](../../docs/protocol.md) are also accepted; the device replies with a serialized envelope when the input was JSON.

## 1. Install

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

## 2. Bot token

Same as `pipe_check`. See [docs/pi-setup.md §4](../../docs/pi-setup.md). Once you have a token, drop it into `~/.config/freemotion.env`:

```ini
TELEGRAM_BOT_TOKEN=123456:ABC-your-token-here

# Optional but recommended after the first message:
# TELEGRAM_ALLOWED_CHAT_IDS=11111111

# Optional metadata; useful when /status is shown in chat:
FREEMOTION_DEVICE_ID=mock-drone-01
FREEMOTION_HARDWARE=mock

# Default is dry_run (safest). Set to bench so /arm and /move actuate.
FREEMOTION_SAFETY_DEFAULT=bench

# Optional per-command deny list (CSV of wire command names). Refused at
# the router with error.code = "denied_by_policy". `stop` cannot be denied.
# FREEMOTION_DENIED_COMMANDS=arm,move
```

## 3. Run

```bash
set -a && source ~/.config/freemotion.env && set +a
python examples/mock_drone/mock_drone.py
```

## 4. Try it from Telegram

A short tour:

```text
You: /capabilities
Bot: capabilities: arm, capabilities, disarm, move, ping, status, stop

You: /status
Bot: device: mock-drone-01
     hardware: mock
     ...
     armed: no

You: /arm
Bot: armed

You: /move 1 2 3
Bot: moved (1.0, 2.0, 3.0)

You: /status
Bot: device: mock-drone-01
     ...
     armed: yes

You: /stop
Bot: stopped
```

If you sent `/move` without `/arm` first, the device replies with an `unsafe_in_mode` error. If your `safety` is `dry_run`, `/move` logs the intent but does not change state. That mirrors how a real device behaves under the same protocol.

## 5. Add a command of your own

Pick the smallest possible change: a `/hello` slash command that replies with a custom message.

1. Add `HELLO = "hello"` to `freemotion/protocol/envelopes.py`.
2. Add a slash branch for `hello` in `freemotion/protocol/codec.py::parse_slash`.
3. Write a handler factory `make_hello_handler(cfg)`.
4. Register it in `mock_drone.py::build_router`.
5. Add a test in `tests/test_protocol.py`.

The runtime needs no other change. The router, agent, and config layers are agnostic.

## Troubleshooting

- **`unauthorized chat`** — your chat id isn't in `TELEGRAM_ALLOWED_CHAT_IDS`. Send any non-slash text first; the bot echoes your chat id, and you can copy it into the env file.
- **`/arm` always refused** — check `FREEMOTION_SAFETY_DEFAULT`. `dry_run` refuses `arm` by design.
- **`/move` says `not armed`** — call `/arm` first.
- **Bot stays silent** — check the foreground console output for connection errors. Most often a bad token.
