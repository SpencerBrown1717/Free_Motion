# Demo

Free Motion has three runnable demos. Each one proves a different thing.

| Demo | Hardware | Telegram | Models | What it proves |
|---|---|---|---|---|
| [`examples/local_sim_demo.py`](../examples/local_sim_demo.py) | none | none | mocks | The full M3 loop runs end-to-end on a laptop with no setup. |
| [`examples/mock_drone/`](../examples/mock_drone/) | none | required | mocks | The Telegram → router → controller path works without hardware. |
| [`examples/pipe_check/`](../examples/pipe_check/) | Raspberry Pi + LED (optional) | required | none | The Telegram → device path works on a real Pi. |

Start with the local sim. It's the fastest path from `git clone` to "I see Free Motion run."

## 60-second quickstart (no hardware, no Telegram, no models)

```bash
git clone https://github.com/SpencerBrown1717/Free_Motion.git
cd Free_Motion
python -m venv .venv && source .venv/bin/activate
pip install -e .
python examples/local_sim_demo.py
```

What you'll see:

- One `arm` command flowing through the protocol → router → mock controller.
- Five ticks of an "intent → vision → world → mission_control → router → controller → world" loop.
- Each tick prints the vision detections, the mission decision (with reason and confidence), the dispatched protocol envelope, the controller state, and the resulting `WorldStateSnapshot`.
- The state mutates deterministically. The loop terminates.

That's the same code path a real device runs. Only the mock backends will be swapped for `YoloVision`, `GemmaMissionControl`, and `PiHardwareController` later — see [`docs/models.md`](models.md) for the swap path.

## What each tick does

The script scripts five intents through the loop:

| Tick | Intent | Vision (mocked) | Mission decision | Hardware effect |
|---|---|---|---|---|
| 1 | `follow person` | person at conf 0.92 | `move (1, 0, 0)` | position updates |
| 2 | `follow person` | empty scene | idle (no person) | no change |
| 3 | `party time` | empty scene | idle (unknown intent) | no change |
| 4 | `stop` | (ignored) | `stop` | disarmed |
| 5 | `disarm` | (ignored) | `disarm` | confirmed disarmed |

The full protocol envelope is printed for every dispatched command so the wire format is visible.

## How to extend it

Three points where the demo is meant to be modified:

1. **More intents.** Add an entry to `intents`. `MockMissionControl` will idle on anything unrecognized — this is the right behavior to test how the loop handles unknown plans.
2. **Different scenes.** Add scripted `VisionResult`s to `MockVision` to drive new mission decisions. A scene with a tracked person, an obstacle, and an unknown class is a useful next test.
3. **A real adapter.** Construct a real `VisionBackend` or `MissionPolicy` instead of the mock. The interfaces are in [`docs/models.md`](models.md). The runtime won't notice.

## When to graduate to the other demos

- After `local_sim_demo` runs cleanly and you've made one change to it.
- Then `examples/mock_drone/` — same loop, but with Telegram in front. Lets you drive the device from your phone.
- Then `examples/pipe_check/` — real Pi, real GPIO, no AI yet.
- After M4 ships, the first hardware demo.

## Running the demo as a smoke test

`tests/test_local_sim_demo.py` runs the demo and asserts the loop terminates with the expected state transitions. CI runs it on every push, so a regression that breaks the loop breaks the build.
