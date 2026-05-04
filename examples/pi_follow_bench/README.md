# pi_follow_bench — the named Pi benchmark (Step 5)

The single repeatable Pi benchmark task. Drives the locked Pi reference architecture ([`docs/pi-reference.md`](../../docs/pi-reference.md)) through a fixed 10-step command sequence and emits a stable JSON artifact for each run. Lets you say "the device passed `pi_follow_bench`" and have that mean exactly the same thing every time.

> **Frozen.** The 10-step sequence, the success criteria, and the JSON artifact schema are part of the locked Pi contract. See [`docs/pi-benchmark.md`](../../docs/pi-benchmark.md) for the protocol; see [ADR-0013](../../docs/decisions.md) for the design rationale.

## What it is

A CLI that:

1. Wires the **same stack** [`pi_closed_loop_demo`](../pi_closed_loop_demo/) wires (`Config.from_env` → `PiCameraSource` → `YoloVision` → `WorldState` → `GemmaMissionControl` → `SafetyGate` → `PiHardwareController`) and the same `MissionLoop`.
2. Dispatches a fixed sequence of 10 commands directly through the router (no Telegram needed — the benchmark is reproducible).
3. Watches the loop tick during a documented hold window.
4. Applies the frozen pass/fail criteria.
5. Writes a structured JSON artifact you can diff against previous runs.

It is **not** a unit test, **not** a load test, and **not** a model-quality benchmark. It is a contract benchmark: the same locked surfaces pass the same checks every time the runtime ships.

## Frozen sequence

| # | Step | Purpose |
|---|---|---|
| 1 | `/ping` | Round-trip liveness check. |
| 2 | `/capabilities` | Exact 8-command surface match against [`docs/pi-reference.md` §2](../../docs/pi-reference.md). |
| 3 | `/status` (initial) | Loop=idle, controller=idle, no failures. |
| 4 | `/arm` | Drive `armed_pin` HIGH. |
| 5 | `/mission_start <intent>` | Start the loop. Default intent: `follow person`. |
| 6 | observe (sleep `--hold`) | Let the loop tick. |
| 7 | `/status` (mid-mission) | Loop=running, `tick_count >= --min-loop-ticks`, `last_dispatched=move` (clean run only). |
| 8 | `/stop` | Master kill — loop stops, pins LOW. |
| 9 | `/disarm` | Idempotent after `/stop`; must still ack. |
| 10 | `/status` (final) | Loop=idle, controller=idle, no unexpected failures. |

The sequence is **identical** under every `--inject` mode. What changes is the expected outcome — see "Failure modes" below.

## Frozen success criteria

A run passes only when **every** flag below is true:

- `all_commands_ok` — every dispatched command returned `ok=true`.
- `capabilities_match_locked_surface` — `/capabilities` reports exactly the 8 commands locked in §2 of [`docs/pi-reference.md`](../../docs/pi-reference.md).
- `loop_reached_running` AND `loop_ticks_met` — at step 7, `mission_loop.running=true` and `tick_count >= --min-loop-ticks` (default 1).
- `min_move_dispatches_required` — when `--min-move-dispatches=1`, step 7 reports `last_dispatched=move` with `last_dispatch_ok=true`.
- `loop_stopped_clean` — `/stop` returned `ok` and step 10 reports `mission_loop.running=false`.
- `pins_low_at_end` — step 10 reports `controller.armed=false`.
- `no_unexpected_failures` — failure counters are within the bounds documented for the active `--inject` mode (zero for a clean run; bounded for each named inject).

The boolean rollup is `success=true` IFF every flag is true. The artifact carries each flag and each counter, so a failed run tells you exactly which contract regressed.

## Two modes

### `--mode=ci` — deterministic mock harness

No Pi, no camera, no models, no env vars. Wires:

- `MockHardwareController()` + `SafetyGate(safety_default=bench)`
- `MockVision(scripted=[<one person scene>])` (or a fault-injecting variant under `--inject`)
- `MockMissionControl()` (or `_OfflineMission` under `--inject=mission_offline`)
- `WorldState`, `MissionLoop`, the same router as the Pi reference

A clean run completes in ~1s on a CI runner. The CI workflow runs this on every push.

### `--mode=bench` — real Pi rig (the canonical benchmark)

Calls `Config.from_env()` and wires the real adapters exactly the way `pi_closed_loop_demo.main()` does. **Run this on a Pi bench rig** with:

