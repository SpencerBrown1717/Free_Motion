# Jetson dependency and environment mapping (M5 Phase 1, Step 9)

The companion to [`docs/jetson-phase1.md`](jetson-phase1.md). The phase-1 plan tells you **what** to build; this document tells you **with which dependencies, env vars, camera path, model runtime, and explicit unsupported features**. Read [`jetson-phase1.md`](jetson-phase1.md) first; this is a precise mapping table, not a narrative.

> **Status.** Plan-only. No Jetson code has shipped. Decisions documented here are working assumptions; the binding lock for the Jetson reference is `docs/jetson-reference.md`, written **after** Phase 1 ships ([`jetson-phase1.md` §5 step 10](jetson-phase1.md)).

---

## 1. Dependencies (Jetson Nano)

**Target platform:** NVIDIA Jetson Nano (4 GB), JetPack 4.6.x or 5.x. JetPack 6 is preferred where available — it ships a recent libcamera and a recent Python — but Phase 1 is committed to JetPack 4.6.x compatibility because that's what most Nanos in the wild are running.

### Base install (always required)

| Package | Version range | Why | Source |
|---|---|---|---|
| Python | `>=3.10` | Same minimum as Pi reference (`pyproject.toml`). JetPack 4.6.x ships Python 3.6 — a Phase-1 prerequisite is `pyenv` or a system Python upgrade. | [`pyproject.toml`](../pyproject.toml) |
| `python-telegram-bot` | `>=21,<22` | Telegram transport. Same as Pi. | [`pyproject.toml`](../pyproject.toml) |

### `[jetson]` extra (proposed — locked by ADR-0014 when Phase 1 lands)

| Package | Version range | Notes |
|---|---|---|
| `Jetson.GPIO` | `>=2.1,<3` | The Jetson equivalent of `RPi.GPIO`. NVIDIA-maintained. Lazy-imported in `freemotion/hardware/jetson.py` so non-Jetson hosts stay clean — same discipline as `RPi.GPIO`. |
| `numpy` | matches the JetPack-shipped wheel | Required by `cv2` and YOLO. Pin to whatever the JetPack image already provides; do not bring a fresh PyPI wheel that won't link against the system OpenCV / CUDA. |

### Vision / mission extras

The existing `[yolo]` and `[gemma]` extras work on Jetson **subject to the runtime caveats in §4**. Camera support migrates from `[picam]` to a new `[jetson-camera]` extra (or reuses `cv2.VideoCapture` for USB webcams; see §3).

| Existing extra | On Jetson | Notes |
|---|---|---|
| `pip install -e .[yolo]` | works | Same `ultralytics>=8.0,<9`. Use `yolov8n.pt` for tight memory budgets; the Jetson Nano's 4 GB shared memory rules out `yolov8m.pt` and larger without aggressive offloading. |
| `pip install -e .[gemma]` | partial | `transformers>=4.40,<5` works, but `torch>=2` from PyPI does **not** include CUDA support for Jetson's older Tegra architecture. Use NVIDIA's pre-built PyTorch wheel for the JetPack version. See §4. |
| `pip install -e .[picam]` | **does not apply** | `picamera2` is Pi-specific. Replaced on Jetson by `[jetson-camera]` (proposed) or by `cv2.VideoCapture` for USB. See §3. |

### Proposed new extras (to be added in Phase 1)

```toml
# Proposed addition to pyproject.toml (locked by ADR-0014 when Phase 1 lands).
[project.optional-dependencies]
jetson = [
  "Jetson.GPIO>=2.1,<3",
]
jetson-camera = [
  # Phase 1 chooses ONE of:
  #   - "opencv-python>=4.5"  (USB webcams via cv2.VideoCapture; trivially portable)
  #   - the system-package GStreamer python bindings (requires apt; not pip-installable)
  # Decision lives in §3.
]
```

The naming `jetson-camera` (not `jcamera`) keeps the parallel with `picam` clear: `[picam]` ships `picamera2`; `[jetson-camera]` ships whatever the Jetson camera path turns out to need. Both extras are optional — a deployment using a USB webcam doesn't need `[jetson-camera]` at all.

