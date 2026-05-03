#!/usr/bin/env python3
"""Free Motion pi_camera_demo (Step 1 — Pi live camera integration).

The first **real perception** Free Motion demo. Wires:

    PiCameraSource (picamera2)  ->  YoloVision (ultralytics)
        ->  printed detections  ->  loop

This demo is intentionally narrower than `examples/pi_bench_demo/`:
no Telegram, no router, no agent loop, no hardware controller. It
proves the live-camera + YOLO integration in isolation. The full
closed loop (Telegram -> YOLO -> world -> Gemma -> hardware ->
status) is the next step (`examples/pi_closed_loop_demo/`).

Acceptance criteria for Step 1 (covered by this demo):

- A Pi can provide live frames continuously (PiCameraSource).
- `YoloVision.scene()` runs on real Pi camera input.
- Person detection works on live input.
- Failure to read frames does not crash the runtime — the loop keeps
  going, the source's `capture_failures` counter increments, and a
  warning is logged.
- The camera capture path never holds a global lock, so a future
  closed-loop demo can read `/status` while the camera is active.

Setup is documented in `examples/pi_camera_demo/README.md` and
`docs/pi-camera.md`. The short version:

    pip install -e .[yolo]
    pip install -e .[picam]      # Pi only — picamera2 is not on macOS
    python examples/pi_camera_demo/pi_camera_demo.py --interval 0.5

If `picamera2` or `ultralytics` is missing, the demo logs a clear
warning and exits with a non-zero code rather than crashing.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from types import FrameType
from typing import Optional

from freemotion.vision import (
    Detection,
    PiCameraSource,
    VisionResult,
    YoloVision,
)

LOG = logging.getLogger("freemotion.pi_camera_demo")

# Capture the YOLO defaults at import time as module-level constants.
# argparse reads these at `main()` time; capturing them now keeps the
# CLI's defaults stable even when tests monkeypatch `YoloVision`
# itself with a stub.
DEFAULT_CONFIDENCE: float = YoloVision.DEFAULT_CONFIDENCE
DEFAULT_MODEL: str = YoloVision.DEFAULT_MODEL


_running: bool = True


def _handle_sigint(_signum: int, _frame: Optional[FrameType]) -> None:
    global _running
    _running = False
    LOG.info("received SIGINT; shutting down")


def _format_detection(det: Detection) -> str:
    x, y, w, h = det.bbox
    return (
        f"{det.label:<10s} conf={det.confidence:.2f} "
        f"bbox=({x:.2f},{y:.2f},{w:.2f},{h:.2f})"
    )


def _summarize_scene(result: VisionResult) -> str:
    if not result.detections:
        return "no detections"
    parts = [_format_detection(d) for d in result.detections]
    return "; ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Free Motion pi_camera_demo — live Pi camera + YOLO loop"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Seconds to sleep between scene() calls (default: 0.5)",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help="YOLO confidence threshold (default: 0.25)",
    )
    parser.add_argument(
        "--classes",
        default="person",
        help=(
            "Comma-separated class list (default: 'person'); "
            "pass an empty string to accept every class"
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"YOLO model file (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--width", type=int, default=640, help="Camera width (default: 640)"
    )
    parser.add_argument(
        "--height", type=int, default=480, help="Camera height (default: 480)"
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=0,
        help="Exit after this many successful scenes (0 = run forever)",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python logging level (default: INFO)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]

    cam = PiCameraSource(resolution=(args.width, args.height))
    if not cam.available:
        LOG.error(
            "PiCameraSource is offline. Install picamera2 with "
            "`pip install -e .[picam]` on a Raspberry Pi running "
            "Bullseye or newer, and confirm the camera is wired in."
        )
        return 2

    vision = YoloVision(
        frame_source=cam,
        model=args.model,
        classes=classes if classes else [],
        confidence_threshold=args.confidence,
        min_interval_s=0.0,
    )
    if not vision.available:
        LOG.error(
            "YoloVision is offline. Install ultralytics with "
            "`pip install -e .[yolo]` and confirm the model file "
            "%r is reachable.",
            args.model,
        )
        cam.close()
        return 3

    LOG.info(
        "pi_camera_demo started: model=%s, classes=%s, conf=%.2f, "
        "resolution=%dx%d, interval=%.2fs",
        args.model,
        classes or ["<all>"],
        args.confidence,
        args.width,
        args.height,
        args.interval,
    )

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    ticks = 0
    detections_total = 0
    try:
        while _running:
            t0 = time.monotonic()
            try:
                result = vision.scene()
            except Exception as exc:
                # YoloVision already swallows its own inference errors,
                # but defend the loop one more level out so a surprise
                # never escapes.
                LOG.warning("vision.scene raised unexpectedly: %s", exc)
                time.sleep(args.interval)
                continue

            ticks += 1
            detections_total += len(result.detections)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            LOG.info(
                "tick=%d elapsed=%.0fms cam_failures=%d -> %s",
                ticks,
                elapsed_ms,
                cam.capture_failures,
                _summarize_scene(result),
            )

            if args.max_ticks and ticks >= args.max_ticks:
                LOG.info("max_ticks reached; exiting")
                break

            time.sleep(max(0.0, args.interval))
    finally:
        LOG.info(
            "pi_camera_demo stopping: ticks=%d, total_detections=%d, "
            "camera_failures=%d",
            ticks,
            detections_total,
            cam.capture_failures,
        )
        cam.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