- A camera attached and `picamera2` installed (or set `--inject=camera_offline` if you want to test the offline path).
- `~/.config/freemotion.env` configured the same way `pi_closed_loop_demo` reads it (`FREEMOTION_HARDWARE=pi`, `FREEMOTION_SAFETY_DEFAULT=bench`, `FREEMOTION_VISION_BACKEND=yolo`, `FREEMOTION_MISSION_BACKEND=gemma`, etc. — see [`docs/pi-reference.md` §5](../../docs/pi-reference.md)).
- An operator standing in front of the camera (or a stand-in object the YOLO model recognizes as `person`).

The benchmark is bench-safe: the only motion primitive is the bench `moving_pin` HIGH-pulse. No motors, no propellers. Same safety guarantees as the canonical demo (see [`docs/pi-reference.md` §6](../../docs/pi-reference.md)).

## Run it

### CI mode (laptop, fast)

```bash
# from the repo root
python examples/pi_follow_bench/pi_follow_bench.py run \
  --mode=ci --hold=0.3 --tick-interval=0.05 \
  --min-loop-ticks=2 --min-move-dispatches=1 \
  --print-human
```

You'll see:

```
pi_follow_bench — PASS
  run_id:       <uuid>
  mode:         ci
  inject:       (none)
  ...
verdict: PASS
```

The artifact is written to `~/.cache/freemotion/results/pi_follow_bench-ci-<utc-stamp>.json`. Pass `--output -` to print to stdout instead.

### Bench mode (real Pi)

```bash
# on the Pi, after ~/.config/freemotion.env is set up
source ~/src/Free_Motion/.venv/bin/activate
cd ~/src/Free_Motion
python examples/pi_follow_bench/pi_follow_bench.py run \
  --mode=bench --hold=10 --print-human
```

Stand in front of the camera during the 10-second observation window. The artifact lands at `~/.cache/freemotion/results/pi_follow_bench-bench-<utc-stamp>.json`.

For a one-shot systemd run:

```bash
mkdir -p ~/.config/systemd/user
cp examples/pi_follow_bench/systemd/freemotion-pi-follow-bench.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start freemotion-pi-follow-bench.service
journalctl --user -u freemotion-pi-follow-bench.service -e
```

The unit is `Type=oneshot` — it runs once and exits. The artifact is written to `~/.cache/freemotion/results/`.

### View a previous run

```bash
python examples/pi_follow_bench/pi_follow_bench.py view \
  ~/.cache/freemotion/results/pi_follow_bench-ci-20260503T234732Z.json
```

Prints the same human-readable summary the runner emits with `--print-human`.

### Compare across runs

The artifact is JSON; standard tooling works. Examples:

```bash
# pass/fail timeline
ls -1 ~/.cache/freemotion/results/pi_follow_bench-bench-*.json | \
  xargs -I{} sh -c 'jq -r "[.completed_at, .success, .criteria.loop_ticks_observed] | @tsv" {}'

# diff two artifacts at the criteria level
diff <(jq .criteria run-A.json) <(jq .criteria run-B.json)

# extract last-run-was-good for a deploy gate
jq -r '.success' <(ls -1t ~/.cache/freemotion/results/*.json | head -1 | xargs cat)
```

## Failure modes (`--inject`)

Three named injects exercise the failure paths from [Step 3](../../docs/pi-failure-modes.md). Each runs the **same 10-step sequence** but expects a different per-mode outcome. The universal contracts (no crash, `/stop` returns ok, pins LOW at end, loop reads idle after stop, capabilities match locked surface) must hold under every inject — that's the safety story.

### `--inject=camera_offline`

The CI vision is replaced with a `MockVision` that always returns empty scenes (no detections). Models a Pi where `picamera2` is not installed or the camera ribbon is disconnected.

**Expected outcome:** loop runs, no MOVE dispatched, `vision_failures=0`, `mission_failures=0`, `dispatch_failures=0`, `move_dispatches_observed=false`, all universal contracts hold.

```bash
python examples/pi_follow_bench/pi_follow_bench.py run \
  --mode=ci --inject=camera_offline --hold=0.5 --tick-interval=0.05 \
  --stale-world-timeout=0.2 --print-human
```

### `--inject=mission_offline`

The CI mission is replaced with a policy whose `available=False` and whose `plan()` always returns idle. Models a Pi where Gemma's model load failed (OOM, missing weights, network filesystem flake).

**Expected outcome:** loop runs, no MOVE dispatched (idle decision every tick), all counters stay zero, all universal contracts hold.

```bash
python examples/pi_follow_bench/pi_follow_bench.py run \
  --mode=ci --inject=mission_offline --hold=0.5 --tick-interval=0.05 \
  --print-human
```