---

## 2. Environment-variable mapping

Same five-tier structure as [`pi-reference.md` §5](pi-reference.md). Variables that already work on every host (`TELEGRAM_BOT_TOKEN`, `FREEMOTION_DEVICE_ID`, `FREEMOTION_DENIED_COMMANDS`, etc.) are unchanged on Jetson and not re-listed here.

### Required (unchanged from Pi)

| Variable | Notes |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Same purpose, same effect. Empty → `Config.from_env()` raises `SystemExit`. |

### Strongly recommended

| Variable | Pi value | Jetson value | Notes |
|---|---|---|---|
| `FREEMOTION_HARDWARE` | `pi` | `jetson` | The new factory branch. `host` falls back to `MockHardwareController` on either platform. Unknown values warn and fall back to mock — same discipline as Pi. |
| `FREEMOTION_SAFETY_DEFAULT` | `bench` (on rig) / `dry_run` (default) | `bench` (on rig) / `dry_run` (default) | Unchanged. The bench primitive on Jetson is still a GPIO indicator pulse, so `bench` mode keeps the same meaning. |

### Optional — backend selection (unchanged from Pi)

| Variable | Notes |
|---|---|
| `FREEMOTION_VISION_BACKEND` | `mock` or `yolo`. Same factory; the Jetson closed-loop demo passes `frame_source=JetsonCameraSource()` (or a `cv2.VideoCapture`-shaped lambda) through `make_vision_from_config(cfg, frame_source=...)`. |
| `FREEMOTION_MISSION_BACKEND` | `mock` or `gemma`. Unchanged. |

### Optional — pin overrides (new, parallel to Pi)

| Variable | Default | Purpose |
|---|---|---|
| `FREEMOTION_JETSON_ARMED_PIN` | TBD by Phase-1 PR — proposed: `19` (BCM equivalent on the Jetson 40-pin header) | Override the armed indicator pin. **Default chosen during PR review based on the dev-kit pinout; locked in ADR-0014.** |
| `FREEMOTION_JETSON_MOVING_PIN` | TBD by Phase-1 PR — proposed: `21` | Override the moving indicator pin. Same rule. |

The Pi pin overrides (`FREEMOTION_PI_ARMED_PIN`, `FREEMOTION_PI_MOVING_PIN`) **continue to exist and continue to map to the Pi controller**. A Jetson deployment ignores them; a Pi deployment ignores the Jetson ones. The factory dispatches to the right controller based on `FREEMOTION_HARDWARE` and reads only the relevant pin overrides.

### Optional — closed-loop / benchmark demo only (unchanged from Pi)

| Variable | Notes |
|---|---|
| `FREEMOTION_LOG_LEVEL` | Same. |
| `FREEMOTION_MISSION_TICK_INTERVAL_S` | Same default (`1.0`). Jetson's GPU lets you go lower (e.g. `0.25`) with a larger YOLO model; tuning concern, not a contract change. |
| `FREEMOTION_DEFAULT_INTENT` | Same. |

### `~/.config/freemotion.env` example (Jetson)

The Jetson template mirrors the Pi template at [`examples/pi_closed_loop_demo/README.md`](../examples/pi_closed_loop_demo/README.md); only the hardware variables and (optionally) pin overrides change.

```bash
# ~/.config/freemotion.env on a Jetson Nano with the closed-loop demo.
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=12345678,87654321

FREEMOTION_HARDWARE=jetson
FREEMOTION_SAFETY_DEFAULT=bench
FREEMOTION_VISION_BACKEND=yolo
FREEMOTION_MISSION_BACKEND=gemma

# Jetson pin overrides — defaults are locked once Phase 1 lands.
# FREEMOTION_JETSON_ARMED_PIN=19
# FREEMOTION_JETSON_MOVING_PIN=21

FREEMOTION_DEVICE_ID=jetson-bench-01
FREEMOTION_LOG_LEVEL=INFO
FREEMOTION_MISSION_TICK_INTERVAL_S=0.5
FREEMOTION_DEFAULT_INTENT=follow person
```

---

