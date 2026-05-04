# Jetson Phase 1 — bring-up plan (M5 Phase 1)

The Pi-first lockdown ([`docs/pi-reference.md`](pi-reference.md), [v0.2.0](../CHANGELOG.md)) locked one canonical Pi path with frozen surfaces and one named benchmark (`pi_follow_bench`) that verifies the contract. **Phase 1 of M5 ports that contract to a Jetson Nano** — same protocol, same command surface, same world state shape, same mission decision shape, same safety contract, same status shape, same `/stop` ordering, same failure model. Only the hardware-specific seams listed in [`pi-reference.md` §10](pi-reference.md) are allowed to differ.

This document is the **operator-facing bring-up plan**: read it before you write a single line of Jetson code. It tells you what to keep, what's allowed to change, what the first target demo is, and what the acceptance gate looks like.

> **Status.** Plan locked. No Jetson code has been written. Real Jetson hardware is required to ship Phase 1; this document opens the milestone cleanly so the work doesn't start ad hoc. Companion: [`docs/jetson-mapping.md`](jetson-mapping.md) — the dependency / env-var / camera-path / model-runtime / unsupported-features mapping (Step 9).

---

## 1. Read first

Before reading anything else, read:

1. [`docs/pi-reference.md`](pi-reference.md) — the locked Pi contract. Phase 1 ports **this** to Jetson; you cannot do the work without it loaded.
2. [`docs/pi-reference.md` §10](pi-reference.md) — the must-keep / allowed-to-differ table. **The most important section in the whole project for Phase 1.** Every decision in this doc is a downstream consequence of §10.
3. [`docs/pi-benchmark.md`](pi-benchmark.md) — the frozen benchmark protocol. Your acceptance gate is "a Jetson rig produces a `pi_follow_bench`-shaped artifact."
4. [`docs/decisions.md` ADR-0010, ADR-0011, ADR-0012, ADR-0013](decisions.md) — the rationale behind the locked surfaces. ADRs are not optional reading; if you don't know why a surface is locked, you'll regret it the first time you want to change it.

---

## 2. Must keep (hard constraints — no exceptions without an ADR)

These are copied verbatim from [`pi-reference.md` §10 "Must remain identical on Jetson"](pi-reference.md). They are repeated here because Phase 1's first acceptance criterion is "the operator can read this single doc and know what is allowed."

- **Protocol v0.** Same envelopes, same `v: 0`, same wire format. A Jetson device must be reachable from the same OpenClaw client that drives a Pi device. ([`docs/protocol.md`](protocol.md))
- **Eight-command surface.** `/ping`, `/capabilities`, `/status`, `/arm`, `/disarm`, `/move`, `/mission_start`, `/stop`. Anything else needs a protocol bump per [ADR-0002](decisions.md). Operators must not have to remember which commands are Pi vs. Jetson.
- **Loop dispatch scope = MOVE only.** [ADR-0010](decisions.md): the mission loop dispatches `MOVE` and only `MOVE`. ARM / DISARM / STOP stay strictly Telegram-driven. An LLM hallucination must not be able to arm or disarm any Free Motion device, regardless of host.
- **World state shape.** `target`, `current_state`, `confidence`, `last_seen`, `next_action`. Additive new fields are allowed; renames or removals require an ADR. ([ADR-0005](decisions.md))
- **Mission decision shape.** `next_command`, `args`, `reason`, `confidence`. Same rule — additive only. ([ADR-0008](decisions.md))
- **Twelve-guarantee safety contract.** `dry_run` is the floor; `bench` allows only the bench-safe primitive; `/stop` is unconditional; stale-world refusal; degraded summary; hung-tick handling; ordered graceful shutdown. Every guarantee from [`pi-reference.md` §6](pi-reference.md) must hold on Jetson, verified by tests against a Jetson-mocked controller.
- **`/status` shape.** `controller` and `mission_loop` telemetry blocks are required. New telemetry keys are additive; existing keys may not be removed or renamed. The same client tooling must parse a Jetson `/status` and a Pi `/status` interchangeably.
- **`/stop` ordering.** `mission_loop.stop` → `controller.stop` → `cam.close` → `inner.cleanup`. ([ADR-0011](decisions.md)) The Jetson's graceful-shutdown helper must follow this order; reordering is a regression.
- **Failure model surface.** Every environmental failure documented in [`docs/pi-failure-modes.md`](pi-failure-modes.md) must have an analog on Jetson — not necessarily the same code path, but the same observable behavior: clean degradation, accurate `/status` signal, documented operator action.

