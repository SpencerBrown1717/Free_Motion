# Free Motion

**Open source AI motion layer for drones, robots, and edge devices.**
OpenClaw sends a command. The device sees, decides, and moves on its own.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/SpencerBrown1717/Free_Motion/actions/workflows/ci.yml/badge.svg)](https://github.com/SpencerBrown1717/Free_Motion/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-174%20passing-brightgreen.svg)](tests/)

**Site:** [freemotion.tech](https://www.freemotion.tech/) · **Splash:** [spencerbrown1717.github.io/Free_Motion](https://spencerbrown1717.github.io/Free_Motion/) · **Roadmap:** [ROADMAP.md](ROADMAP.md)

## What it does

```mermaid
flowchart LR
    A[User / OpenClaw] --> B[Telegram]
    B --> C[Free Motion device]
    C --> D[Vision<br/>YOLO]
    C --> E[Mission control<br/>Gemma small]
    D --> F[Hardware adapter]
    E --> F
    F --> G[Motors / GPIO / autopilot]
    G --> H[Telemetry + status]
    H --> A
```

The device runs locally: perception, decisions, and motion all happen on the edge. Cloud is optional, not required.

## Run the demo in 60 seconds

No hardware, no Telegram, no models. Just the real loop on mock backends.

```bash
git clone https://github.com/SpencerBrown1717/Free_Motion.git
cd Free_Motion
python -m venv .venv && source .venv/bin/activate
pip install -e .
python examples/local_sim_demo.py
```

You'll see five ticks of `intent → vision → mission_control → protocol → router → hardware → state`, with the full wire envelope printed for every dispatched command. Same code path a real device runs; only the backends change.

**Got a Pi?** Graduate to the real bench rig: [`examples/pi_bench_demo/`](examples/pi_bench_demo/) wires `Config.from_env` → `PiHardwareController` → `SafetyGate` → `Router` → `Agent` over Telegram, with two GPIO indicator pins reflecting real hardware state. Walkthrough in [`docs/pi-hardware.md`](docs/pi-hardware.md). More demos and the swap path are in [`docs/demo.md`](docs/demo.md).

## Default stack vs. swappable stack

| Layer | Default (today) | Pluggable interface |
|---|---|---|
| Transport | Telegram | (more transports later) |
| Protocol | v0 (typed envelopes) | stable contract — see [`docs/protocol.md`](docs/protocol.md) |
| Vision | `MockVision` (real: YOLO, planned) | [`VisionBackend`](freemotion/vision/interface.py) |
| Mission control | `MockMissionControl` (real: Gemma small, planned) | [`MissionPolicy`](freemotion/mission_control/interface.py) |
| World state | `WorldStateSnapshot` + `WorldState` (lock-protected) | [`freemotion.world`](freemotion/world/state.py) |
| Hardware | `MockHardwareController` **and** `PiHardwareController` (M4) | [`HardwareController`](freemotion/hardware/interface.py) |
| Safety | `SafetyGate` (M4) — device default is the floor; `dry_run` blocks `arm`/`move`; `stop` always passes | [`SafetyGate`](freemotion/hardware/safety.py), [ADR-0006](docs/decisions.md#adr-0006--safetygate-enforce-safetymode-at-the-hardware-boundary-dry_run-is-the-floor--2026-05-03) |
| Target device | Raspberry Pi (M4 shipped) | Jetson, ESP32, Arduino on the roadmap |

Every layer is a `Protocol` you can implement. See [`docs/models.md`](docs/models.md) for the model swap path and [`docs/pi-hardware.md`](docs/pi-hardware.md) for the Pi adapter.

## Current status

- **Shipped:** Telegram transport (M0); protocol v0 (M1); device runtime — config + router + agent (M2); mock hardware (M2); per-command deny list (M2); vision and mission interfaces + mocks (M3 partial); world state (M3); end-to-end loop demo (M3); **Pi hardware controller, bench demo, and SafetyGate (M4)**.
- **Mocked, not yet real:** YOLO vision adapter, Gemma small mission policy, higher autonomy (multi-step plans).
- **Not started:** Jetson / ESP32 / Arduino support (M5).

174 tests passing on every push, including 22 covering the Pi controller (via `FakeGPIO`) and 14 covering the safety gate. The full state of play is in [`ROADMAP.md`](ROADMAP.md); open work is in [`docs/issues/m2-m3.md`](docs/issues/m2-m3.md).

## Repository tour

```text
freemotion/
├── protocol/         # v0 envelopes, parser, serializer
├── config/           # frozen Config, env-driven
├── router/           # CommandName -> Handler dispatch (with deny policy)
├── agent/            # Telegram transport + handle_text + builtin handlers
├── hardware/         # HardwareController Protocol + Mock + Pi + SafetyGate
├── vision/           # VisionBackend Protocol + MockVision
├── mission_control/  # MissionPolicy Protocol + MockMissionControl
└── world/            # WorldStateSnapshot + WorldState (thread-safe)

examples/
├── local_sim_demo.py # 60-second laptop demo, no setup
├── mock_drone/       # Telegram + mocks, no hardware
├── pipe_check/       # Smallest Pi check (M0) — optional GPIO LED
└── pi_bench_demo/    # Real Pi (M4) — PiHardwareController + SafetyGate

docs/
├── architecture.md   # how the modules fit
├── decisions.md      # ADR ledger
├── demo.md           # the four demos and what each one proves
├── models.md         # vision + mission control swap path
├── pi-hardware.md    # Pi controller, safety gate, bench flow (M4)
├── pi-runtime.md     # how to write a device on the runtime
├── pi-setup.md       # how to prepare a Pi
├── protocol.md       # command + reply envelope contract
└── issues/           # drafted issue packs + file_issues.sh
```

## Safety and non-goals

This project moves real motors. Trust comes from boundaries.

- **Default safety mode is `dry_run`.** No actuation unless a device explicitly opts in to `bench` or `live`. See [`SAFETY.md`](SAFETY.md).
- **`SafetyGate` is the floor.** `cfg.safety_default` is enforced at the controller boundary; a per-command `safety=bench` against a `dry_run` device is refused. Configuration is the floor, not the ceiling. See [ADR-0006](docs/decisions.md#adr-0006--safetygate-enforce-safetymode-at-the-hardware-boundary-dry_run-is-the-floor--2026-05-03).
- **`stop` is honored unconditionally.** Dispatch always succeeds; handler exceptions can't swallow it; the deny policy can't refuse it; the safety gate can't block it.
- **Auth is not optional.** Chat-id allowlist is enforced at the agent layer; unauthenticated messages never reach a handler.
- **Deny policy is per-device.** Set `FREEMOTION_DENIED_COMMANDS=arm,move` and the router refuses those commands before any handler runs. Refused replies surface `error.code="denied_by_policy"`. `stop` is always exempt.

Non-goals for v0.x:

- Cloud-hosted control plane. Free Motion is edge-first by design.
- A general-purpose autopilot. We ship one narrow loop well; mission control returns one structured next action, not a free-form plan.
- A model zoo. The default stack is YOLO + Gemma small. Other backends are welcome via the interfaces, not in the default install.

## Contributing

The repo crosses the threshold where a stranger can usefully contribute. Three quick paths:

1. **Run [`examples/local_sim_demo.py`](examples/local_sim_demo.py)** and read the code. Open a PR for any rough edge.
2. **Land a real adapter.** Top of the post-M4 list: `YoloVision` (`VisionBackend`) and `GemmaMissionControl` (`MissionPolicy`). Interfaces are frozen in `freemotion/vision/` and `freemotion/mission_control/`; mocks are the structural reference. See [`docs/models.md`](docs/models.md) for the swap path.
3. **Implement a hardware adapter.** `PiHardwareController` is the M4 reference. Jetson, ESP32, Arduino are open against the same `HardwareController` Protocol. See [`docs/pi-hardware.md`](docs/pi-hardware.md) for the bench-rig contract.

Contribution guide: [`CONTRIBUTING.md`](CONTRIBUTING.md). Architectural decisions live in [`docs/decisions.md`](docs/decisions.md).

## License

[MIT](LICENSE).
