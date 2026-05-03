# Models

Where YOLO and Gemma small plug in. Interfaces and mock adapters ship as defaults; real model adapters live behind feature flags and optional install extras.

## Why interfaces first

Model dependencies are heavy. Pulling YOLO or Gemma into the runtime as a hard requirement would mean:

- CI gets slower (model downloads).
- Contributors without a GPU can't run anything.
- The runtime is held hostage to upstream model releases.
- Every change to vision or mission code risks bricking the rest of the system.

So we land **architecture now, dependency optional**. This file is the contract real adapters conform to. ADR-0003 in [`decisions.md`](decisions.md) records the call. ADR-0007 (`YoloVision`) and ADR-0008 (`GemmaMissionControl`) lock the v1 shapes.

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

### Real — `YoloVision` (shipped, post-M4)

`freemotion/vision/yolo.py` wraps `ultralytics` YOLO with the same `VisionBackend` interface. v1 scope, locked by [ADR-0007](decisions.md#adr-0007--yolovision-v1-ultralytics-backed-person-only-callable-frame-source-corner-based-bbox--2026-05-03):

- **Person detection by default** (`classes=frozenset({"person"})`; override with `classes=[...]` or `classes=[]` for every label).
- **One model, one threshold:** `yolov8n.pt` (~6 MB nano) and confidence `0.25`. Both are constructor args.
- **Frame source is a caller-injected callable.** The backend does not own the camera. Plug in `cv2.VideoCapture`, `picamera2`, an MJPEG stream, or a directory of test frames.
- **Lazy `ultralytics` import.** Module imports cleanly on a host without `[yolo]` installed; the backend stays offline (`available is False`, `scene()` returns empty) rather than crashing.
- **bbox is `(x, y, w, h)` normalized 0..1, top-left corner-based.** Ultralytics's center-based `xywhn` is converted internally and clamped to the unit square.
- **`min_interval_s` throttle** as the "cheap `scene()`" contract. Default `0.0` (no throttle).

Install and turn it on:

```bash
pip install -e .[yolo]
export FREEMOTION_VISION_BACKEND=yolo
```

Wire by hand for any non-default knob:

```python
import cv2
from freemotion.vision import YoloVision

cap = cv2.VideoCapture(0)
vision = YoloVision(
    frame_source=lambda: (lambda r: r[1] if r[0] else None)(cap.read()),
    model="yolov8n.pt",
    classes=["person"],          # or [] to accept every label
    confidence_threshold=0.4,
    min_interval_s=0.1,           # cap inference at 10 Hz
)

result = vision.scene()
for det in result.detections:
    print(det.label, det.confidence, det.bbox)
```

Or via the factory (uses the constructor's defaults):

```python
from freemotion.config import Config
from freemotion.vision import make_vision_from_config

vision = make_vision_from_config(Config.from_env())  # YoloVision when FREEMOTION_VISION_BACKEND=yolo
```

Tests in [`tests/test_vision_yolo.py`](../tests/test_vision_yolo.py) (24 + 1 skip) cover the adapter through an injected `yolo_factory` so CI runs cleanly without `ultralytics`. The trailing test calls `pytest.importorskip("ultralytics")` so contributors with `[yolo]` installed get an extra smoke layer.

#### Live frames — `PiCameraSource` (shipped, Step 1)

`freemotion/vision/picamera.py` ships a canonical Pi camera frame producer for the `frame_source` seam:

```python
from freemotion.vision import PiCameraSource, YoloVision

cam = PiCameraSource(resolution=(640, 480))
try:
    vision = YoloVision(frame_source=cam, classes=["person"])
    while True:
        result = vision.scene()
        # ... do something with result.detections ...
finally:
    cam.close()
```

`PiCameraSource` is a callable, **not** a `VisionBackend`. It produces frames; YoloVision (or whatever you wire in) does inference. v1 scope per [ADR-0009](decisions.md#adr-0009--picamerasource-v1-picamera2-backed-callable-frame-producer-transient-failure-tolerant--2026-05-04):

- Backed by `picamera2`. Install via `pip install -e .[picam]`. Pi OS Bookworm or newer; the legacy `picamera` (mmal) is dead and not supported.
- Lazy `picamera2` import. Failures during import / open / configure / start flip the source offline (`available is False`, `cam()` returns `None`) and clean up the partial camera handle. The agent loop never sees a camera-induced exception.
- Per-call capture failures don't latch the source offline — one bad frame returns `None` for that tick, increments `cam.capture_failures`, the next call retries. That matches how real cameras behave.
- `close()` is idempotent and never raises. Synchronous capture, no background thread, no global lock around capture itself — `/status` keeps working while the camera is active (the architectural prerequisite for the Step 2 closed loop).
- USB webcams **don't need this wrapper.** Plug `cv2.VideoCapture(0).read`-shaped callables straight into `YoloVision(frame_source=...)`. ADR-0009 records why — a wrapper would only cargo-cult `PiCameraSource`'s libcamera-specific lifecycle.

Standalone demo and full operator walkthrough: [`examples/pi_camera_demo/`](../examples/pi_camera_demo/) and [`docs/pi-camera.md`](pi-camera.md). Tests in [`tests/test_pi_camera_source.py`](../tests/test_pi_camera_source.py) (16 + 1 skip) and [`tests/test_pi_camera_demo.py`](../tests/test_pi_camera_demo.py) (6) cover construction failures, capture failures, idempotent close, the demo's exit codes, and integration with `YoloVision` via the `frame_source` seam — all CI-clean via injected fakes.

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

### Real — `GemmaMissionControl` (shipped, post-M4)

`freemotion/mission_control/gemma.py` wraps a Hugging Face `transformers`-served Gemma instruction-tuned model with the same `MissionPolicy` interface. v1 scope, locked by [ADR-0008](decisions.md#adr-0008--gemmamissioncontrol-v1-transformers-backed-single-decision-tolerant-json-parser-fail-offline--2026-05-03):

- **One inference per `plan()` call.** No multi-step plans, no agent loops, no tool calls. The output is a single `MissionDecision`.
- **Structured output.** The LLM is asked to reply with a small JSON object: `{next_command, args, reason, confidence}`. We extract the first balanced `{...}` block from the response, parse it, normalize unknown commands to `None`, default missing fields, and clamp `confidence` to `[0, 1]`. Anything we can't normalize collapses to an idle decision instead of raising.
- **Lazy `transformers` import.** Module imports cleanly on a host without `[gemma]` installed; if the import or model load fails, the adapter stays offline (`available is False`) and `plan()` returns idle decisions with a clear reason.
- **Fail-offline on inference errors.** `client.generate()` raising (CUDA OOM, model unloaded, etc.) is caught; the agent loop never sees a Gemma-induced exception.
- **Default model is `google/gemma-2-2b-it`.** Smallest instruction-tuned Gemma 2 — the most plausible candidate for CPU-bound or modest-GPU hosts. Override via the `model=` constructor arg.
- **`_LLMClient` seam is a one-method duck type:** anything with `generate(prompt: str) -> str` is a valid client. Tests inject a fake; the default implementation wraps `transformers` (tokenizer + `AutoModelForCausalLM`, with the Gemma chat template applied when the tokenizer ships one).

Install and turn it on:

```bash
pip install -e .[gemma]
export FREEMOTION_MISSION_BACKEND=gemma
```

Wire by hand for any non-default knob:

```python
from freemotion.mission_control import GemmaMissionControl

policy = GemmaMissionControl(
    model="google/gemma-2-2b-it",
    max_new_tokens=128,
    temperature=0.1,
)

decision = policy.plan(intent="follow person", scene=scene, world=world)
if decision.next_command is not None:
    router.dispatch(...)
```

Or via the factory (uses the constructor's defaults):

```python
from freemotion.config import Config
from freemotion.mission_control import make_mission_from_config

policy = make_mission_from_config(Config.from_env())  # GemmaMissionControl when FREEMOTION_MISSION_BACKEND=gemma
```

Tests in [`tests/test_mission_gemma.py`](../tests/test_mission_gemma.py) (37 tests) cover the adapter through an injected `gemma_factory` so CI runs cleanly without `transformers`. There is no real-dep smoke test — `transformers` is heavy enough that some installs hang or SIGFPE on import in ways that even subprocess-isolated probes can't escape; the structural tests cover the entire contract.

Differences vs. the mock at a glance:

| Aspect | `MockMissionControl` | `GemmaMissionControl` |
|---|---|---|
| Inputs | intent + scene + world | same |
| Output | `MissionDecision` | same |
| Decision logic | hard-coded rules (stop / disarm / follow / idle) | LLM inference + JSON parsing |
| Determinism | totally deterministic | sampling-influenced (set `temperature=0.0` for greedy) |
| Failure mode | always `available`; idle on unknown intent | offline if model load fails; idle on parse / inference failure |
| Install cost | zero | `pip install -e .[gemma]` (transformers + torch) |
| When to use | tests, CI, local sim, default device boot | a host that has explicitly opted into the heavy stack |

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