If a Jetson port "works" but breaks any item above, **the port is not done**. Phase 1 is "same contract, different hardware" by construction.

---

## 3. Allowed to differ (hardware-specific seams)

Copied from [`pi-reference.md` §10 "Allowed to differ on Jetson"](pi-reference.md). Each seam below has a tracked deliverable in §5 of this document.

| Seam | What's free to change | Hard constraint |
|---|---|---|
| `HardwareController` adapter | New `JetsonHardwareController` class. Different GPIO library (`Jetson.GPIO`), different pin map, different bench primitives if the Jetson dev kit doesn't expose 27/22 the same way. | Must implement the existing `HardwareController` Protocol; `state()` must include the same keys plus any additive Jetson-only telemetry; `stop()` must remain unconditional and lock-free. |
| Camera adapter | New `JetsonCameraSource` class **or** the same `cv2.VideoCapture`-shaped lambda used for USB webcams. `picamera2` is Pi-specific; Jetson has its own GStreamer / libcamera path or a USB-CSI bridge. | Must be callable returning `np.ndarray` or `None`. Must support `close()` (idempotent). Must fail-offline at construction without crashing. |
| Hardware factory | `freemotion.hardware.make_controller_from_config(cfg)` learns a new branch for `FREEMOTION_HARDWARE=jetson`. | Pi branch and host fallback must remain unchanged. |
| Vision / mission performance tuning | Larger YOLO weights (`yolov8s.pt` and up) where the Jetson GPU can carry them; Gemma quantization choices; tick interval. | The `VisionBackend` and `MissionPolicy` interfaces don't change. Tuning is per-deployment, not per-platform. |
| systemd unit | New `freemotion-jetson-closed-loop-demo.service`. | Same `Restart=`, `EnvironmentFile=`, and graceful-shutdown ordering as the Pi unit. |
| OS prep | New `docs/jetson-setup.md`. | Mirrors `docs/pi-setup.md` structure so the operator experience is parallel. |
| Examples directory | New `examples/jetson_closed_loop_demo/` and (optionally) `examples/jetson_bench_demo/`. | The closed-loop demo's `main()` must use the same `Config.from_env` → `make_controller_from_config` → `make_vision_from_config` → `make_mission_from_config` shape `pi_closed_loop_demo` uses. Re-implementing the whole top-level wiring on Jetson is a regression on the lock. |

Anything outside this table is **not allowed to differ** without a new ADR.

---

## 4. First target demo

> **`examples/jetson_closed_loop_demo/`** — Telegram → Jetson camera → YOLO → world state → Gemma → SafetyGate → Jetson GPIO → `/status`.

The Jetson equivalent of `pi_closed_loop_demo`. **Identical wiring, different adapters.** Specifically:

- Same top-level structure: `main() → Config.from_env → build_router_without_loop → MissionLoop → attach_mission_loop → Agent → run`. The closed-loop demo's two-pass router build and graceful-shutdown ordering are part of the lock; Jetson reuses both.
- Same `make_*_from_config` factories. The factories learn new branches; the demo doesn't.
- Same exit-code contract: `0` clean, `2` camera offline, `3` vision offline.
- Same systemd unit shape (renamed; `EnvironmentFile=%h/.config/freemotion.env` stays).