## 3. Camera path differences

The Pi reference uses `picamera2` (libcamera-based) at `(640, 480)` ([`pi-reference.md` §3](pi-reference.md), [`pi-camera.md`](pi-camera.md), [ADR-0009](decisions.md)). On Jetson, the camera path is a deliberate choice between two options. Phase 1 picks **one**; the choice is locked in ADR-0014.

### Option A — `JetsonCameraSource` via GStreamer (recommended for Pi-style CSI cameras)

A new `freemotion/vision/jetson_camera.py` mirroring `picamera.py`'s structure:

- Callable like a frame producer: `cam()` returns the latest frame as `np.ndarray`, or `None` on any failure.
- Backed by GStreamer through `cv2.VideoCapture(<pipeline>, cv2.CAP_GSTREAMER)`. The pipeline string targets `nvarguscamerasrc` for the IMX219 (the canonical Jetson Nano CSI camera).
- Same lazy-import discipline as `PiCameraSource`: `cv2` import inside `__init__`; construction failures flip `available=False`; per-call failures return `None` and increment `capture_failures`; `close()` is idempotent.
- Resolution fixed at construction (`(640, 480)` default — same as Pi for parity, even though the Jetson GPU could carry more). Larger resolutions are a tuning concern once Phase 1 ships.

Tradeoffs vs. `PiCameraSource`:

- **Pro:** lifecycle quirks identical to Pi (lazy import, fail-offline, per-call failure tolerance, idempotent close). Same testing discipline (`_FakeCamera`).
- **Pro:** GStreamer pipelines are the canonical Jetson camera path; the operator experience matches every other Jetson tutorial.
- **Con:** GStreamer python bindings (`gi.repository.Gst`) and `nvarguscamerasrc` are system packages, not pip-installable. The `[jetson-camera]` extra cannot fully express the dependency; the operator must `apt install` the GStreamer prerequisites per `docs/jetson-setup.md` (Phase-1 deliverable).
- **Con:** the test fake (`_FakeCamera`) cannot exercise the real `nvarguscamerasrc` path on a non-Jetson host. Same constraint `tests/test_pi_camera_source.py` already accepts for `picamera2` — the smoke test is a `pytest.importorskip("cv2")` plus a synthetic frame, and real hardware is verified by `pi_follow_bench --mode=bench`.

### Option B — `cv2.VideoCapture(0)` lambda (recommended for USB webcams)

The same path the Pi reference recommends for non-CSI cameras: a one-line lambda passed to `make_vision_from_config(cfg, frame_source=lambda: ret_frame())`.

- **Pro:** zero new code. The existing `YoloVision(frame_source=...)` seam already accepts this.
- **Pro:** trivially portable across hardware.
- **Con:** USB webcams have higher per-frame latency than CSI cameras and consume USB bandwidth that may be scarce on a Jetson Nano with other peripherals.

### Phase 1 recommendation

