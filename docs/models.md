# Models

Where YOLO and Gemma small plug in. The interfaces and mock adapters ship now; the real model adapters land behind feature flags later.

## Why interfaces first

Model dependencies are heavy. Pulling YOLO or Gemma into the runtime as a hard requirement would mean:

- CI gets slower (model downloads).
- Contributors without a GPU can't run anything.
- The runtime is held hostage to upstream model releases.
- Every change to vision or mission code risks bricking the rest of the system.

So we land **architecture now, dependency later**. This file is the contract real adapters will conform to. ADR-0003 in [`decisions.md`](decisions.md) records the call.

## Vision

### Interface — [`freemotion/vision/interface.py`](../freemotion/vision/interface.py)

```python
@dataclass(frozen=True)
class Detection:
    label: str           # "person", "obstacle", "vehicle", ...
    confidence: float    # 0.0..1.0
    bbox: tuple[float, float, float, float]  # x, y, w, h, normalized 0..1


@dataclass(frozen=True)
class VisionResult:
    detections: tuple[Detection, ...]
    ts: str  # ISO 8601 UTC


@runtime_checkable
class VisionBackend(Protocol):
    name: str
    @property
    def available(self) -> bool: ...
    def scene(self) -> VisionResult: ...
```

Single method (`scene()`) on purpose. The backend manages its own input — a mock returns scripted data, a real adapter pulls from its camera or a frame buffer. Callers don't care.

### Mock — [`freemotion/vision/mock.py`](../freemotion/vision/mock.py)

```python
MockVision()                              # always returns empty scene
MockVision(scripted=[r1, r2, r3])         # cycles through r1 -> r2 -> r3 -> r1 -> ...
```

Deterministic. Drives [`examples/local_sim_demo.py`](../examples/local_sim_demo.py) and the test suite.

### Real (planned)

`freemotion/vision/yolo.py` will wrap `ultralytics` (or equivalent) with the same `VisionBackend` interface. v1 scope is intentionally narrow:

- Person detection
- A small set of obstacle classes
- Confidence + normalized bbox

Construction is gated on a config flag (e.g. `FREEMOTION_VISION_BACKEND=yolo`). That flag does not exist yet — it lands with the adapter.

## Mission control

### Interface — [`freemotion/mission_control/interface.py`](../freemotion/mission_control/interface.py)

```python
@dataclass(frozen=True)
class MissionDecision:
    next_command: Optional[CommandName]   # None = idle / no-op
    args: Mapping[str, Any]               # args for next_command
    reason: str                           # human-readable explanation
    confidence: float                     # 0.0..1.0


    @runtime_checkable
class MissionPolicy(Protocol):
    name: str
    @property
    def available(self) -> bool: ...
    def plan(
        self,
        *,
        intent: str,
        scene: VisionResult,
        world: WorldStateSnapshot,
    ) -> MissionDecision: ...
```

`world` is the M3 [`WorldStateSnapshot`](../freemotion/world/state.py); pass `WorldStateSnapshot()` when no live state is available. ADR-0005 in [`decisions.md`](decisions.md) records the shape.

`plan` returns a **structured decision**, not free-form text. The runtime translates it back into a protocol `Command` and runs it through the router. That's the only contract the rest of the system needs.

### Mock — [`freemotion/mission_control/mock.py`](../freemotion/mission_control/mock.py)

Rule-based, deterministic. Recognizes:

| Intent | Decision |
|---|---|
| `stop` / `halt` / `abort` | `stop`, confidence 1.0 |
| `disarm` / `land` | `disarm`, confidence 1.0 |
| `follow` / `follow person` (with person in scene) | `move (1, 0, 0)`, confidence = best detection confidence |
| `follow` / `follow person` (no person in scene) | idle, confidence 0.0 |
| anything else | idle, confidence 0.0 |

This is **the structural pattern Gemma will follow**, not a permanent home for the logic.

### Real (planned)

`freemotion/mission_control/gemma.py` will wrap `transformers` or `llama.cpp` with the same `MissionPolicy` interface. v1 scope:

- Parse high-level intent.
- Suggest the next concrete action (one `CommandName` + args).
- Return a short structured decision with a reason and a confidence.

Not free-form robotics. Same shape as the mock, with a bigger brain.

Construction is gated on `FREEMOTION_MISSION_BACKEND=gemma` (flag also lands with the adapter).

## How they compose

```text
Telegram intent (e.g. "/follow person")
    │
    ▼
mission_control.plan(intent, scene, world)        # MissionPolicy
    │
    ▼
MissionDecision(next_command, args, reason, confidence)
    │
    ▼
Router.dispatch(Command(next_command, args, ...))   # protocol-typed
    │
    ▼
Reply
```

`scene` comes from a `VisionBackend.scene()` call (mock or real). `world` comes from `WorldState().snapshot()` ([`freemotion.world`](../freemotion/world/__init__.py), shipped in M3).

## Where these are wired

[`examples/local_sim_demo.py`](../examples/local_sim_demo.py) closes the loop with both mocks plus `WorldState`: vision detections feed `world.see(...)`, mission decisions consume the snapshot, the router executes the resulting `Command`, and post-dispatch hardware state is reflected back into the world. When real adapters land, swapping each is one config flag — no other code changes.

## Adding your own backend

The Protocol is `runtime_checkable`. Implement the methods, instantiate, pass to whatever wires it in. No registration ceremony, no plugin manager. See `MockVision` / `MockMissionControl` for the shape.

If you write a real adapter:

- Keep the v1 scope narrow. Resist feature creep before the second example exists.
- Make construction lazy if the dependency is heavy (don't import `torch` at module load time).
- Document the env var that turns it on.
- Add an ADR if you make a non-obvious design call.

## Related

- [`docs/protocol.md`](protocol.md) — the wire format `MissionDecision` translates back into.
- [`docs/pi-runtime.md`](pi-runtime.md) — how `freemotion.{config,router,agent}` compose.
- [`docs/decisions.md`](decisions.md) — ADR-0003 for the interface-first call.
