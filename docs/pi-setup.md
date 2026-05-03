# Raspberry Pi setup

Use this after you have a bootable SD card and network. It turns a fresh Pi into a sensible dev host for Free Motion.

## 1. System

- **OS:** 64-bit Raspberry Pi OS or Ubuntu for Raspberry Pi (pick one team-wide).
- Apply updates:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

- Optional but recommended: enable **SSH** (Raspberry Pi OS: `sudo raspi-config` → Interface Options → SSH).

## 2. Dev basics

```bash
sudo apt install -y git python3 python3-venv python3-pip
python3 --version   # 3.10+ is typical; use what your distro ships
```

Create a workspace and clone:

```bash
mkdir -p ~/src && cd ~/src
git clone https://github.com/SpencerBrown1717/Free_Motion.git
cd Free_Motion
```

## 3. Python environment

Keep dependencies isolated:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

Install the project (editable, so changes you make show up immediately):

```bash
pip install -e .
```

On a Pi with an LED you want to drive, also:

```bash
pip install RPi.GPIO
```

## 4. Telegram bot credentials

Never commit tokens.

- Create a bot with **BotFather** on Telegram; keep the **bot token** private.
- On the Pi, store tokens in a file that only your user can read, for example:

```bash
mkdir -p ~/.config
install -m 600 /dev/null ~/.config/freemotion.env
# edit the file; example:
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_ALLOWED_CHAT_ID=...   # optional: restrict who can talk to the bot
```

Load in shells when developing:

```bash
set -a && source ~/.config/freemotion.env && set +a
```

Exact variable names will match whatever the first shipped example uses; this layout is the pattern.

## 5. Camera (when you need vision)

- Enable the camera interface on Raspberry Pi OS if you use the CSI module (`raspi-config` → Interface Options → Camera), or plug in a supported USB camera.
- Confirm a device appears (`libcamera-hello` on Bookworm-era systems, or your distro’s equivalent test).

## 6. Realtime and GPIO (later)

Motion code will depend on how you drive hardware (PWM hats, MAVLink to a flight controller, etc.). Until that code lives in the repo, treat GPIO and ESC calibration as **follow the hardware vendor’s guide** and [SAFETY.md](../SAFETY.md).

## 7. Checklist before “first demo”

- [ ] Network stable; you can `ping` the outside world.
- [ ] `git pull` works; you are on a known branch/commit.
- [ ] `python3 -m venv` works; you can activate `.venv`.
- [ ] Telegram token is in a **non-committed** env file with strict permissions.
- [ ] [SAFETY.md](../SAFETY.md) read if the demo touches motors or props.

When the demo script exists, [GETTING_STARTED.md](../GETTING_STARTED.md) will point to the exact command.