The Jetson bench demo (`examples/jetson_bench_demo/`) is **optional** for Phase 1 and may be deferred. It plays the same role `pi_bench_demo` plays — a controller-and-safety-only sub-path used to debug GPIO without perception or mission control. Recommended but not required for Phase 1's acceptance gate.

---

## 5. Phase 1 deliverables (in order)

Each line below is one PR's worth of work. The acceptance gate (§6) is the gate; this list is the path to the gate.

1. **`docs/jetson-mapping.md`** — Jetson dependency list, env-var mapping, camera-path differences, model-runtime differences, unsupported-features list. **(Step 9; required before code.)**
2. **`docs/jetson-setup.md`** — Jetson Nano OS prep walkthrough mirroring `docs/pi-setup.md`. JetPack version, `pip install -e .[jetson]` (or whatever the extra ends up named per Step 9), GPIO group setup, `~/.config/freemotion.env` template.
3. **`freemotion/hardware/jetson.py`** — `JetsonHardwareController`. Mirrors `freemotion/hardware/pi.py`'s structure: lazy `Jetson.GPIO` import, `armed_pin` / `moving_pin` (defaults TBD per dev-kit pinout), `move_pulse_s`, `stop()` lock-free + swallow-everything, `cleanup()` idempotent. Hardware exceptions caught; `arm()` / `move()` return `False` on failure; `state()` returns the same keys as `PiHardwareController.state()` plus any additive Jetson telemetry.
4. **Hardware factory branch** — `freemotion.hardware.make_controller_from_config(cfg)` learns `FREEMOTION_HARDWARE=jetson`. Pi branch and host fallback unchanged. New `Config.jetson_armed_pin` / `jetson_moving_pin` fields (mirroring the Pi pattern) parsed from `FREEMOTION_JETSON_ARMED_PIN` / `FREEMOTION_JETSON_MOVING_PIN`.
5. **`freemotion/vision/jetson_camera.py`** (or reuse `cv2.VideoCapture` lambda) — `JetsonCameraSource`. Mirrors `freemotion/vision/picamera.py`: callable, `close()` idempotent, transient-failure tolerant, fail-offline at construction. Backed by GStreamer / libcamera or `cv2.VideoCapture` as Step 9 decides.
6. **`examples/jetson_closed_loop_demo/`** — the first target demo (§4). Includes `README.md` (operator runbook mirroring `examples/pi_closed_loop_demo/README.md`) and `systemd/freemotion-jetson-closed-loop-demo.service`.
7. **Tests** — mirror `tests/test_pi.py`, `tests/test_pi_camera_source.py`, and `tests/test_pi_closed_loop_demo.py` for the Jetson adapters and demo. All tests must run on a non-Jetson host using injected `FakeGPIO` / fake camera fixtures (the same discipline `test_pi.py` follows). CI must not require a Jetson runner; bench-mode is the operator's responsibility.
8. **CI smoke** — extend `.github/workflows/ci.yml` to import-smoke `from freemotion.hardware.jetson import JetsonHardwareController`, `from freemotion.vision import JetsonCameraSource` (if shipped), and `import jetson_closed_loop_demo`. Same lazy-import discipline `pi_closed_loop_demo` uses.
9. **Run `pi_follow_bench` on real Jetson hardware** — see §6.
10. **`docs/jetson-reference.md`** — the locked Jetson reference doc, structured like `docs/pi-reference.md` and explicitly referencing it as the parent contract. **Last** — write it after the bring-up runs; it documents what Phase 1 actually shipped.

A new ADR (number TBD; next free is **ADR-0014**) records the Jetson port's design rationale: why these adapter boundaries, why these env-var names, why the camera path choice (GStreamer vs. cv2 vs. picamera2-equivalent), why Phase 1 ships before Phase 2 (ESP32). Land the ADR with the bring-up commit, not before.

---

## 6. Acceptance gate — "Phase 1 bring-up complete"

Phase 1 is **done** when **all five** are true:

1. `examples/jetson_closed_loop_demo/` runs the canonical command sequence (`/ping`, `/capabilities`, `/status`, `/arm`, `/move 1 0 0`, `/mission_start follow person`, `/stop`, `/disarm`) end-to-end against real Jetson hardware. Telegram is the transport.
2. Every safety guarantee from [`pi-reference.md` §6](pi-reference.md) holds — verified by running `pytest tests/` against a Jetson-mocked controller and Jetson-mocked camera. The existing test suite (376 tests) plus the new Jetson tests (target ~30, mirroring the Pi count) all pass on a non-Jetson host.
3. Every telemetry key from [`pi-reference.md` §7](pi-reference.md) is present in a Jetson `/status` reply. Diff a Jetson `/status` against a Pi `/status` from the same client; only platform-additive keys may appear, and no Pi-existing key may be missing.
4. **A Jetson rig produces a `pi_follow_bench`-shaped artifact.** Run on the Jetson:

   ```bash
   python examples/pi_follow_bench/pi_follow_bench.py run --mode=bench --print-human
   ```

   Expected: `success: true`, every criterion flag in [`docs/pi-benchmark.md` §2](pi-benchmark.md) green, the JSON artifact written to `~/.cache/freemotion/results/pi_follow_bench-bench-<ts>.json`. **Renaming the runner to `jetson_follow_bench` is allowed; the schema, sequence, and criteria are not.** This is the operator-facing proof that the contract holds end-to-end on the new hardware.
5. **`docs/jetson-reference.md` exists**, structured like `docs/pi-reference.md`, and explicitly references `docs/pi-reference.md` as the parent contract. The same 10-section template; values updated for Jetson; same locking discipline.

If any of the five fails, Phase 1 is not done. Each criterion maps to a section above:
- 1 → §4 (the demo).
- 2 → §5 step 7 (the tests).
- 3 → §2 + §3 (the must-keep / allowed-to-differ split).
- 4 → §1 (read [`pi-benchmark.md`](pi-benchmark.md)) and §5 step 9 (run it).
- 5 → §5 step 10 (the reference doc, written last).

---

## 7. What Phase 1 deliberately does **not** ship

These are out of scope for Phase 1 to keep the milestone tight. Each may be picked up after the gate is met.

