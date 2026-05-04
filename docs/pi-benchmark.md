# Pi benchmark protocol — `pi_follow_bench` (Step 5 — frozen)

This is the **single source of truth** for what `pi_follow_bench` runs, what it counts as success, and what it writes to disk. Step 5 of the Pi-first lockdown plan: turn the locked Pi contract ([`pi-reference.md`](pi-reference.md)) into one repeatable, operator-runnable benchmark.

> **Status.** Locked. Locking decisions are recorded in [ADR-0013](decisions.md). The 10-step sequence, the success criteria, and the JSON artifact schema are frozen — changes require an ADR. Tunables (hold window, tick interval, stale-world timeout, min-loop-ticks) are operator knobs, not protocol changes.

The runner is at [`examples/pi_follow_bench/`](../examples/pi_follow_bench/). The operator runbook is [`examples/pi_follow_bench/README.md`](../examples/pi_follow_bench/README.md).

---

## 1. Frozen sequence

`pi_follow_bench` always runs **exactly these 10 steps, in order**, regardless of `--mode` or `--inject`. Skipping a step or reordering them is a breaking change.

| # | Kind | Wire name | Args | Why |
|---|---|---|---|---|
| 1 | command | `ping` | `{}` | Round-trip liveness check. Asserts router + handlers are up. |
| 2 | command | `capabilities` | `{}` | Asserts the device exposes **exactly** the 8 commands locked in [`pi-reference.md` §2](pi-reference.md). |
| 3 | command | `status` | `{}` | Captures the initial telemetry snapshot (loop=idle, controller=idle, no failures). |
| 4 | command | `arm` | `{}` | Drives `armed_pin` HIGH. Refused in `dry_run`. |
| 5 | command | `mission_start` | `{"intent": <str>}` | Starts the background mission loop. Default intent: `"follow person"`. Refused in `dry_run` per [ADR-0010](decisions.md). |
| 6 | observe | `observe` | `hold_s` | Blocking sleep so the loop can tick. The default hold is 5.0s; a single integer step in the artifact, no router dispatch. |
| 7 | command | `status` | `{}` | The criterion-bearing snapshot. Asserts `mission_loop.running=true` and `mission_loop.tick_count >= min_loop_ticks`. |
| 8 | command | `stop` | `{}` | Master kill. Halts the loop **and** drops both pins LOW unconditionally per [ADR-0006](decisions.md). |
| 9 | command | `disarm` | `{}` | Idempotent after `/stop` — must still ack `ok=true`. |
| 10 | command | `status` | `{}` | Final state. Asserts `mission_loop.running=false` and `controller.armed=false`. |

Every command is dispatched **directly through the router** (no Telegram, no JSON serialization, no chat-id auth). The benchmark constructs `Command` envelopes with `safety=cfg.safety_default` and calls `router.dispatch(cmd)` — the same call path the agent's `handle_text` uses for every accepted message. See [ADR-0013](decisions.md) for why direct dispatch is the right choice.

The `safety` field on each command is `cfg.safety_default`; the benchmark never tries to override it. `cfg.safety_default = bench` is required (a `dry_run` config refuses `arm` and `mission_start`, so the benchmark cannot make progress past step 4). `live` is allowed but irrelevant for v1 — there is no v1 `live`-only behavior.

## 2. Frozen success criteria

A run passes only when **every** flag below is true. The roll-up is the artifact's `success` field; each flag is reported individually in `criteria.*` so a regression points at the offending contract.

### Universal (every mode + every inject)