**Ship Option A as the default `JetsonCameraSource` (because it matches the Pi reference's lifecycle discipline) and document Option B as the supported alternative for USB webcams (because it requires zero new code).** Same posture the Pi reference takes for `PiCameraSource` vs. USB webcams. The choice is locked in ADR-0014 when Phase 1 lands.

### Frame format compatibility

Both options return `np.ndarray` in BGR with shape `(H, W, 3)`, identical to what `picamera2.capture_array()` returns on the Pi. `YoloVision` handles BGR/RGB via `ultralytics`'s normalization, so neither path needs a colorspace shim. Same contract on every platform.

### Camera-related failure modes

The four failure modes in [`docs/pi-failure-modes.md`](pi-failure-modes.md) (camera unplugged, bad frames, partial init, capture failure) all have analogs on Jetson:

| Pi failure | Jetson analog | `JetsonCameraSource` behavior |
|---|---|---|
| `picamera2` not installed | `cv2` not installed / `nvarguscamerasrc` not present | `available=False` at construction; demo exits `2`. |
| Pi camera unplugged at boot | CSI cable disconnected | `pipeline.open()` fails; `available=False`. |
| `capture_array` raises mid-loop | `cap.read()` returns `(False, None)` | `cam()` returns `None`; `capture_failures` increments; loop sees no detection; world goes stale per [ADR-0011](decisions.md). |
| Pi camera busy | another process already holds the CSI handle | construction fails offline; demo exits `2`. |

Same four-row table the Pi failure-modes doc uses; same observable behavior. The contract holds.

---

## 4. Model runtime differences

### YOLO (`ultralytics` + PyTorch)

| Concern | Pi reference | Jetson Phase 1 |
|---|---|---|
| Default weights | `yolov8n.pt` (~6 MB) | `yolov8n.pt` (same) — locked default. Larger weights (`yolov8s.pt`, `yolov8m.pt`) are a tuning concern, not a contract change. |
| Inference backend | `torch` CPU on Pi 4 / 5 (~200ms / frame) | `torch` CUDA on Jetson Nano (~30–60ms / frame with `yolov8n.pt`) **but only if** PyTorch is installed from NVIDIA's pre-built wheel. PyPI's `torch` does not include CUDA support for Jetson's Tegra architecture. |
| `min_interval_s` | `0.0` (no throttle) by default | `0.0` works; the Jetson's faster inference may make a small throttle (e.g. `0.05`) useful for keeping `WorldState` updates in sync with mission ticks. Operator-tunable; not a contract change. |
| `confidence_threshold` | `0.25` (Ultralytics default) | Same. |

**Critical: PyTorch installation on Jetson.** The PyPI `torch>=2` wheel does **not** ship CUDA support for Jetson. The operator must install NVIDIA's pre-built wheel that matches the JetPack version (e.g. NVIDIA's `torch-1.11.0a0` for JetPack 4.6.x). The `[gemma]` extra in `pyproject.toml` declares `torch>=2`, which means a fresh `pip install -e .[gemma]` on a Jetson will install a CPU-only `torch` that runs but is ~10x slower than the JetPack wheel. **`docs/jetson-setup.md` must walk the operator through replacing the PyPI wheel with NVIDIA's wheel before running the closed-loop demo.** This is a documentation responsibility, not a code one — `pip` cannot express "use NVIDIA's wheel" portably.

### Gemma (`transformers` + PyTorch)

| Concern | Pi reference | Jetson Phase 1 |
|---|---|---|
| Default model | `google/gemma-2-2b-it` (~5 GB) | Same on Jetson Nano 4 GB **only with quantization**. The Nano's 4 GB shared memory cannot fit `gemma-2-2b-it` in fp16 alongside YOLO. Use `bitsandbytes` 8-bit quantization or `transformers`'s `load_in_4bit` (BitsAndBytes integration). Decision locked in ADR-0014. |
| Inference latency | ~3–5s per `plan()` call on Pi 5 (CPU-only) | ~1–2s per `plan()` call on Jetson Nano with quantization (CUDA-accelerated) |
| `max_new_tokens` | `128` default | Same. |
| `temperature` | `0.1` default | Same. |
| Fail-offline behavior | adapter `available=False` if model load fails | Same. The fallback chain in [`pi-reference.md` §4](pi-reference.md) is platform-agnostic. |

### Inference threading

The mission loop calls `mission.plan()` synchronously per tick and is hardened against hangs ([ADR-0011](decisions.md), [`pi-failure-modes.md`](pi-failure-modes.md)). The Jetson's CUDA pipeline can produce harder-to-reproduce hangs (CUDA OOM mid-inference with no Python exception, kernel queue stalls, etc.), but the mitigation is the same: `MissionLoop.stop()` does not depend on `plan()` returning, so a hung tick cannot block `/stop`. Phase 1 does not need to add new hung-tick handling.

### Loop tick interval recommendations

| Target | Recommended `FREEMOTION_MISSION_TICK_INTERVAL_S` |
|---|---|
| Pi 4 (CPU YOLO + Gemma 2-2b) | `1.0` (default — anything lower starves) |
| Pi 5 (CPU YOLO + Gemma 2-2b) | `0.5` (tunable, headroom available) |
| Jetson Nano (CUDA YOLO + quantized Gemma 2-2b) | `0.5` (default for Phase 1) |

