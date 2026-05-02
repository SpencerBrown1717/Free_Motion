# Free Motion

Open source AI motion layer for drones, robots, and Raspberry Pi builds.

Free Motion connects OpenClaw style command systems to real hardware. A user sends a command, the Raspberry Pi receives instructions through Telegram, runs on board logic, and controls the connected drone or robot.

## Live site
[Free Motion website](https://spencerbrown1717.github.io/Free_Motion/)

## What it does
- Sends missions through Telegram
- Runs mission control locally on Raspberry Pi
- Uses a visual model to understand the camera feed
- Controls drones, robots, or other moving hardware
- Reports status back during operation

## How it works
1. User sends a command through Telegram
2. Raspberry Pi runs two models
3. Device executes the motion task
4. System reports back what it is doing and seeing

## Stack
- HTML landing page
- Raspberry Pi
- Telegram bot
- Mission control model
- Visual model

## Status
Hackathon stage, open source, MIT licensed.

## Contributing
Pull requests, issues, and ideas are welcome.

## License
MIT
