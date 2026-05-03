# Pi camera

Canonical Free Motion path for a live Raspberry Pi camera feeding `YoloVision`. Covers the adapter (`PiCameraSource`), the demo (`examples/pi_camera_demo/`), USB webcam alternatives, the failure model, and where it fits in the larger architecture.

## What is `PiCameraSource`?

`freemotion/vision/picamera.py` ships a tiny adapter class:

```python
from freemotion.vision import PiCameraSource, YoloVision

cam = PiCameraSource(resolution=(640, 480))
try:
    vision = YoloVision(frame_source=cam, classes=["person"])
    result = vision.scene()
finally:
    cam.close()
```

It satisfies one contract: **be a callable that returns a frame, or `None` on failure**. That's the exact shape `YoloVision`'s `frame_source` arg already expects, so dropping a `PiCameraSource()` in is a one-liner.

It is **not** a `VisionBackend`. There's no `scene()` method on it. It does not own inference; it only produces frames. ADR-0009 has the full design rationale.

## What `PiCameraSource` does

- Lazy-imports `picamera2` inside `__init__`. The module imports cleanly on any host (CI, dev laptop, Jetson). Without `picamera2` (the `[picam]` extra), the source is **offline at construction**: `available is False`, `cam()` returns `None`, the agent loop is unaffected.
- Configures a preview-style stream at the requested resolution and starts the camera. Failures during configure or start (camera busy, no kernel module, broken ribbon cable) mark the source offline and clean up the partial handle so re-running the demo doesn't trip "camera busy."
- Returns a frame per `__call__`. Each call uses `picamera2.capture_array()` and hands the ndarray straight back. YOLO accepts it directly; OpenCV does too.
- Counts per-call capture failures in `cam.capture_failures` (an `int` property). One bad frame returns `None` for that tick and increments the counter; the source stays available and the next call retries. This matches how real cameras behave — the occasional dropped frame is normal, not a reason to take the camera permanently offline.
- `close()` is idempotent. Multiple calls hit the same flag; underlying `stop()` / `close()` exceptions are caught and logged. Safe to call from any number of cleanup paths (`__exit__`, `signal.SIGINT`, the example's `finally`).
- Captures **never** acquire the source's start/close lock. A slow `capture_array()` does not block `close()`, `available`, or `capture_failures` readers. That's the architectural pre-requisite for "/status still works while camera is active" in the closed-loop demo (Step 2).

## What `PiCameraSource` does **not** do

- It does not own a background thread. Capture is synchronous per-call.
- It does not retry failed captures. One failure → one `None`.
- It does not reconfigure resolution at runtime. Build a new source instead.
- It does not implement `VisionBackend`. Plug it into a backend (`YoloVision`, or your own) via the `frame_source` arg.
- It does not handle USB webcams. See below.

## Setup

### Hardware

- Raspberry Pi 4 or 5 (3B+ technically works but YOLO nano is slow on it).
- Raspberry Pi OS **Bookworm** or newer.
- A wired-in CSI camera (Module 1 / Module 2 / Module 3 / HQ Camera). Confirm with `libcamera-hello --timeout 2000` before installing anything Free Motion specific.

### Software

```bash
pip install -e .[yolo]
pip install -e .[picam]
```

The `[picam]` extra pins `picamera2>=0.3`. The first time YOLO runs it'll download `yolov8n.pt` (~6 MB) on first construction; subsequent runs use the cached file.

### Run the standalone demo

```bash
python examples/pi_camera_demo/pi_camera_demo.py --interval 0.5
```

See [`examples/pi_camera_demo/README.md`](../examples/pi_camera_demo/README.md) for the full operator walkthrough.

## Failure model (in order)

1. **`picamera2` not installed.** The source's `__init__` catches `ImportError` and logs a single warning. `available is False`. `cam()` returns `None`. The agent loop is unaffected. The standalone demo exits with code `2` rather than crashing.
2. **Camera open fails.** `Picamera2()` raised — typically "camera busy" (another process holds it) or "no camera detected." Same outcome: source offline, `cam()` returns `None`. Verify with `libcamera-hello`; common causes are an unplugged ribbon, an unfinished `apt full-upgrade`, or a stale `picamera2` process from a previous run that didn't clean up.
3. **Configure / start fails.** The source calls `stop()` + `close()` on the partial handle so the camera is released, then flips offline. Without this rollback, the next run would find the camera busy.
4. **Per-call capture fails.** `capture_array()` raised — common during a USB hub power glitch, a brief libcamera state hiccup, or thermal throttling. The current call returns `None`, `capture_failures` increments, the source stays available, the next call retries. `YoloVision` already treats `None` as "no frame this tick" and returns an empty `VisionResult`.
5. **`close()` raises.** Caught and logged. The source's `_closed` flag is set first, so a re-entrant `close()` from a SIGTERM handler is a no-op.

The agent loop never sees a camera-induced exception. That's true at every layer: `PiCameraSource` swallows its own failures, `YoloVision` swallows `frame_source` exceptions one level up, and the agent's `Router.dispatch` catches anything that escapes those.

## Wiring into a closed loop

For Step 1 (this), the demo is standalone — no Telegram, no router, no agent. The full closed loop is the Step 2 demo (`examples/pi_closed_loop_demo/`, not yet shipped):

```text
PiCameraSource ─► YoloVision ─► WorldState.see(label, confidence)
                                       │
                                       ▼
                                Telegram intent ─► GemmaMissionControl.plan(...)
                                                          │
                                                          ▼
                                                     MissionDecision
                                                          │
                                                          ▼
                                                Router.dispatch(Command)
                                                          │
                                                          ▼
                                          SafetyGate(PiHardwareController).move()
                                                          │
                                                          ▼
                                                       /status
```

The architecture is intentionally compositional: every arrow is a Protocol you can swap. `PiCameraSource` is the only piece in this chain that's Pi-specific; `YoloVision` is hardware-agnostic, `WorldState` is in-process, `GemmaMissionControl` runs anywhere `transformers` does, `SafetyGate` is pure logic.

## USB webcams (no wrapper needed)

A USB webcam works fine — but it doesn't need `PiCameraSource`. The seam is the `frame_source` callable, and `cv2.VideoCapture(0).read()` already produces a frame:

```python
import cv2
from freemotion.vision import YoloVision

cap = cv2.VideoCapture(0)

def grab() -> object | None:
    ok, frame = cap.read()
    return frame if ok else None

try:
    vision = YoloVision(
        frame_source=grab,
        classes=["person"],
        confidence_threshold=0.3,
    )
    while True:
        result = vision.scene()
        # ... handle detections ...
finally:
    cap.release()
```

That's the whole thing. No `PiCameraSource`, no `picamera2`, just a 4-line lambda. ADR-0009 is explicit that USB sources stay un-wrapped in v1 — a wrapper would be cargo-culting `PiCameraSource`'s lifecycle onto a backend (v4l2) that doesn't share its quirks. When a second canonical source ships (e.g. an RTSP/MJPEG streamer for off-Pi cameras), ADR-0010 will record the call.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Demo exits immediately with code `2` | `picamera2` not installed, or camera not wired in | `pip install -e .[picam]`; run `libcamera-hello` to confirm camera |
| Demo exits immediately with code `3` | `ultralytics` not installed, or `yolov8n.pt` unreachable | `pip install -e .[yolo]`; run from the repo root so the cached model is found |
| `tick` log shows `cam_failures` climbing | Bad ribbon connection, USB hub brownout, libcamera in a bad state | Reseat the ribbon; reboot the Pi; check `dmesg` for `csi` errors |
| First run works, second run says "camera busy" | A previous demo run left the camera open (rare with `close()` but possible on hard kill) | `pkill -f pi_camera_demo`; if that fails, reboot |
| Person detection latency >1 s per tick | Pi 3B+, or 1080p resolution | Use Pi 4/5; lower resolution to 640×480 (the default) |
| `RuntimeError: Could not create v4l2 instance` | You're running picamera2 against a USB camera | Use `cv2.VideoCapture` instead — picamera2 is CSI-only |

## Where to read next

- [`examples/pi_camera_demo/README.md`](../examples/pi_camera_demo/README.md) — the operator walkthrough.
- [`docs/models.md`](models.md) — `YoloVision` reference (the inference side of this demo).
- [`docs/decisions.md`](decisions.md) — ADR-0007 locks `YoloVision` v1; ADR-0009 locks `PiCameraSource` v1.
- [`docs/pi-runtime.md`](pi-runtime.md) — how `freemotion.{config,router,agent}` compose into a device. Step 2 will integrate the camera path into that runtime.
- [`docs/pi-hardware.md`](pi-hardware.md) — the Pi controller / SafetyGate architecture this will plug into.