### `--inject=vision_drop_after_n`

The CI vision returns N clean scenes (default 3), then raises on every subsequent call. Models live YOLO that runs cleanly for a while and then starts erroring (the most common Pi-CPU failure mode for live inference).

**Expected outcome:** `vision_failures > 0`, MOVE dispatched in the clean window, no MOVE after the drop, all universal contracts hold.

```bash
python examples/pi_follow_bench/pi_follow_bench.py run \
  --mode=ci --inject=vision_drop_after_n --hold=0.5 --tick-interval=0.05 \
  --vision-drop-after=3 --print-human
```

## Allowed variance across runs

The benchmark protocol is frozen; the artifact is not byte-identical across runs by design. Field-by-field guidance:

| Field | Stable across runs? | Notes |
|---|---|---|
| `schema_version` | yes | Bump only on a breaking artifact change. |
| `run_id` | no | Fresh UUID every run. |
| `started_at` / `completed_at` / `duration_s` | no | Wall-clock dependent. |
| `mode` / `inject` / `intent` / `hold_s` | yes (per CLI args) | Identifies the run conditions. |
| `config_summary` | yes (per env / CLI args) | Operator-controlled. |
| `command_sequence[*].step` / `name` / `kind` | yes | The 10-step protocol is frozen. |
| `command_sequence[*].started_at` / `duration_s` | no | Wall-clock dependent. |
| `command_sequence[*].ok` / `state` / `error_code` | yes (per mode + inject) | Step 8 (`/stop`) is `ok=true` in every mode. |
| `command_sequence[*].telemetry_snapshot` | yes (shape) | Counter values vary; shape is stable. |
| `criteria.*` | yes (per mode + inject) | The roll-up is the regression detector. |
| `success` | yes (per mode + inject) | The single bit that says "did the contract hold?" |

If two runs in the same `--mode` and `--inject` differ on `success` or on any `criteria.*` boolean, that's a regression. If they differ only on timestamps or counter values within the documented bounds, that's noise.

## Verification checklist

Before declaring a Pi bench rig "ready for benchmark," walk through this once:

1. `pi_closed_loop_demo` runs end-to-end. (Prerequisite — the benchmark uses the same wiring; if the demo doesn't run, the benchmark won't either.)
2. `~/.config/freemotion.env` includes `FREEMOTION_SAFETY_DEFAULT=bench` (`mission_start` is refused in `dry_run`).
3. The camera is wired in and a simple `python -c "from freemotion.vision import PiCameraSource; print(PiCameraSource().available)"` reports `True`.
4. The bench rig has the two LEDs on `armed_pin` and `moving_pin` so the operator can see the loop dispatching MOVEs.
5. An operator (or a recognizable stand-in) is in frame for the duration of `--hold`.

Then run the benchmark; expect `verdict: PASS`. If it doesn't pass, the artifact's `criteria.*` and `command_sequence[*].error_*` fields tell you exactly which contract regressed.

## Why direct router dispatch?

The benchmark **does not** use Telegram. Round-tripping commands through Telegram would couple the benchmark to network latency, the bot's polling interval, and python-telegram-bot's retry behavior — none of which are part of the locked Pi contract. The router is the same path Telegram drives (the `Agent` calls `router.dispatch(cmd)` for every accepted message), so dispatching `Command` objects directly through the router exercises the safety floor, the deny list, the SafetyGate, the controller, and the mission loop in **exactly the same way** — minus the transport. That makes runs comparable across networks, machines, and bot tokens.

The full rationale (and the trade-offs) is in [ADR-0013](../../docs/decisions.md).

## Related docs

- [`docs/pi-reference.md`](../../docs/pi-reference.md) — Pi reference architecture lock (Step 4). The contracts this benchmark verifies.
- [`docs/pi-benchmark.md`](../../docs/pi-benchmark.md) — frozen benchmark protocol: sequence, criteria, artifact schema, allowed variance.
- [`docs/pi-failure-modes.md`](../../docs/pi-failure-modes.md) — Step 3 environmental failures + operator runbook. Every `--inject` mode here corresponds to a documented failure mode.
- [`docs/pi-closed-loop.md`](../../docs/pi-closed-loop.md) — Step 2 architecture reference. The wiring the benchmark mirrors.
- [`docs/decisions.md`](../../docs/decisions.md) — ADR-0013 (this benchmark's design); ADR-0012 (Pi reference architecture lock); ADR-0011 (Step 3 hardening); ADR-0010 (`MissionLoop`).
