# Free Motion

**OpenClaw flies drones.**  
Free Motion is an open source AI motion layer for drones, robots, and Raspberry Pi based systems.

At its core, Free Motion lets **OpenClaw send commands through Telegram to an edge device**, starting with **Raspberry Pi**. The device runs **two on board models**:

- a **vision model** for perception
- a **mission control model** for action and decision making

The result is simple:

**OpenClaw → Telegram → device → motion**

## Website

**Product site:** [freemotion.tech](https://www.freemotion.tech/)  
**Repo:** [github.com/SpencerBrown1717/Free_Motion](https://github.com/SpencerBrown1717/Free_Motion)  
**GitHub Pages splash:** [spencerbrown1717.github.io/Free_Motion](https://spencerbrown1717.github.io/Free_Motion/)

## What Free Motion does

Free Motion is designed to let OpenClaw communicate with real world hardware in a lightweight, practical way.

Current direction:

- **Telegram** is the command pipe from OpenClaw to the device
- **Raspberry Pi** is the first development target
- **YOLO** handles on device vision
- **Gemma small** handles on device mission control
- The device executes motion locally and reports status back upstream

While the first focus is drones, the same pattern can extend to:

- ground robots
- roombas
- robot dogs
- camera rigs
- other lightweight edge controlled systems

## How it works

1. A user gives OpenClaw a real world instruction
2. OpenClaw sends the command through Telegram
3. The connected Raspberry Pi receives the command
4. The Pi runs:
   - a **vision model** to understand the environment
   - a **mission control model** to decide what to do next
5. The device executes the motion task
6. The system reports status, updates, and observations back to OpenClaw

## Core architecture

```text
User
  ↓
OpenClaw
  ↓
Telegram
  ↓
Edge device
  ├─ YOLO vision
  ├─ Gemma small mission control
  └─ Motion execution
  ↓
Drone / robot action + status updates
