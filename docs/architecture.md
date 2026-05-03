# Architecture

Free Motion is an **edge-first motion layer**: commands arrive from OpenClaw, the device thinks and sees locally, and motion happens on the machine.

## Narrative flow

1. A user gives **OpenClaw** a real-world instruction.
2. OpenClaw sends the instruction over **Telegram** to the edge device.
3. The edge device runs:
   - **Vision (YOLO)** to interpret the scene.
   - **Mission control (Gemma small)** to decide what to do next.
4. **Motion execution** drives motors, servos, or an autopilot bridge as implemented for that platform.
5. Status and observations go back upstream so OpenClaw stays in the loop.

```text
User
  ↓
OpenClaw
  ↓
Telegram
  ↓
Edge device (Pi first; Jetson / ESP32 / Arduino later)
  ├─ YOLO          → perception / tracks / detections
  ├─ Gemma small   → task parsing, policy, next action
  └─ Actuation     → motors, ESCs, flight stacks, GPIO
  ↓
World + telemetry back to OpenClaw
```

## Why this shape

- **Telegram** is a simple, reliable-enough pipe for early builders: easy to monitor, easy to throttle, no custom cloud required for v1.
- **On-device models** keep latency down and avoid streaming raw video to a datacenter for basic autonomy loops.
- **Pi-first** matches the README: affordable, well-documented, good enough to prove the full stack before optimizing on Jetson or shrinking onto ESP32/Arduino peripherals.

## Named modules

The system is framed around six modules. Each has a clear job and a clear status. The [ROADMAP.md](../ROADMAP.md) lights these up in order.

| Module | Role | Status |
|---|---|---|
| **Transport** | Move bytes between OpenClaw and the device. | Telegram shipped (M0) |
| **Protocol** | Command + reply envelopes, validation, versioning. | shipped (M1) — see [protocol.md](protocol.md) and `freemotion/protocol/` |
| **Agent / runtime** | Long-running service on the device: receive → validate → route → reply. | shipped (M2) — see `freemotion/agent/`, `freemotion/router/`, `freemotion/config/` |
| **Mission control** | Goal + perception → next action. Gemma small target. | `MissionPolicy` Protocol + `MockMissionControl` shipped (M3 partial) — see [models.md](models.md) and `freemotion/mission_control/`; `GemmaMissionControl` pending |
| **Vision** | On-device perception. YOLO target. | `VisionBackend` Protocol + `MockVision` shipped (M3 partial) — see [models.md](models.md) and `freemotion/vision/`; `YoloVision` pending |
| **Hardware adapter** | Per-platform actuators (Pi GPIO first, then Jetson, ESP32, Arduino). | `HardwareController` Protocol + `MockHardwareController` shipped (M2); Pi GPIO via `examples/pipe_check/`; `PiHardwareController` on the roadmap |
| **World state** | Shared "what does the device think is true now." | `WorldStateSnapshot` + `WorldState` shipped (M3) — see `freemotion/world/` |
| **Safety** | Modes, hard stops, rate limits, watchdogs. Cuts across every other module. | basics in protocol (M1), expanded with M4 |

## Repository layout (today)

```text
Free_Motion/
├── README.md, GETTING_STARTED.md, ROADMAP.md, SAFETY.md, CONTRIBUTING.md
├── pyproject.toml        # makes `freemotion` installable (`pip install -e .`)
├── freemotion/
│   ├── __init__.py
│   ├── protocol/         # v0 envelopes, parser, serializer, slash sugar (M1)
│   ├── config/           # frozen Config, env-driven (M2)
│   ├── router/           # CommandName -> Handler dispatch (M2)
│   ├── agent/            # Telegram transport + handle_text + built-in handlers (M2)
│   ├── hardware/         # HardwareController Protocol + MockHardwareController (M2)
│   ├── vision/           # VisionBackend Protocol + MockVision (M3 partial)
│   ├── mission_control/  # MissionPolicy Protocol + MockMissionControl (M3 partial)
│   └── world/            # WorldStateSnapshot + WorldState (M3)
├── docs/
│   ├── architecture.md   # this file
│   ├── decisions.md      # short ADR ledger
│   ├── issues/           # drafted issue packs + file_issues.sh
│   ├── models.md         # vision + mission control interfaces and adapter swap path
│   ├── pi-runtime.md     # how to write a device on top of the runtime
│   ├── pi-setup.md       # how to prepare a Pi
│   └── protocol.md       # command + reply envelope contract (v0)
├── examples/
│   ├── local_sim_demo.py # 60-second laptop demo: closes the M3 loop on mocks, no setup
│   ├── pipe_check/       # Pi reference: GPIO LED + Agent wiring
│   └── mock_drone/       # No-hardware reference: MockHardwareController + Agent
├── tests/                # protocol + config + router + builtins + agent + hardware + vision + mission + world + pipe_check + local_sim_demo
├── CHANGELOG.md
└── .github/workflows/    # ci.yml: install + import + pytest
```

Future code (Python package `freemotion/...`) will land milestone by milestone; see [ROADMAP.md](../ROADMAP.md) for the order.

## Related reading

- Command contract: [protocol.md](protocol.md)
- Hardware order: [ROADMAP.md](../ROADMAP.md)
- Pi setup: [pi-setup.md](pi-setup.md)
- Safety baseline: [SAFETY.md](../SAFETY.md)