Tick interval is not a contract surface; it's an operator knob, recorded per-run in the `pi_follow_bench` artifact's `config_summary` ([`pi-benchmark.md` §3](pi-benchmark.md)).

---

## 5. Unsupported features (explicit, by design)

These features are **not** part of Jetson Phase 1. Each is listed with the status it has in the broader roadmap, so a contributor reading this doc knows whether the feature is "deferred to Phase 2," "deferred to a separate ADR," or "out of scope at the protocol level."

### Hardware-level

| Feature | Phase 1 status | Reason / where it belongs |
|---|---|---|
| Jetson Orin / AGX support | Not Phase 1 | Same code (Protocol is identical) but tuning and packaging may differ. Phase 1 targets the Nano specifically because it's the cheapest member of the family that runs the full `[yolo,gemma]` stack. |
| Jetson Xavier NX support | Not Phase 1 | Same reason. |
| ESP32 / Arduino | Not Phase 1 | M5 Phase 2 / 3. Different SoC class — micro-controllers, not Linux SBCs. Will get separate ADRs. |
| Multi-camera Jetson rigs | Not Phase 1 | The Pi reference is one camera ([`pi-reference.md` §3](pi-reference.md)). Multi-camera is a `frame_source` design problem, not a Jetson problem. |
| I²C / SPI / UART / PWM | Not Phase 1, possibly never on Phase 1 reference | Out of scope on the Pi reference too ([`pi-reference.md` §3](pi-reference.md)); Phase 1 mirrors that. Real motion primitives that need these will land via a separate ADR after Phase 1's contract is verified. |
| External GPS / IMU / encoders | Not Phase 1 | World state is shaped today around perception ([ADR-0005](decisions.md)). Sensor fusion is a future ADR, not Phase 1. |
| Custom motor drivers / autopilot link | Not Phase 1 | Phase 1 exposes `move(x, y, z)` against a bench primitive (GPIO indicator pulse). Real motors are a separate roadmap item. |

### Software-level

| Feature | Phase 1 status | Reason / where it belongs |
|---|---|---|
| Operator authentication, allow lists | Not Phase 1 | Same status as on the Pi ([release notes for v0.2.0](releases/v0.2.0.md)). |
| Rate limits, watchdogs, link-loss fail-safe | Not Phase 1 | Same status as on the Pi. |
| Multi-device fan-out (`to` field) | Not Phase 1 | Reserved at the protocol level; not implemented on any platform. |
| Second transport (HTTP / MQTT / gRPC) | Not Phase 1 | Same status as on the Pi. |
| Hosted Jetson CI runner | Not Phase 1 | No hosted runner exists. Bench-mode `pi_follow_bench` (or `jetson_follow_bench`) is the operator's responsibility. CI continues to run `--mode=ci` only. |
| Auto-recovery of camera handle after USB unplug | Not Phase 1 | Deliberately deferred on the Pi too ([ADR-0011](decisions.md)). Service restart is the supported recovery path. |
| New protocol commands | Not Phase 1 | Phase 1 ships zero protocol changes. Any new command requires a protocol bump per [ADR-0002](decisions.md), which is a separate ADR. |

If a feature you care about is on this list, it's not blocking Phase 1 — file an issue against the relevant milestone and continue with the bring-up.

---

## 6. Verification — what tests/checks Phase 1 owes

Each line below maps to one `git grep`-able artifact that proves Step 9's promises were kept once Phase 1 lands.

