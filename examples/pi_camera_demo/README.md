# pi_camera_demo (Step 1 — Pi live camera integration)

The **first real perception** Free Motion demo. A Raspberry Pi captures live frames through `picamera2`, hands them to `YoloVision`, and prints person detections to the console in a tight loop.

This demo is **standalone**. It does not run Telegram, the router, the agent, the world state, mission control, or the hardware controller. It only proves the Pi camera + YOLO pair works end-to-end on real hardware. The full closed loop (`Telegram → YOLO → world → Gemma → hardware → status`) is the next step (Step 2).

## What the demo supports

```text
PiCameraSource (picamera2)  ─►  YoloVision (ultralytics)  ─►  detections
                                       │
                                       ▼
                              tick=N elapsed=Xms cam_failures=K -> person conf=0.92 bbox=(...)
```

- Live frames from a CSI Pi camera (Module 1, Module 2, Module 3, HQ).
- Person detection by default (`--classes person`); pass `--classes ""` to accept every COCO class.
- Loop runs forever (`Ctrl+C` exits cleanly) or for `--max-ticks N` runs.
- Camera read failures are logged, counted, and the loop continues. One bad frame does **not** flip the source offline.
- Uses a tiny SIGINT/SIGTERM handler so `systemctl --user stop` and `Ctrl+C` shut down cleanly.

## What you need

- Raspberry Pi 4 or 5 (3B+ technically works but YOLO inference is slow on it).
- Raspberry Pi OS **Bookworm** or newer (older releases shipped legacy `picamera`, not `picamera2`).
- A wired-in CSI camera (ribbon-cable connected; check `libcamera-hello` works first).
- Python 3.10+.

USB webcams are **not** supported by this demo, but they're easy to plug in: skip `PiCameraSource` and pass `cv2.VideoCapture(0).read`-shaped callable into `YoloVision(frame_source=...)` directly. See `docs/pi-camera.md` for the snippet.

## 1. Confirm the camera is wired correctly

Before installing anything Free Motion specific:

```bash
libcamera-hello --timeout 2000
```

If you see frames in the preview window, the camera is wired right and Bookworm's `libcamera` stack is happy. If you see "Could not open camera" or similar, fix that first — `picamera2` will not work either.

## 2. Install

From the repo root on the Pi:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[yolo]
pip install -e .[picam]
```

The first `pip install` pulls `ultralytics` (and `torch`). On a Pi, that's a heavy install (~1–2 GB on disk and several minutes); be patient. The second adds `picamera2`.

The first time YOLO is constructed, `ultralytics` will download `yolov8n.pt` (~6 MB) into the working directory. Subsequent runs use the cached file.

## 3. Run it

```bash
python examples/pi_camera_demo/pi_camera_demo.py --interval 0.5
```

You should see one log line per tick:

```
2026-05-04 12:34:56 INFO freemotion.pi_camera_demo:
    pi_camera_demo started: model=yolov8n.pt, classes=['person'], conf=0.25,
    resolution=640x480, interval=0.50s
2026-05-04 12:34:57 INFO freemotion.pi_camera_demo:
    tick=1 elapsed=210ms cam_failures=0 -> no detections
2026-05-04 12:34:58 INFO freemotion.pi_camera_demo:
    tick=2 elapsed=205ms cam_failures=0 -> person     conf=0.91 bbox=(0.31,0.18,0.27,0.62)
```

Step in front of the camera. The `tick` line should report a `person` detection within a few seconds.

`Ctrl+C` exits cleanly:

```
2026-05-04 12:35:21 INFO freemotion.pi_camera_demo: received SIGINT; shutting down
2026-05-04 12:35:21 INFO freemotion.pi_camera_demo:
    pi_camera_demo stopping: ticks=42, total_detections=27, camera_failures=0