| Flag | Meaning | What makes it true |
|---|---|---|
| `all_commands_ok` | Every dispatched command returned `ok=true`. | Step 1–5 and 7–10 must each return `Reply.ok=true`. The observe step is not a command. |
| `capabilities_match_locked_surface` | `/capabilities` reports the locked Pi command surface. | Step 2's `telemetry.capabilities` (sorted) equals `("arm","capabilities","disarm","mission_start","move","ping","status","stop")`. |
| `loop_reached_running` | The loop was actually running at step 7. | Step 7's `telemetry.mission_loop.running == true`. |
| `loop_ticks_met` | The loop did real work during the hold. | Step 7's `telemetry.mission_loop.tick_count >= min_loop_ticks` (default 1). |
| `loop_stopped_clean` | `/stop` brought the loop to idle. | Step 8 returned `ok=true` AND step 10's `telemetry.mission_loop.running == false`. |
| `pins_low_at_end` | The controller is depowered. | Step 10's `telemetry.controller.armed == false`. |
| `no_unexpected_failures` | Failure counters are within the bounds for this mode. | See "Inject-specific bounds" below. |
| `min_move_dispatches_required` | When the operator asked for MOVE evidence, step 7 reports it. | If `--min-move-dispatches=1`: step 7 reports `last_dispatched=="move"` AND `last_dispatch_ok==true`. If 0: vacuously true. |

### Inject-specific bounds (`no_unexpected_failures`)

The clean run has zero tolerance. Each named inject documents which counter is **expected** to grow and requires the others to stay clean. Failures **outside** the injected stage are still regressions.

| `inject` | `vision_failures` | `mission_failures` | `dispatch_failures` |
|---|---|---|---|
| `null` (clean) | `== 0` | `== 0` | `== 0` |
| `camera_offline` | (allowed; bounded only by inject behavior) | `== 0` | `== 0` |
| `mission_offline` | `== 0` | `== 0` (the offline policy returns idle, not raises) | `== 0` |
| `vision_drop_after_n` | (allowed; expected `> 0`) | `== 0` | `== 0` |

`stale_world_skips` is reported but does not gate `success`. It's a downstream consequence of `vision_failures` (or empty scenes) — the runtime's documented response, not an independent fault.

### Single rollup

```
success = (
    all_commands_ok
    AND capabilities_match_locked_surface
    AND loop_reached_running
    AND loop_ticks_met
    AND loop_stopped_clean
    AND pins_low_at_end
    AND no_unexpected_failures
    AND min_move_dispatches_required
)
```

Adding a flag is additive (existing artifacts stay readable; the rollup just gets stricter). Removing a flag is a breaking change requiring an ADR. **Do not weaken `success`.**

## 3. Frozen artifact schema

Every run writes one JSON file. The schema version is `1`; bumps require an ADR. New fields are additive — readers must tolerate unknown fields.

```jsonc
{
  "schema_version": 1,
  "run_id": "<uuid v4>",
  "started_at": "<ISO 8601 UTC, seconds precision>",
  "completed_at": "<ISO 8601 UTC, seconds precision>",
  "duration_s": 12.34,
  "mode": "bench" | "ci",
  "inject": null | "camera_offline" | "mission_offline" | "vision_drop_after_n",
  "intent": "follow person",
  "hold_s": 5.0,
  "config_summary": {
    "device_id": "<str>",
    "hardware_profile": "pi" | "host" | "<other>",
    "safety_default": "dry_run" | "bench" | "live",
    "vision_backend": "mock" | "yolo",
    "mission_backend": "mock" | "gemma",
    "denied_commands": ["..."],
    "pi_armed_pin": <int|null>,
    "pi_moving_pin": <int|null>
  },
  "command_sequence": [
    {
      "step": 1,
      "name": "ping",
      "kind": "command" | "observe",
      "started_at": "<ISO 8601 UTC>",
      "duration_s": 0.001,
      "ok": true,
      "state": "idle" | "armed" | "running" | "moving" | "error" | null,
      "error_code": null | "<protocol error code>",
      "error_message": null | "<str>",
      "message": "<str>",
      "telemetry_snapshot": null | {
        "controller": { ... },
        "mission_loop": { ... },
        "capabilities": ["..."]
      }
    }
    /* ... 9 more steps ... */
  ],
  "criteria": {
    "expected_outcome": "clean" | "<inject>",
    "all_commands_ok": true,
    "capabilities_match_locked_surface": true,
    "loop_reached_running": true,
    "min_loop_ticks_required": 1,
    "loop_ticks_observed": 7,
    "loop_ticks_met": true,
    "loop_stopped_clean": true,
    "pins_low_at_end": true,
    "move_dispatches_observed": true,
    "min_move_dispatches_required": true,
    "no_unexpected_failures": true,
    "vision_failures": 0,
    "mission_failures": 0,
    "dispatch_failures": 0,
    "stale_world_skips": 0,
    "notes": []
  },
  "success": true
}
```