| Promise | Verifying artifact |
|---|---|
| `Jetson.GPIO` lazy-imports | `tests/test_jetson.py` runs on a non-Jetson host with a `_FakeJetsonGPIO` injected — same discipline as `tests/test_pi.py`. |
| Hardware factory dispatches `FREEMOTION_HARDWARE=jetson` to `JetsonHardwareController` | `tests/test_hardware_factory.py` extended with a Jetson branch test. |
| Pin override env vars round-trip | `tests/test_config.py` extended with `FREEMOTION_JETSON_ARMED_PIN` / `FREEMOTION_JETSON_MOVING_PIN` cases. |
| `JetsonCameraSource` lazy-imports `cv2` and fails offline cleanly | `tests/test_jetson_camera_source.py` mirrors `tests/test_pi_camera_source.py`. |
| `examples/jetson_closed_loop_demo/` registers exactly the locked 8-command surface | `tests/test_jetson_closed_loop_demo.py` mirrors `tests/test_pi_closed_loop_demo.py`. |
| `pi_follow_bench --mode=bench` produces a passing artifact on real Jetson | Manual operator run + committed artifact at `docs/releases/jetson-phase1-bench-artifact.json` per [`jetson-phase1.md` §9](jetson-phase1.md). |
| `docs/jetson-setup.md` walks the operator through every system-level prerequisite (Python upgrade, NVIDIA PyTorch wheel, GStreamer apt packages if Option A wins) | The doc itself, structured to mirror `docs/pi-setup.md`. |

Phase 1's CI smoke job extends to import-smoke `from freemotion.hardware.jetson import JetsonHardwareController` and `import jetson_closed_loop_demo` on every push. CI does **not** attempt to run `pi_follow_bench` against Jetson code paths — there's no Jetson runner — but the existing `pi_follow_bench --mode=ci` job continues to pass against the Pi reference path on every push, which is what catches contract regressions that would also affect the Jetson port.

---

## 7. Open questions for Phase 1's PR review

Each of the following is an explicit decision point that lands in ADR-0014 with the Phase-1 commit. They're listed here so the PR reviewer doesn't have to rediscover them.

1. **Camera path:** Option A (`JetsonCameraSource` via GStreamer) vs. Option B (`cv2.VideoCapture` lambda). §3 recommends Option A as the default and Option B as the supported alternative. Lock the choice in ADR-0014.
2. **Default Jetson pin numbers:** `19`/`21` are proposed in §2. The actual choice depends on whether the deployed Nano dev kit exposes any conflicting peripherals on those pins. Confirm during Phase-1 bring-up; record in ADR-0014.
3. **Gemma quantization choice:** `bitsandbytes` 8-bit vs. `load_in_4bit`. §4 leaves this open. Likely 8-bit (better quality, fits comfortably) but verify on real hardware first.
4. **`docs/jetson-setup.md` vs. inline notes in `examples/jetson_closed_loop_demo/README.md`:** The Pi pattern is two docs (setup + demo readme). Phase 1 mirrors that. Do not consolidate into one file — it makes the operator experience non-parallel with the Pi.
5. **`[jetson]` vs. `[jetson-camera]` extras separation:** Confirm in Phase-1 PR review that splitting these is preferable to one combined `[jetson-all]` extra. The Pi pattern keeps `[picam]` separate from any future `[pi-hardware]` extra; we'd be following that precedent.

---

## 8. Move-to-code rule

You start writing Jetson code only when:

1. [`docs/jetson-phase1.md`](jetson-phase1.md) (Step 8) is merged.
2. **This document** (Step 9) is merged.
3. Real Jetson Nano hardware is available for the bring-up team (see [`jetson-phase1.md` §8 risks](jetson-phase1.md)).

Until all three are true, Phase 1 stays a documentation milestone. The Pi-first lockdown shipped specifically to make Phase 1 deterministic; rushing the code before the docs would forfeit that determinism.

---

## Related

- [`docs/jetson-phase1.md`](jetson-phase1.md) — Phase 1 plan (Step 8). **Read first.**
- [`docs/pi-reference.md`](pi-reference.md) — the parent contract.
- [`docs/pi-camera.md`](pi-camera.md) — the camera path this doc maps from.
- [`docs/pi-failure-modes.md`](pi-failure-modes.md) — the failure model this doc maps from.
- [`docs/pi-setup.md`](pi-setup.md) — the setup template `docs/jetson-setup.md` will mirror.
- [`docs/decisions.md`](decisions.md) — ADR ledger; ADR-0014 will lock the Jetson Phase 1 design rationale.
- [`docs/releases/v0.2.0.md`](releases/v0.2.0.md) — what was locked in the Pi-first lockdown release that gates Jetson work.
