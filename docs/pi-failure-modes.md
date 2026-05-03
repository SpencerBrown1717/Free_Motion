# Pi failure modes (Step 3 — real-world hardening)

Step 1 made perception real; Step 2 closed the loop; **Step 3 is about what happens when reality misbehaves.** This page is the canonical reference for every environmental failure the Pi closed loop is contracted to survive — what you'll see, where the signal lands in `/status`, and what the operator should do.

> **Status.** Step 3 of the Pi-first lockdown plan. The runtime contract is implemented in [`freemotion/agent/mission_loop.py`](../freemotion/agent/mission_loop.py); the design rationale is in [ADR-0011](decisions.md). The contract is locked at the v1 surface described here. **Step 4** ([`docs/pi-reference.md`](pi-reference.md)) treats this page as part of the reference architecture lock — the failure model is now part of the M5 baseline that Jetson must reproduce on its own hardware.

## The principle

> **Do not add capability. Add survivability.**

Step 3 added zero new commands and zero new model paths. What it added is:

1. A **stale-world timeout** that refuses MOVE on outdated perception.
2. **Per-stage consecutive failure counters** that flip a `degraded` flag the operator can read at a glance.
3. **Hung-tick handling** that prevents zombie threads when `mission.plan()` blocks past the join timeout.
4. **`graceful_shutdown`** — a helper that runs the demo's teardown in a tested, ordered, exception-tolerant sequence.
5. **Failure-injection tests** for every environmental failure listed in the table below.

## The 10 environmental failures and what the runtime does

For each, the table gives the symptom, what the runtime does automatically, what shows up in `/status`, and what the operator should do.

### 1. Camera unplugged mid-mission

| Question | Answer |
|---|---|
| Symptom | `vision.scene()` raises (libcamera errors, or `PiCameraSource()` returns `None` and YOLO sees no frame). |
| Runtime | Each failed scene increments `vision_failures` and `consecutive_vision_failures`. The loop continues with an empty scene. After `degraded_threshold` consecutive failures (default 5), `degraded=True` with reason `vision_failures>=5 (N)`. The stale-world clock advances; once it crosses `stale_world_timeout_s` (default 5s), `world_stale=True` and any MOVE the policy emits is **skipped** — `stale_world_skips` increments, no MOVE reaches the controller. |
| `/status` | `mission: running [DEGRADED: vision_failures>=5 (12)] [stale world: 8.3s] (intent='follow person')`. `telemetry.mission_loop.{vision_failures, consecutive_vision_failures, world_stale, world_age_s, stale_world_skips}` all carry the structured signal. |
| Hardware | The pins that were already HIGH (e.g. `armed_pin`) **stay** HIGH — the loop does not auto-disarm. The `moving_pin` does not pulse because no MOVE dispatches. |
| Operator | `/stop` to drop both pins LOW and halt the loop. Plug the camera in. `/mission_start` to resume. The runtime does not auto-recover the camera *handle* — `PiCameraSource` remains construction-bound to the original handle (ADR-0009). To pick up the new handle, restart the service. |

### 2. Camera returns bad frames repeatedly

| Question | Answer |
|---|---|
| Symptom | `cam()` returns `None` for many calls (libcamera transient errors), or returns malformed frames that YOLO can't infer on. |
| Runtime | `cam.capture_failures` increments per dropped frame. `YoloVision.scene()` already swallows its own inference errors and returns no detections; the loop sees empty scenes. Same behavior as Failure 1 from this point on (stale world; degraded if `vision.scene()` raises). |
| `/status` | `telemetry.mission_loop.world_stale` flips after the timeout; `telemetry.controller` is unaffected. |
| Hardware | Same as Failure 1 — pins that were HIGH stay HIGH; no MOVE dispatches; `/stop` is the operator's path. |
| Operator | If `capture_failures` rises but the loop eventually recovers (rare-camera-glitch case), the loop is doing exactly what's wanted: skip-MOVE-on-stale-then-resume-on-fresh. If failures persist, treat it like an unplugged camera. |

### 3. YOLO goes offline mid-loop