`telemetry_snapshot` is `reply.telemetry` filtered to `{controller, mission_loop, capabilities}`. The full controller and mission_loop shapes are pinned in [`pi-reference.md` §7](pi-reference.md). Adding telemetry keys is additive; removing or renaming requires an ADR (see [ADR-0002](decisions.md), additive-only protocol evolution — same rule applies to telemetry).

## 4. Allowed variance across runs

The benchmark is repeatable, not byte-identical. Two runs of the same `--mode` and `--inject` will differ on:

- `run_id` (always fresh)
- `started_at`, `completed_at`, `duration_s`
- per-step `started_at` and `duration_s`
- exact `tick_count`, `last_decision`, `last_dispatch_message` (varies with timing)
- `vision_failures` count under `--inject=vision_drop_after_n` (depends on how many post-drop ticks fit in `--hold`)

Two runs **must agree** on:

- the 10 step `name`s and `kind`s (the protocol is frozen)
- every `criteria.*` boolean (the contract is frozen)
- `success`

If two runs in the same mode/inject disagree on `success` or any `criteria.*` boolean, **that is a regression**. The runtime's contract failed under conditions where it was supposed to hold.

## 5. Tunables (operator knobs, not protocol)

Tunables live in CLI flags and never change the protocol. Adding a tunable is operator-friendly; changing a default that affects pass/fail is an ADR-level decision (because old artifacts become non-comparable).

| Flag | Default | Effect | Why it is not part of the protocol |
|---|---|---|---|
| `--hold` | 5.0s | Observation window between steps 5 and 7. | Faster runs in CI, longer runs on a real Pi. |
| `--tick-interval` | 1.0s | `MissionLoop.tick_interval_s`. | Lets a Pi 3 run at 0.5–1Hz and a Pi 5 at 5Hz. |
| `--stale-world-timeout` | 5.0s | `MissionLoop.stale_world_timeout_s`. | Tunable per [`pi-reference.md` §5](pi-reference.md). |
| `--min-loop-ticks` | 1 | Minimum `tick_count` at step 7. | Stricter on a Pi 5 (e.g. 5–10), looser on a Pi 3. |
| `--min-move-dispatches` | 0 | If 1, step 7 must report a successful MOVE. | Recommended `1` for CI clean runs; stays `0` for bench runs where the operator may stand out of frame. |
| `--intent` | `"follow person"` | The intent passed to `/mission_start`. | Future intents (`"track package"`, `"loiter"`) live behind their own benchmark; this benchmark is `pi_follow_bench`. |
| `--vision-drop-after` | 3 | For `--inject=vision_drop_after_n`: clean scenes before the drop. | Tunable test fixture, not a contract surface. |

The benchmark **records** the active values of these knobs in `config_summary` and the top-level `intent`/`hold_s` fields, so a future reader of an old artifact can tell what was tuned. A run with non-default knobs is still a valid `pi_follow_bench` run, but artifacts comparing across knob values are not directly comparable on every counter (e.g. `loop_ticks_observed` scales with hold/tick).

## 6. Failure modes (`--inject`)

Three named injects map onto the environmental failures locked in [`pi-failure-modes.md`](pi-failure-modes.md). Each runs the **same 10-step sequence** but expects a different per-mode outcome.

