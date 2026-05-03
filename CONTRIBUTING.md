# Contributing to Free Motion

Thanks for contributing to Free Motion.

Free Motion is an open source motion layer that lets **OpenClaw send real world commands to connected devices** through **Telegram**, with on-device models handling vision and mission execution.

The core system story is:

**OpenClaw → Telegram → device → action**

For the first version, Free Motion is being built around:

- **YOLO** for on-device vision
- **Gemma small** for on-device mission control
- **Ubuntu + Telegram** for receiving commands and reporting back

Our long term goal is simple:

**Let OpenClaw talk to and coordinate real devices across multiple hardware types.**

## Hardware priority

Development priority should follow this order:

1. **Raspberry Pi**  
   Main development target and first class platform for Free Motion.

2. **Jetson Nano**  
   Second priority for stronger edge inference and robotics use cases.

3. **ESP32**  
   Third priority for lightweight control, peripherals, and embedded tasks.

4. **Arduino**  
   Fourth priority for simple hardware control and low level integrations.

When contributing, work that improves support in this order is especially valuable.

## Model and system priorities

We want contributions that keep the architecture clear and practical:

- **YOLO for vision**
  - object detection
  - person following
  - scene awareness
  - lightweight real time perception on device

- **Gemma small for mission control**
  - interpret task instructions
  - decide next action
  - handle simple autonomy loops
  - report status back to OpenClaw

- **Ubuntu + Telegram transport**
  - receive commands reliably
  - send updates back upstream
  - support unstable connections gracefully
  - keep setup simple for builders

## What to contribute

High value contributions include:

- Fixes in the **Telegram → device** command path
- Better integration between **YOLO** and **Gemma**
- Raspberry Pi setup, scripts, and deployment improvements
- Jetson Nano support and optimization
- ESP32 and Arduino bridge work
- Examples for drones, rovers, robot dogs, roombas, or similar systems
- Documentation that makes the architecture easier to understand
- Safety notes for testing motion systems in the real world

## Contribution guidelines

Please try to keep contributions aligned with the core direction of the project:

- Prefer simple, practical implementations over heavy abstraction
- Keep the OpenClaw → Telegram → device workflow easy to understand
- Prioritize Raspberry Pi support before expanding to lower priority hardware
- Make setup easy for hackathon builders and open source contributors
- Document assumptions clearly, especially around device control and autonomy
- Avoid unnecessary complexity unless it clearly improves reliability or usability

## How to contribute

1. Fork the repo
2. Create a branch
3. Make your changes
4. Test what you changed
5. Open a pull request

## Pull request focus

Pull requests are especially helpful when they improve one of these areas:

- Raspberry Pi support
- YOLO vision pipeline
- Gemma mission control pipeline
- Telegram communication reliability
- Device reporting back to OpenClaw
- Clearer documentation and setup guides
- Support for Jetson Nano, ESP32, or Arduino in that order

## Notes for contributors

Free Motion is being built first as a practical open source project and development platform.

That means the best contributions usually make the system:

- easier to run
- easier to understand
- easier to extend
- more reliable on real hardware

If your contribution helps OpenClaw communicate with Raspberry Pi, Jetson Nano, ESP32, or Arduino more cleanly, it is likely a strong fit for the project.