| Question | Answer |
|---|---|
| Symptom | The model unloads, hits a CUDA error, or its weights file becomes unreachable. `vision.scene()` either raises or returns empty. |
| Runtime | If `scene()` raises: same path as Failure 1. If `scene()` returns empty `VisionResult` (the YOLO adapter's standard fail-offline behavior per ADR-0007): empty scenes accumulate, world goes stale, no MOVE. |
| `/status` | If raising: `consecutive_vision_failures` climbs, `degraded=True`. If silently empty: `world_stale=True`, `stale_world_skips` climbs, `degraded` does **not** flip (vision technically didn't fail). |
| Hardware | Same — `/stop` is unconditional. |
| Operator | If `degraded=True` but `world_stale=False`, vision is *crashing*; investigate ultralytics. If `world_stale=True` but not `degraded`, vision is *silently empty*; check the model file path or GPU state. |

### 4. Gemma hangs or errors mid-tick

| Question | Answer |
|---|---|
| Symptom (errors) | `mission.plan()` raises (CUDA OOM, model unloaded, transformers internal error). |
| Symptom (hangs) | `mission.plan()` blocks much longer than `tick_interval_s` — the worker thread is stuck inside `transformers.generate(...)`. |
| Runtime (errors) | `mission_failures` and `consecutive_mission_failures` both increment; the tick treats the result as an idle decision (no MOVE). After threshold, `degraded=True` with reason `mission_failures>=5 (N)`. |
| Runtime (hangs) | Python provides no safe primitive to force-kill a thread (`PyThreadState_SetAsyncExc` is documented as unreliable). The runtime does the right thing anyway: `/stop` sets the stop event, joins for `join_timeout_s` (default 2s), and if the thread is still alive, leaves `_thread` set so a subsequent `/mission_start` refuses (no zombie thread leak) and the controller-stop callback still drops the pins LOW. When the hung tick eventually returns, `start()` reaps the dead thread and a fresh mission can be launched. |
| `/status` | Errors: `mission_failures > 0`, eventually `degraded=True`. Hangs after `/stop`: `running=False`, `stop_requested=True`, `intent=None` (the loop is idle from the operator's POV, even though there's a daemon thread asleep inside Gemma). |
| Hardware | `/stop`'s composite callback drops both pins LOW *before* it touches the loop, so a hung tick cannot leave the device armed. |
| Operator | After `/stop`, wait for the `mission_loop: stop()` warning to either log "stopped" (clean) or "worker thread did not join within Xs" (hung). In the hung case, `/mission_start` will refuse until the worker exits naturally; restart the service if you need to recover faster. |

### 5. OOM or resource pressure during a tick

| Question | Answer |
|---|---|
| Symptom | A layer raises `MemoryError` or similar. Could be vision, mission, or world. |
| Runtime | The per-stage `try/except` blocks catch every Exception (including `MemoryError`). Vision OOM → `vision_failures` increments. Mission OOM → `mission_failures` increments. The thread does not die. The OS still owns the process, of course; if Linux OOM-killer fires first, systemd's `Restart=on-failure` brings the service back. |
| `/status` | Identical to Failures 1, 3, 4 depending on which stage was the victim. |
| Hardware | Pins unaffected by the failure itself. `/stop` works as long as the agent's main thread can still receive the message — Python OOMs typically don't take down the polling loop. |
| Operator | Watch the failure counters. Repeated OOMs on a Pi 4 usually mean Gemma is too big for the host (ADR-0008 recommends `gemma-2-2b-it`). |

### 6. SIGTERM during an active mission

| Question | Answer |
|---|---|
| Symptom | systemd's `systemctl --user stop freemotion-pi-closed-loop-demo` (or any `kill -TERM <pid>`). |
| Runtime | python-telegram-bot's `app.run_polling()` honors SIGINT and SIGTERM and returns from the main thread. The demo's `try/finally` then calls `graceful_shutdown(...)`, which runs `mission_loop.stop()` (joins worker thread), `controller.stop()` (drops both pins LOW), `cam.close()` (releases libcamera), and `inner.cleanup()` (releases `RPi.GPIO`). Order is mission_loop → controller → cam → inner_cleanup so a still-ticking loop cannot dispatch MOVE *after* the controller is stopped. |
| `/status` | The agent is gone; nothing to query. The next service start will boot fresh — counters reset, `intent=None`, no zombie state. |
| Hardware | Pins LOW. GPIO library released. Camera released. |
| Operator | Nothing required. systemd's `Restart=on-failure` does not fire on SIGTERM (clean exit), so the service stays stopped until `systemctl --user start ...`. |

### 7. Telegram or network drop while the loop is running

| Question | Answer |
|---|---|
| Symptom | DNS fails, Telegram is unreachable, or the link drops. Slash commands stop arriving; replies cannot be sent. |
| Runtime | `app.run_polling()` retries internally per python-telegram-bot's documented backoff. The mission loop is **independent** of Telegram — it keeps ticking, perceiving, deciding, and dispatching MOVE. The hardware does whatever the loop says, subject to the SafetyGate. |
| `/status` | None — Telegram is unreachable. |
| Hardware | If the loop was running at the time of the drop, it will keep running until `/stop` reaches it. |
| Operator | This is the real-world reason `/stop` is unconditional and the deny-list cannot refuse it. If you trust the network, that's fine. If you don't, set `FREEMOTION_DENIED_COMMANDS=mission_start` at boot — only operator-driven `/move` ever runs, and `/stop` is always honored. (See [`docs/pi-runtime.md`](pi-runtime.md).) |

### 8. Stale world state or no detections for too long

| Question | Answer |
|---|---|
| Symptom | The camera is fine; YOLO is fine; the room is just empty. `world.last_seen[target]` ages without refresh. |
| Runtime | `world_age_s` climbs. Once it crosses `stale_world_timeout_s`, `world_stale=True`. Mission may still emit MOVE; the loop **skips** the dispatch (`stale_world_skips` increments). The skip is logged at `info` level. |
| `/status` | `[stale world: 8.3s]` in the human-readable summary; `mission_loop.world_stale=True` and `mission_loop.world_age_s` in telemetry. The skip is *not* counted as a `dispatch_failure` — it's a separate signal class. |
| Hardware | No MOVE → `moving_pin` doesn't pulse. The device sits idle waiting for fresh perception. |
| Operator | This is the safe behavior. The loop will resume MOVE dispatches the moment a non-empty scene comes in. To exit the wait, `/stop`. |

### 9. Repeated dispatch failures

| Question | Answer |
|---|---|
| Symptom | Every MOVE the loop emits is refused at the router. Causes include: device not armed (`/move` requires `arm` first), `denied_commands=move` configured, `dry_run` safety mode, controller disconnected from GPIO. |
| Runtime | `dispatch_failures` and `consecutive_dispatch_failures` increment per refusal. Threshold reached → `degraded=True` with reason `dispatch_failures>=5 (N)`. The loop **keeps running** so a config change (e.g. `/arm`) clears the streak automatically — one successful dispatch resets the consecutive counter. |
| `/status` | `[DEGRADED: dispatch_failures>=5 (7)]`, `last_dispatch_message: "move refused (not armed? insufficient battery?)"`. |
| Hardware | No actuation. |
| Operator | Read `last_dispatch_message`. The most common cause is "the device wasn't armed" — `/arm` and the next tick's MOVE will succeed. If you'd configured `denied_commands=move` deliberately, expect this; the loop is doing the right thing by surfacing degraded. |

### 10. Restart and recovery behavior

| Question | Answer |
|---|---|
| Recovery from `/stop` | `/mission_start` works again. All counters reset to zero on the new run; `intent` is the new intent. Hardware state is whatever `/arm` and `/disarm` last left it as (loop does not auto-arm). |
| Recovery from a hung tick | After `/stop`, the worker thread is hung. `/mission_start` refuses until the worker exits naturally. When it does, the next `/mission_start` reaps the dead thread and starts fresh (the same `start()` reaps-or-refuses logic). |
| Recovery from systemd restart | `Restart=on-failure` brings the service back after a crash. State is fully fresh — counters at zero, intent unset, `/status` reads idle. The pins are LOW because `inner.cleanup()` ran on the previous shutdown (or because GPIO defaults LOW on `RPi.GPIO.cleanup()`). |
| Recovery from SIGTERM (clean stop) | Service stays down. `systemctl --user start` to bring it back. |
| Recovery from camera reconnect | Today: requires service restart (the libcamera handle is bound at `PiCameraSource.__init__`, ADR-0009). Future: an explicit `/reconnect_camera` operator command if needed; the field experience suggests it's not. |
| Recovery from Gemma reload | Same — model is loaded at `GemmaMissionControl.__init__`. Service restart picks up a swapped model file. |

## Acceptance criteria → where each is verified

| Criterion | Evidence |
|---|---|
| **No failure crashes the process** | The thread's outer `try/except` catches every `Exception`; per-stage handlers catch theirs. Tested via `test_camera_unplugged_mid_loop_does_not_crash_and_eventually_stales`, `test_mission_exception_does_not_crash_the_loop`, `test_handler_exception_does_not_crash_the_loop`, and friends. |
| **`/stop` always wins** | The composite `on_stop` drops the controller pins **first**; `make_stop_handler` swallows callback exceptions. Tested via `test_stop_halts_loop_and_drops_pins`, `test_stop_remains_unconditional_with_deny_list`, `test_stop_in_dry_run_still_drops_pins`, `test_stop_interrupts_the_loop_even_with_long_tick_interval`. The hung-mission case is covered by `test_hung_mission_plan_keeps_thread_set_so_start_refuses` — the worker stays hung but `/stop`'s `controller.stop()` still ran. |
| **Hardware fails safe** | SafetyGate (ADR-0006) blocks ARM/MOVE in `dry_run` regardless of any per-command override. The loop only dispatches MOVE (ADR-0010). The composite `on_stop` always drops pins. Verified end-to-end by `test_safety_gate_floor_blocks_per_command_override` and the closed-loop demo's `test_stop_*` suite. |
| **Failures are visible** | Per-stage cumulative + consecutive counters in `state()`; degraded flag with reason; `world_age_s` and `world_stale`. Tested via `test_state_telemetry_is_complete_after_first_tick`, `test_status_handler_includes_mission_loop_state`, and the formatting tests in `test_builtins.py`. |
| **Degradation is protocol-shaped** | Every refusal goes through the router, which returns a `Reply` envelope. Every counter and flag is in `telemetry.mission_loop.*`. There is no ad-hoc text path — the human-readable `mission: ...` line in `/status` is derived from the same dict. |
| **Loop exits cleanly on SIGTERM** | `graceful_shutdown(...)` runs `mission_loop.stop` → `controller.stop` → `cam.close` → `inner.cleanup` in order, swallowing exceptions per layer. Tested via `test_graceful_shutdown_*` (ordering, exception tolerance, idempotency, polymorphism over `inner.cleanup`). |
| **Repeated failure does not leak state** | A new `start()` resets every counter, every consecutive flag, `degraded`, `last_perception_ts`, and `stale_world_skips`. Tested via `test_start_after_stop_resets_all_step3_counters`. The hung-thread case is covered by `test_hung_mission_plan_keeps_thread_set_so_start_refuses` and `test_start_reaps_dead_orphan_thread_after_hung_then_unhung`. |
| **Recovery is possible** | Camera capture failures are transient by design (ADR-0009); a single non-empty scene clears `world_stale` (`test_stale_world_clears_after_a_non_empty_scene`); `degraded` clears automatically when consecutive failures drop below threshold (`test_degraded_clears_after_recovery`); a fresh `start()` after a clean `stop()` works (`test_restart_after_clean_stop_works`). |

## Operator runbook — "what to do when it goes wrong"

This is the one-page version. It assumes Telegram is reachable and the operator has permissions on the chat.

### `mission: idle` and you didn't ask for it

The loop wasn't started, or it stopped on its own. The loop never auto-stops — only `/stop` halts it. So either:

1. The operator (or `/stop` from someone else with chat access) issued `/stop`.
2. The service was restarted (`/status` will show `uptime_s` close to zero).

`/mission_start` to bring it back.

### `[DEGRADED: vision_failures>=5 (N)]`

Vision is throwing. In order of likelihood:

1. **Camera unplugged.** Check the ribbon cable. `/stop`, reseat, restart the service.
2. **`ultralytics` crashed.** Check `journalctl --user -u freemotion-pi-closed-loop-demo.service`. Look for a Python traceback inside the YoloVision adapter. Restart the service.
3. **The CSI camera is being used by another process.** Stop other camera consumers. Restart.

### `[DEGRADED: mission_failures>=5 (N)]`

Mission control is throwing. Most likely:

1. **Gemma OOM.** The model is too big for this host. Use a smaller variant, or set `FREEMOTION_MISSION_BACKEND=mock` to fall back to the rule-based policy.
2. **`transformers` import error.** Reinstall: `pip install -e .[gemma]`.

### `[DEGRADED: dispatch_failures>=5 (N)]`

The router is refusing every MOVE the loop sends. Read `last_dispatch_message`:

- `"move refused (not armed?...)"` → `/arm`, then re-issue `/mission_start` (or just wait — the loop's next tick will succeed).
- `"command 'move' denied by device policy"` → `denied_commands=move` is set in the env. Either change the env and restart, or accept that the loop won't actuate.
- `"arm refused in dry_run; ..."` → device is in `dry_run`. Set `FREEMOTION_SAFETY_DEFAULT=bench` and restart.

### `[stale world: 8.3s]` with no `[DEGRADED]`

Vision is alive but seeing nothing. The room is empty, the camera is pointed at a wall, or YOLO's confidence threshold is too high for the scene. The loop is waiting for fresh perception — this is the safe behavior, not a failure. Walk into frame; the loop resumes on the next tick.

### Hung-tick scenario (rare)

`/stop` returns immediately, but `/mission_start` reports `mission already running` even though `/status` shows `mission: idle`. The worker thread is wedged inside `mission.plan()` (Gemma hung). Two options:

1. **Wait.** When Gemma's call eventually returns, `/mission_start` will succeed automatically (the next call reaps the dead thread).
2. **Restart the service.** `systemctl --user restart freemotion-pi-closed-loop-demo.service`.

The hardware is *not* armed during the wait — the composite `on_stop` already dropped both pins.

### Anything else

`/stop` is always available. `/status` always replies (it doesn't depend on the loop). `journalctl --user -u freemotion-pi-closed-loop-demo.service -f` shows the loop's per-tick logs and any warnings. The safety floor is the SafetyGate; the master kill is `/stop`. Both are unconditional by construction.

## Configuration knobs

| Env var | Constructor arg | Default | Effect |
|---|---|---|---|
| `FREEMOTION_MISSION_TICK_INTERVAL_S` | `tick_interval_s` | `1.0` | seconds between mission-loop ticks |
| (constructor only) | `stale_world_timeout_s` | `5.0` | seconds before a non-refreshed world is considered stale; MOVE skipped while stale |
| (constructor only) | `degraded_threshold` | `5` | per-stage consecutive failures that flip `degraded=True` |
| (constructor only) | `join_timeout_s` | `2.0` | how long `stop()` waits for the worker thread to join before logging hung-tick |

Constructor-only knobs are deliberate: they're tuning parameters for the v1 contract, not operational knobs. If you need to tune them in the field, fork the demo or wrap `MissionLoop` directly. The vast majority of bench rigs should leave the defaults alone — they were chosen for a Pi 4 + YOLO-nano + Gemma-2-2b-it on a stable bench.

## Related

- [docs/pi-closed-loop.md](pi-closed-loop.md) — canonical end-to-end architecture (Step 2).
- [docs/pi-hardware.md](pi-hardware.md) — `PiHardwareController` + `SafetyGate` (M4).
- [docs/pi-camera.md](pi-camera.md) — `PiCameraSource` + recovery semantics (Step 1).
- [docs/decisions.md](decisions.md) — ADR-0006 (SafetyGate), ADR-0009 (camera), ADR-0010 (loop), ADR-0011 (Step 3 hardening).
- [SAFETY.md](../SAFETY.md) — bench rules.