| Inject | What it injects | Documented outcome |
|---|---|---|
| `camera_offline` | Vision returns empty scenes always (no detections). | Loop runs; no MOVE dispatched (mission decides "no person in scene"); `move_dispatches_observed=false`; `vision_failures=0`; universal contracts hold. Maps to `pi-failure-modes.md §1`–`§3`. |
| `mission_offline` | A `MissionPolicy` whose `available=false` and `plan()` returns idle. | Loop runs; no MOVE dispatched (idle decision every tick); all counters zero; universal contracts hold. Maps to `pi-failure-modes.md §4`. |
| `vision_drop_after_n` | Vision returns N clean scenes (default 3), then raises on every subsequent call. | Loop runs; MOVE dispatched in the clean window; `vision_failures > 0`; universal contracts hold. Maps to `pi-failure-modes.md §3`. |

The universal-contract guarantees do **not** weaken under injection. `stop` always returns `ok`, pins are LOW at end, the loop reads idle after stop, and the capability surface still matches. That is the safety story this benchmark exists to prove.

## 7. CI integration

CI runs `pi_follow_bench --mode=ci` on every push as a structural smoke test. The job:

1. Imports the runner module (lazy-imports of `picamera2` / `RPi.GPIO` / `ultralytics` / `transformers` are not exercised — `--mode=ci` uses mocks).
2. Runs `pi_follow_bench run --mode=ci --hold=0.3 --tick-interval=0.05 --min-loop-ticks=2 --min-move-dispatches=1`.
3. Runs the same with each of the three injects (allowed-failure bounds documented above).
4. Asserts `success=true` for every run.

A CI failure means the benchmark contract regressed — either the runtime broke a locked surface, or an additive change to the runtime invalidated a pinned criterion. Either way, the artifact in the run logs tells you which `criteria.*` flag flipped.

## 8. Doc alignment

The benchmark has one source of truth for the **protocol** (this file), one for the **runner** (the runbook in [`examples/pi_follow_bench/README.md`](../examples/pi_follow_bench/README.md)), and one for the **rationale** ([ADR-0013](decisions.md)). Cross-references:

| Doc | Role |
|---|---|
| **`docs/pi-benchmark.md`** (this file) | The protocol: sequence, criteria, artifact schema, allowed variance, tunables, inject mapping. |
| [`examples/pi_follow_bench/README.md`](../examples/pi_follow_bench/README.md) | The operator runbook: how to install, run, view, interpret. |
| [`docs/pi-reference.md`](pi-reference.md) | The Pi reference architecture lock — the contracts this benchmark verifies. |
| [`docs/pi-failure-modes.md`](pi-failure-modes.md) | The environmental failure reference — every `--inject` mode here maps onto one of those failures. |
| [`docs/decisions.md`](decisions.md) | ADR-0013: rationale for direct router dispatch, the frozen schema, and the criteria choices. |
| [`README.md`](../README.md), [`GETTING_STARTED.md`](../GETTING_STARTED.md) | Entry points; both link to the runbook above. |
| [`ROADMAP.md`](../ROADMAP.md), [`CHANGELOG.md`](../CHANGELOG.md) | Per-step delta log; Step 5's entry points back here. |

If a contributor finds drift between any two of these, **this file** is the source of truth for the protocol; the runner code (`benchmark.py` + `pi_follow_bench.py`) is the source of truth for behavior.

## 9. Move-to-M5 rule

This benchmark is the gate for M5. Per [`pi-reference.md`](pi-reference.md), a Jetson port is "done" when (among other criteria) "the named benchmark task from Step 5 runs on Jetson with the same success criteria." That promise binds Jetson to this protocol — same 10 steps, same criteria, same artifact schema, different hardware. M5 Phase 1 ships when a Jetson rig produces a `pi_follow_bench`-shaped artifact (renamed `jetson_follow_bench` is allowed; the schema, sequence, and criteria are not).

## 10. Definition of done (Step 5)

A contributor can:

1. **Run the benchmark on a real Pi** following the runbook and read a `success: true` artifact.
2. **Read a previous artifact** and tell whether the run passed.
3. **Inject a failure** (`--inject=...`) and read an artifact that still says `success: true` because the universal contracts held.
4. **Compare two artifacts** and identify exactly which contract regressed when one fails.
5. **Promote a Pi to "ready for Jetson"** by checking that the benchmark passes on the Pi reference rig.

When all five are obvious, Step 5 is done.
