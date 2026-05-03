#!/usr/bin/env bash
# File the M2/M3 issue pack on GitHub.
#
# Run from the repo root, after checking that `gh` is authenticated:
#   gh auth status
#   bash docs/issues/file_issues.sh
#
# Each issue is a separate `gh issue create` block. To file selectively,
# comment out the blocks you don't want. To preview without filing,
# replace `gh issue create` with `cat`.
#
# Labels referenced below assume the repo has them. If `gh issue create`
# complains about a missing label, create it once:
#
#   gh label create m2          --description "Milestone 2"
#   gh label create m3          --description "Milestone 3"
#   gh label create runtime     --description "Runtime / agent / router"
#   gh label create hardware    --description "Hardware adapters"
#   gh label create vision      --description "Vision pipeline"
#   gh label create mission     --description "Mission control"
#   gh label create state       --description "World / runtime state"
#   gh label create example     --description "Example device"

set -euo pipefail

# 1. (was: per-command allow/deny — shipped, ADR-0004) ----------------------
# No issue filed.

# 2. PiHardwareController in freemotion/hardware/ ----------------------------
gh issue create \
  --title "PiHardwareController in freemotion/hardware/" \
  --label "enhancement,m2,hardware" \
  --body-file - <<'EOF'
`MockHardwareController` proves the contract; now ship a real `PiHardwareController` so the Pi can be a first-class device beyond peripherals.

**Scope**

- New class implementing the `HardwareController` Protocol.
- Stub `arm` / `disarm` / `move` until the project picks a motor stack (PWM hat, MAVLink to a flight controller, etc.).
- The first stub can simply log + reply, with a clear TODO and ADR pointer.
- Migration: `examples/pipe_check/` adopts the new controller for `stop` and `status`; the LED handlers stay example-local.

**Acceptance**

- `tests/test_pi_hardware.py` (mocking `RPi.GPIO`).
- `docs/pi-runtime.md` updated to describe the controller-vs-peripheral distinction.
- Separate ADR for the motor-stack pick (deferred — not part of this issue).

Tracked in `docs/issues/m2-m3.md`.
EOF

# 3. YoloVision adapter behind a feature flag --------------------------------
gh issue create \
  --title "YoloVision adapter behind a feature flag" \
  --label "enhancement,m3,vision" \
  --body-file - <<'EOF'
The `VisionBackend` interface and `MockVision` shipped (see `docs/models.md`, ADR-0003). Now ship the first real adapter without making the dependency mandatory.

**Scope**

- `freemotion/vision/yolo.py` exposing `YoloVision` (implements `VisionBackend`).
- Wraps `ultralytics` (or equivalent); imports lazily so the package still loads on machines without the dep.
- v1 detections limited to `person` and a small set of obstacle classes; everything else filtered out.
- New env var `FREEMOTION_VISION_BACKEND=mock|yolo` (default `mock`). Reads in the example wiring, not in the interface.
- `pyproject.toml` gets an optional extra: `pip install -e .[yolo]` pulls the deps.

**Acceptance**

- `tests/test_yolo_vision.py` skips cleanly when `ultralytics` isn't installed; runs end-to-end on a tiny fixture image when it is.
- `docs/models.md` updated with the install + run command.
- ADR for any non-obvious call (e.g. model size, frame size, NMS threshold).

Tracked in `docs/issues/m2-m3.md`.
EOF

# 4. GemmaMissionControl adapter behind a feature flag -----------------------
gh issue create \
  --title "GemmaMissionControl adapter behind a feature flag" \
  --label "enhancement,m3,mission" \
  --body-file - <<'EOF'
The `MissionPolicy` interface and `MockMissionControl` shipped (see `docs/models.md`, ADR-0003). Now ship the first real adapter without making the dependency mandatory.

**Scope**

- `freemotion/mission_control/gemma.py` exposing `GemmaMissionControl` (implements `MissionPolicy`).
- Wraps `transformers` or `llama.cpp`; imports lazily.
- v1 outputs are constrained: parse intent → emit one `CommandName` + args + short reason + confidence. **Not** free-form robotics.
- New env var `FREEMOTION_MISSION_BACKEND=mock|gemma` (default `mock`).
- `pyproject.toml` gets an optional extra: `pip install -e .[gemma]`.

**Acceptance**

- `tests/test_gemma_mission_control.py` skips cleanly when the dep isn't installed; runs offline against a tiny fixture model when it is.
- `docs/models.md` updated.
- ADR for the structured-output strategy (function-call format, regex constrained decoding, etc.).

Tracked in `docs/issues/m2-m3.md`.
EOF

# 5. (was: shared world state — shipped, ADR-0005) --------------------------
# No issue filed.

# 6. (was: examples/mock_follow_task — shipped as local_sim_demo.py) --------
# No issue filed.

echo
echo "Done. Three issues created (or attempted): PiHardwareController, YoloVision, GemmaMissionControl."
echo "Cross-link them in ROADMAP.md."
