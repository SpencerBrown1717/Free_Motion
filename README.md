# Free Motion

**OpenClaw flies drones.** Open source AI motion layer for drones, robots, and Raspberry Pi.

OpenClaw sends messages through **Telegram** to a **Raspberry Pi**. The Pi runs **two models** on board — **one vision**, **one mission control** — and the **drone flies**.

## Live site
[Free Motion website](https://spencerbrown1717.github.io/Free_Motion/)

## What it does
- Telegram as the pipe from OpenClaw to the Pi
- Two on-device models: vision + mission control
- Executes flight on the drone (and can extend to other robots)
- Status and feedback during operation

## How it works
1. OpenClaw sends the command via Telegram to the Raspberry Pi
2. The Pi runs the vision model and the mission control model
3. The drone executes the motion; you get updates as it runs

## Stack
- HTML landing page
- Raspberry Pi
- Telegram (OpenClaw → Pi)
- Mission control model
- Vision model

## Status
Hackathon stage, open source, MIT licensed.

## Contributing
Pull requests, issues, and ideas are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License
MIT