- **ESP32 / Arduino support.** That's M5 Phase 2 / 3. Different SoC class, different constraint set; will get their own ADRs.
- **Jetson Orin / AGX support.** Phase 1 targets the Nano specifically because it's the cheapest member of the family that can run YOLO + Gemma. Larger Jetsons will work with the same code (the Protocol is identical) but tuning and packaging may need updates; out of scope for Phase 1.
- **Multi-camera Jetson rigs.** Same rule that applies to the Pi reference — one camera ([`pi-reference.md` §3](pi-reference.md)). Multi-camera is a `frame_source` design problem, not a Phase 1 problem.
- **Custom motion primitives.** The Pi reference exposes `move(x, y, z)` against a bench primitive (GPIO pulse). Jetson Phase 1 exposes the same primitive against Jetson GPIO. Real motor drivers, motor controllers, autopilot links, etc. are tracked in the broader Safety / hardware-extension roadmap, not in Phase 1.
- **Operator authentication, allow lists, rate limits, watchdogs, link-loss fail-safe.** Same status as on the Pi — explicitly out of scope for the Pi-first lockdown ([release notes for v0.2.0](releases/v0.2.0.md)). They become Phase-shared concerns once one platform actually needs them.
- **Cross-platform CI runner.** No hosted Jetson runner; bench-mode `pi_follow_bench` (or `jetson_follow_bench`) is the operator's responsibility. CI continues to run `--mode=ci` only.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `Jetson.GPIO` library API drift between JetPack versions. | Pin a `Jetson.GPIO` version range in the new `[jetson]` extra (Step 9). Keep all GPIO calls inside `freemotion/hardware/jetson.py` so a future API drift only changes one file. |
| Camera path is platform-fragile (GStreamer pipelines, libargus, libcamera, USB-CSI bridges all differ across JetPack and dev-kit revisions). | Decide the canonical Jetson camera path in [`docs/jetson-mapping.md`](jetson-mapping.md) (Step 9) **before** writing code. Allow `cv2.VideoCapture` as a fallback for USB webcams. Fail-offline at construction so a misconfigured pipeline doesn't crash the agent. |
| Phase 1 work expands into "let's also fix X on the Pi side." | The Phase 1 deliverable list (§5) is closed. Out-of-scope improvements file as separate issues / PRs against the Pi reference. ADR-0012 is the lock; this milestone respects it. |
| Bench-mode benchmark passes on Jetson but real-world behavior diverges from Pi. | The benchmark's job is to verify the **contract**, not to verify perception quality. Differences in YOLO accuracy or Gemma latency are tuning concerns and live in `docs/jetson-mapping.md` "model runtime differences," not in the contract. |
| LLM-generated MOVE under load on a Jetson behaves differently than on a Pi. | The contract's [`MissionLoop` MOVE-only dispatch](decisions.md) and [`SafetyGate` floor](decisions.md) are platform-agnostic. If behavior diverges in a way that violates a §2 must-keep, that's a regression on the contract, not a Jetson-specific issue, and the fix lands on **both** platforms. |
| Real Jetson hardware unavailable when Phase 1 is otherwise ready to ship. | Phase 1 deliverables 1–8 (docs, adapters, factory, tests, CI smoke) can all ship and be verified on a non-Jetson host. Only deliverables 9 and the live demo require real hardware. The contributor may choose to land deliverables 1–8 in a feature branch and merge after gate-4 verification on real hardware. |

---

## 9. Move-to-Phase-2 rule

You move to **M5 Phase 2 (ESP32)** only when **all five §6 acceptance criteria are met**, the bench-mode `pi_follow_bench` artifact is committed to the repo (e.g. `docs/releases/jetson-phase1-bench-artifact.json`) as evidence, and `docs/jetson-reference.md` has been merged. The same discipline that gated Step 5 → M5 (no skipping the proof) applies here.

---

## 10. Definition of done

A contributor can read this single doc and:

1. Know what is already locked (§2) and what they're allowed to change (§3).
2. Know what the first target demo is and how it relates to the Pi reference (§4).
3. Know the exact list of deliverables in priority order (§5).
4. Know exactly when Phase 1 is done (§6, all five must pass).
5. Know what Phase 1 explicitly does **not** ship (§7) so they don't accidentally scope-creep.
6. Know which pre-existing risks have planned mitigations (§8) and which gate the next phase (§9).

When all six are obvious without needing a separate sync, **Step 8 is done.** The companion mapping doc — [`docs/jetson-mapping.md`](jetson-mapping.md) — closes the remaining "what dependencies / env vars / camera path / model runtime / unsupported features?" gap (Step 9) and is the last documentation deliverable before code.

---

## Related

- [`docs/pi-reference.md`](pi-reference.md) — the parent contract.
- [`docs/pi-reference.md` §10](pi-reference.md) — must-keep / allowed-to-differ table (the source of §2 and §3 above).
- [`docs/pi-benchmark.md`](pi-benchmark.md) — the frozen benchmark protocol; the basis of acceptance criterion §6.4.
- [`docs/jetson-mapping.md`](jetson-mapping.md) — Jetson dependency / env-var / camera / model / unsupported-feature mapping (Step 9, **read before writing code**).
- [`docs/decisions.md`](decisions.md) — ADR ledger; ADR-0010 / 0011 / 0012 / 0013 explain the contracts Phase 1 ports.
- [`ROADMAP.md`](../ROADMAP.md) — where Phase 1 lives in the milestone story.
- [`docs/releases/v0.2.0.md`](releases/v0.2.0.md) — what was locked in the Pi-first lockdown release that gates this work.
