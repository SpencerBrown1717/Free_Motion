# Getting started (Raspberry Pi)

Goal: get from **zero** to **first demo on a Pi** with as few steps as possible.

## What you need

- A **Raspberry Pi** (Pi 4 or newer is the practical default)
- **Reliable power** (official supply beats random USB cables)
- **microSD card** (32 GB+ Class A1 is fine)
- **Wi‑Fi or Ethernet** so the Pi can reach Telegram

## Steps

### 1. Flash the Pi

Use Raspberry Pi Imager. Install **64-bit Raspberry Pi OS** (or Ubuntu for Pi if you already standardize on that).

Full walkthrough: [docs/pi-setup.md](docs/pi-setup.md)

### 2. First boot

- Finish the setup wizard (user, Wi‑Fi, updates)
- Turn on **SSH** if you want headless access

### 3. Clone this repo on the Pi

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/SpencerBrown1717/Free_Motion.git
cd Free_Motion
```

### 4. Do the Pi environment once

Follow [docs/pi-setup.md](docs/pi-setup.md) through Python, a virtual environment, and Telegram prep.

### 5. Run the first demo

The first runnable demo is **pipe_check** (M0). It proves Telegram → device works on the machine in front of you, with no motion involved.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
set -a && source ~/.config/freemotion.env && set +a
python examples/pipe_check/pipe_check.py
```

Then DM your bot `/ping` from Telegram. Full walkthrough, allowlist setup, optional LED wiring, and autostart are in [examples/pipe_check/README.md](examples/pipe_check/README.md).

**Safety before motion:** read [SAFETY.md](SAFETY.md) before anything that spins props or moves a robot.

## Where to go next

- How the system fits together: [docs/architecture.md](docs/architecture.md)
- What is being built in what order: [ROADMAP.md](ROADMAP.md)