```

## All flags

| Flag | Default | Notes |
|---|---|---|
| `--interval` | `0.5` | Seconds to sleep between `scene()` calls. The actual cadence is `interval + capture + inference` (typically ~200–400 ms on a Pi 4). |
| `--confidence` | `0.25` | YOLO confidence threshold, forwarded to `model(..., conf=...)`. |
| `--classes` | `person` | Comma-separated class list. Empty string accepts every class (chair, dog, etc.). |
| `--model` | `yolov8n.pt` | Override only if you've placed a different ultralytics-compatible weight file in the working directory. |
| `--width` / `--height` | `640` / `480` | Camera resolution. Higher resolutions produce slower inference. |
| `--max-ticks` | `0` | If non-zero, exit cleanly after this many successful scenes. Useful for benchmarking. |
| `--log-level` | `INFO` | Standard Python logging level. |

## Behaviors to verify on the bench

- **Loop runs continuously.** Multiple `tick=N` lines without crashing prove `PiCameraSource()` is producing frames and `YoloVision.scene()` is consuming them.
- **Person detection works.** Step in front of the camera; `tick` lines should report `person` with `conf >= 0.25`.
- **Camera failures don't crash the loop.** Pop the ribbon cable mid-run. The next few ticks will log `Pi camera capture failed: ...`, `cam_failures` will increment, and the loop will keep going. Replace the cable; ticks resume reporting normal results.
- **YOLO offline doesn't crash the loop.** Move `yolov8n.pt` aside and re-run. The demo logs `YoloVision is offline` and exits with code `3` rather than crashing.
- **Camera offline doesn't crash the loop.** Run on a Pi without `picamera2` installed (or with the camera unplugged before start). The demo logs `PiCameraSource is offline` and exits with code `2`.
- **Ctrl+C closes the camera.** The "pi_camera_demo stopping" log line indicates the SIGINT path ran. The `picamera2` driver releases the camera; you can re-run the demo without rebooting.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean exit (max-ticks reached, or SIGINT during loop). |
| `2` | `PiCameraSource` offline at startup (picamera2 missing, or camera not wired in). |
| `3` | `YoloVision` offline at startup (ultralytics missing, or model file unreachable). |

## Autostart with systemd (optional)

Drop the unit file into the user systemd path:

```bash
mkdir -p ~/.config/systemd/user
cp examples/pi_camera_demo/systemd/freemotion-pi-camera-demo.service \
   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now freemotion-pi-camera-demo.service
journalctl --user -u freemotion-pi-camera-demo.service -f
```

The unit assumes the repo lives at `~/src/Free_Motion` with a venv at `.venv`. Adjust paths in the unit file if your layout differs.

## Comparison with the other demos

| Demo | What it proves | Hardware needed |
|---|---|---|
| [`local_sim_demo.py`](../local_sim_demo.py) | Mock vision + mock mission + mock controller close the loop end-to-end. | None. |
| [`mock_drone/`](../mock_drone/) | Telegram + the agent + the router on mock hardware. | Telegram bot token. |
| [`pipe_check/`](../pipe_check/) | First-byte Pi pipe check, optional GPIO LED. | Pi (LED optional). |
| [`pi_bench_demo/`](../pi_bench_demo/) | First **real hardware** Free Motion device — full Telegram-driven runtime over `PiHardwareController` + `SafetyGate`. | Pi + 2 LEDs. |
| **`pi_camera_demo/` (this)** | First **real perception** — `PiCameraSource` + `YoloVision` on live Pi camera input. | Pi 4 or 5 + CSI camera. |

The next demo (`pi_closed_loop_demo/`, Step 2) merges this one with `pi_bench_demo/`: live YOLO runs in the agent loop, detections feed `WorldState`, `GemmaMissionControl` picks the next action, and the hardware controller (gated by `SafetyGate`) executes a single bench-safe primitive.

## Where to read next

- [`docs/pi-camera.md`](../../docs/pi-camera.md) — canonical Pi-camera adapter documentation, USB webcam alternative, troubleshooting.
- [`docs/models.md`](../../docs/models.md) — `YoloVision` reference (the inference side of this demo).
- [`docs/pi-runtime.md`](../../docs/pi-runtime.md) — how `freemotion.{config,router,agent}` compose into a device.
- [`docs/decisions.md`](../../docs/decisions.md) — ADR-0007 (`YoloVision` v1) and ADR-0009 (`PiCameraSource` v1).
