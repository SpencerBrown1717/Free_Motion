# Decisions

A short ledger of architectural calls. Each entry is intentionally a few lines, not a paper. Newer entries on top.

The point of this file is so a contributor (or future you) can answer "why did we do it this way?" in 60 seconds without re-deriving the trade-off.

## Format

Each ADR has:

- a one-line title with date,
- the context (what forced the decision),
- the decisions (the binary calls), and
- a status (locked, partial, or replaced by a later ADR).

---

## ADR-0001 — Protocol v0: slash + JSON, optional `to`, in-band telemetry, no seq numbers — 2026-05-03

**Context.** M1 needed a stable contract before more code piled on top of informal slash commands. Six recurring questions kept showing up in discussion; locking them in writing was cheaper than re-deciding each one.

**Decisions.**

- **Slash sugar AND JSON envelopes coexist.** Same internal `Command` type. Slash sugar is a strict subset of what JSON can express. Humans get an easy entry; OpenClaw and any code uses JSON.
- **Correlation id is a sender-generated UUID, not the Telegram message id.** Tying `id` to a transport-specific identifier would couple the protocol to Telegram and break the moment a second transport (MQTT, HTTP, serial) shows up.
- **`safety` defaults to `dry_run` when ambiguous on the protocol level.** Boring is correct: the protocol cannot safely default to "actuate." Devices override with a config default that fits their deployment (`bench` for desk demos, `live` only when explicitly opted in).
- **Devices dedupe by `id` within a 60s TTL window.** No sequence numbers. Cheaper than a full ordering scheme, sufficient for "user pressed retry," easy to extend.
- **Protocol layer is stdlib only.** No Pydantic, no msgspec, no extra dependencies. Easy to audit, fast to install on a Pi, easy to port to another runtime later.
- **`to` field is optional in v0.** Telegram is 1:1. The field becomes required when fan-out (one OpenClaw → many devices) is real, not before.
- **Args schemas are inline in `docs/protocol.md` for v0.** Move to per-command JSON Schema files only when any one command's args grow past a few fields.

**Status.** Locked. Breaking changes bump the protocol `v` per [docs/protocol.md](protocol.md).

---

## ADR-0002 — Hardware abstraction starts now (small) + `move` is additive — 2026-05-03

**Context.** M2 shipped the runtime; the next pressure was "let contributors without a Pi build on this." A mock device is the unlock, but it forced the question of how command handlers should talk to hardware so mock and real implementations can share code.

**Decisions.**

- **`HardwareController` is a Protocol, not an ABC.** Contributors implement it duck-typed; runtime checks via `isinstance(obj, HardwareController)` work because of `@runtime_checkable`. Lower friction than inheritance, easier to swap.
- **Five-method surface for v1: `available`, `arm`, `disarm`, `stop`, `move`, plus `state()`.** Anything richer (sensors, modes, calibration) is deferred until a real second implementation forces a richer interface. Pre-generalizing here would be guessing.
- **Mock is deterministic.** No time-based simulation, no noise, no physics. The mock exists to prove the runtime, not to train autopilots. Time-based behavior would make tests flaky and demos confusing.
- **`move` enters the protocol as an additive change.** Old parsers treat `move` as `unknown_cmd`, which is the correct behavior; new parsers handle it. No `v` bump per the rule in [protocol.md](protocol.md#status).
- **Two examples now coexist (`pipe_check`, `mock_drone`).** Each demonstrates one pattern: peripheral on real hardware vs full controller on mock hardware. Resisted the temptation to make one example serve both via a switch — it would muddy both stories.

**Status.** Locked. The Protocol surface stays as-is until a `PiHardwareController` (or similar) lands and tells us what's actually missing.

---

## ADR-0003 — Vision and mission control: interfaces + mocks now, real models behind feature flags later — 2026-05-03

**Context.** YOLO and Gemma small are core to the project's identity, but pulling either into the runtime as a hard dependency would slow CI, lock out contributors without a GPU, and tie the runtime to upstream model releases. The pressure was to land the architecture for both **now** without paying any of those costs.

**Decisions.**

- **Interfaces ship before any model.** `VisionBackend` and `MissionPolicy` are Protocols (matching the `HardwareController` precedent in ADR-0002). Real adapters (`YoloVision`, `GemmaMissionControl`) come later as separate PRs.
- **Mock backends are first-class, not placeholders.** `MockVision` and `MockMissionControl` are the deterministic implementations the test suite, demos, and `examples/mock_follow_task/` will use indefinitely. They are the canonical reference for the contract; real adapters must match their behavior on the same inputs to the extent the contract is determinate.
- **One method on `VisionBackend`: `scene()`.** Backends manage their own input source. Callers don't pass frames in; the backend owns the camera, frame buffer, or scripted timeline. This keeps the interface trivial for callers and gives real adapters room to optimize internally.
- **`MissionPolicy.plan` returns a single `MissionDecision`, not a plan tree.** One concrete next action (one `CommandName` + args), plus a reason and a confidence. `next_command=None` is the explicit "do nothing" signal. Anything richer (multi-step plans, tool calls, free-form text) is deferred until a real adapter forces it. Constraint here keeps the integration cheap and the loop debuggable.
- **`MissionPolicy.plan` takes vision + world as inputs.** Mission control can react to scene state without owning the camera. World state (`freemotion.world`, M3) becomes the carrier for everything else (current_state, last_seen, next_action). The `world` arg's concrete type was tightened in ADR-0005 to `WorldStateSnapshot`.
- **Real adapters land behind config flags, not extras-by-default.** `FREEMOTION_VISION_BACKEND=mock|yolo` and `FREEMOTION_MISSION_BACKEND=mock|gemma`, defaulting to mock. The flags themselves don't ship until the adapters do — adding flags before they're meaningful would be cargo culting.
- **Heavy deps go behind `pyproject.toml` extras.** `pip install -e .[yolo]` and `pip install -e .[gemma]`. The base install stays stdlib + `python-telegram-bot`, the same as today. Tests for real adapters skip cleanly when their dep isn't installed.

**Status.** Locked. Real adapters are tracked as separate issues in [`docs/issues/m2-m3.md`](issues/m2-m3.md) (#3 and #4). The interfaces stay frozen until at least one real adapter on each side ships and tells us what's missing.

---

## ADR-0004 — Per-command allow/deny: allow by default, explicit deny list, `stop` always exempt — 2026-05-03

**Context.** A device tuned for one role (e.g. "vision only" Pi, or a desk-bound bench rig) needs to refuse commands its handlers would otherwise execute. Without a policy layer, contributors hard-code refusals into individual handlers — fragile, easy to miss, easy to bypass.

**Decisions.**

- **Allow by default.** A registered handler runs unless the command is on the deny list. The alternative — deny by default with an explicit allow list — would require every device config to enumerate its capabilities, doubling the surface area of `Config` for no operational gain. Deny lists are the smaller, more honest knob.
- **Policy lives on `Router`, not `Agent`.** The check happens at dispatch, before the handler runs. That means it covers slash, JSON, and any future transport equally; transports never need to know about it.
- **Wire the policy from `Config.denied_commands` (env var `FREEMOTION_DENIED_COMMANDS`).** Comma-separated wire command names. Unknown names are tolerated (forward-compatible with newer protocol versions).
- **`stop` is exempt unconditionally.** Users can list `stop` in `FREEMOTION_DENIED_COMMANDS`; both `Config.from_env` (with a warning log) and `Router.__init__` strip it. Hard-stop must work in any policy state, full stop.
- **Refused commands return `error.code = "denied_by_policy"`, not `unauthorized`.** Mixing the two would conflate "you're not allowed to talk to this device" (auth) with "this device chose not to do that command" (policy). Different observables, different alerts, different fixes.
- **Deny check precedes the unknown-command check.** A command that's both denied and not registered must report the deny, so an attacker probing for capabilities can't tell whether the command exists.

**Status.** Locked. Adding allow lists later (if a deployment really needs them) is additive and doesn't conflict with this ADR.

---

## ADR-0005 — World state v1: narrow, lock-protected, snapshot-shaped — 2026-05-03

**Context.** Mission control needs a place to read "what does the device think is true right now," and vision needs a place to write what it just saw. Without a typed shared structure, the third component to land would invent its own — and we'd be wiring three formats together.

**Decisions.**

- **Snapshot-shaped, not actor-shaped.** `WorldStateSnapshot` is a frozen dataclass; `WorldState` is a thread-safe wrapper that hands out snapshots. Readers never see a half-applied write; writers never block on each other beyond the lock. Cheaper than an event bus, easier to reason about than a CRDT.
- **Five fields only:** `target`, `current_state`, `confidence`, `last_seen` (per target), `next_action`. The user-facing roadmap listed exactly these. Adding fields requires an ADR; deletions require bumping a version. Wider state belongs in dedicated modules.
- **`MissionPolicy.plan` takes `WorldStateSnapshot`, not `Mapping[str, Any]`.** This is the typed completion of the placeholder in ADR-0003. Tightening the type now is cheap; tightening it after a real adapter ships would not be.
- **Updates are total replacements via `dataclasses.replace`.** `update(**changes)` swaps the snapshot reference under the lock; we never mutate fields in place. Old snapshots stay valid forever.
- **`see(label, *, confidence=...)` is the canonical vision → world hop.** Sets `target`, appends to `last_seen`, optionally records `confidence`, all atomically. Encodes the vision-recorded-a-detection pattern so callers don't reinvent it (and don't accidentally race two writers on `last_seen`).
- **No persistence in v1.** State lives in process memory. Disk-backed or remote state crosses module boundaries we don't have yet (telemetry sink, mission history); deferred until a second concrete consumer demands it.

**Status.** Locked. The `examples/local_sim_demo.py` script is the canonical reference for how to wire `WorldState` into a device.

---

## ADR-0006 — SafetyGate: enforce SafetyMode at the hardware boundary, dry_run is the floor — 2026-05-03

**Context.** M4 Phase 3 asked for "no path can accidentally actuate in `dry_run`, and stop always works." The motion handlers in `freemotion.agent.builtins` already check `cmd.safety` before calling into the controller, but the gate was at the handler layer only — a future contributor adding a new actuating handler can forget the check, and a per-command `safety` override could loosen the device's default rather than tightening it. The pressure was for a single, testable invariant: "in `dry_run`, `arm()` and `move()` cannot run on the controller, regardless of who tried."

**Decisions.**

- **`SafetyGate` is a `HardwareController` wrapper, not a new Protocol.** It satisfies the same interface and is composable with anything that implements it (mock, Pi, future Jetson, future ESP32). Composition over inheritance: nothing inside the controllers themselves needs to learn about `SafetyMode`.
- **The gate's safety mode is fixed at construction.** A runtime safety-mode change is a process-level event (restart with new env vars), not a per-command knob. Per-command `safety` stays a handler-layer concern. Constraint here keeps the safety floor unambiguous: if the gate is `dry_run`, no actuation happens, period.
- **Device default is the floor, command override is the ceiling.** A command saying `safety=bench` against a device configured `safety_default=dry_run` is refused at the gate. This inverts the historical permissive behavior, and is the right default: if an operator chose `dry_run` for the device, an inbound command shouldn't be able to override that.
- **`dry_run` refuses `arm()` and `move()`.** Both return `False` and log; the inner controller is never called. The motion handlers translate that `False` into a protocol-shaped `unsafe_in_mode` reply (their existing failure path).
- **`dry_run` permits `disarm()` and `stop()`.** Depowering is always the safer direction; refusing to drive a pin LOW offers no safety benefit and could leave a controller stuck armed if `dry_run` is enabled mid-flight (figuratively). `stop()` was already exempt per ADR-0004; `disarm()` joins it for symmetry.
- **`bench` and `live` pass through every method.** Distinguishing what each mode permits is the inner controller's job: a future motor controller can refuse motor-driving primitives in `bench` while permitting indicator pins. The gate doesn't pre-judge that distinction.
- **`state()` carries the active safety mode.** The gate stamps `safety: <mode>` into `state()` so `/status` telemetry exposes the runtime's effective safety floor without requiring callers to wire `Config` into the status handler separately.

**Status.** Locked. Wired into `examples/pi_bench_demo/`. `examples/mock_drone/` and `examples/pipe_check/` are not retrofitted: the mock has no real actuation to gate, and `pipe_check`'s LED handlers already gate on `dry_run` at the handler layer.

---

## ADR-0007 — YoloVision v1: ultralytics-backed, person-only, callable frame source, corner-based bbox — 2026-05-03

**Context.** Post-M4, the next bottleneck is real perception. The mock vision backend has been carrying the `VisionBackend` Protocol since M3; it was always going to be replaced when one real adapter forced the contract to clarify a few things (frame source, bbox convention, scope). YOLO is the obvious candidate: well-supported, CPU-runnable (nano variants ~6 MB), wide library coverage. The pressure was to land it without (a) blowing up CI runtime with a torch download, (b) bricking the rest of the runtime when YOLO is missing, or (c) over-engineering the v1 surface before a second real adapter exists.

**Decisions.**

- **`ultralytics` is the YOLO library.** De facto Python YOLO; same author lineage as the original repo; CPU and GPU paths in one API. Heavy deps (`torch`, `numpy`) live behind `pip install -e .[yolo]`. Base install stays stdlib + `python-telegram-bot`. ADR-0003's "real adapters land behind config flags, not extras-by-default" precedent is preserved.
- **`ultralytics` is imported lazily inside `__init__`.** The module imports cleanly on a host without it. If the import fails, the backend stays offline (`available is False`, `scene()` returns empty), the agent loop keeps running, and a warning is logged. Same defensive pattern as `PiHardwareController`.
- **Frame source is a caller-injected callable.** `YoloVision(frame_source=lambda: ...)`. The backend does **not** own the camera. Two reasons: tests stay trivial (`frame_source=lambda: object()` plus a `yolo_factory` fake — no `cv2`, no `picamera2`, no real frames), and contributors can plug in `cv2.VideoCapture`, `picamera2`, MJPEG, or a directory of test images without changing this file. The `VisionBackend` Protocol's "backend manages its own input" clause is preserved at the contract level — the *callable* is the input source, owned by the backend instance.
- **Person detection by default.** `classes=frozenset({"person"})`. The most-asked v1 use case ("follow person"); narrowing the default keeps unrelated YOLO classes (`bench`, `book`, `chair`, `dog`) out of the world-state hop without forcing every caller to filter. Override with `classes=[...]`; pass `classes=[]` to accept every label.
- **Confidence threshold passes through to `model(..., conf=...)`.** The library does the filtering; we don't re-implement it. Clamp to `[0.0, 1.0]` defensively. v1 default is `0.25` — Ultralytics's default — so behavior matches what users see in the `yolo predict` CLI.
- **bbox is `(x, y, w, h)` normalized 0..1, top-left corner-based.** Ultralytics returns center-based `xywhn`; we convert. Locking the convention now is cheap; locking it after a second adapter ships would mean retrofitting two backends. Corner-based wins because it's the more common downstream convention (PIL, OpenCV image crops, JSON annotation formats). Coords are clamped to `[0, 1]` so callers never see negatives from edge boxes.
- **`min_interval_s` is the cache contract.** The `VisionBackend` Protocol says `scene()` SHOULD be cheap. v1 ships the simplest possible version: a monotonic-clock throttle that returns the cached `VisionResult` if called inside the window. Default `0.0` (no throttle). A real per-frame freshness check (e.g. compare frame hashes) is deferred until a use case demands it.
- **No camera plumbing in this module.** `cv2`, `picamera2`, MJPEG sources, frame buffers — all live in example code. v1 keeps `freemotion/vision/yolo.py` ~200 lines and free of platform-specific I/O. When the second example ships (an `examples/yolo_follow/`-style demo), camera adapters can move into a small `freemotion/vision/sources/` if a pattern emerges.
- **`make_vision_from_config(cfg)` mirrors `make_controller_from_config`.** Same lazy-import discipline. `FREEMOTION_VISION_BACKEND` is parsed in `Config.from_env` (only `mock` / `yolo` valid in v1; unknown values warn and fall back to `mock`).

**Status.** Locked. Adapter ships under `freemotion/vision/yolo.py` with 24 CI-clean tests via fakes and one `pytest.importorskip("ultralytics")` smoke that runs only when the optional dep is installed. The `VisionBackend` Protocol stays frozen — `YoloVision` proves the contract is sufficient. A second real adapter (e.g. an ONNX-backed alternative for embedded hosts) would test that further; the interface is not yet considered final-final.

---

## ADR-0008 — GemmaMissionControl v1: transformers-backed, single decision, tolerant JSON parser, fail-offline — 2026-05-03

**Context.** Post-M4 and post-`YoloVision`, the next bottleneck was the decision layer. `MockMissionControl` had been carrying `MissionPolicy` since M3 with a tiny rule table that was always going to be replaced when one real adapter forced clarification on a few questions: which library, which output shape, which failure model, which scope. Gemma is the obvious candidate (open-weights instruction-tuned LLMs from Google, well-supported by `transformers`, smallest IT variant fits in single-digit GB). The pressure was the same as `YoloVision`'s: land it without (a) blowing up CI runtime with a torch download, (b) bricking the agent loop when the model is missing or crashes, or (c) over-engineering v1 before a second mission backend exists.

**Decisions.**

- **`transformers` is the LLM library.** Mainstream Hugging Face stack; native Gemma support; CPU and GPU paths in one API. Heavy deps (`transformers`, `torch`) live behind `pip install -e .[gemma]`. Base install stays stdlib + `python-telegram-bot`. ADR-0003's "real adapters land behind config flags, not extras-by-default" precedent is preserved. `llama.cpp` was considered for embedded/quantized inference and is a reasonable v2 backend; it adds a tooling layer (model conversion, GGUF) that v1 can't justify.
- **`transformers` is imported lazily inside `__init__`.** The module imports cleanly on a host without it. If the import or model load fails, the adapter stays offline (`available is False`, `plan()` returns an idle `MissionDecision` with a clear reason), the agent loop keeps running, and a warning is logged. Same defensive pattern as `PiHardwareController` and `YoloVision`.
- **`plan()` returns a single `MissionDecision`, never raises.** Any inference exception (`client.generate(...)` blowing up on CUDA OOM, model unload, malformed weights, network filesystem flakes, etc.) is caught and converted to `MissionDecision(next_command=None, ..., reason="inference error: ...")`. ADR-0003's "one concrete next action" constraint is preserved verbatim — `GemmaMissionControl` is not allowed to escalate the contract.
- **Output is parsed from a tolerant JSON-extraction step, not a constrained decoder.** v1 prompts the model with a JSON schema hint and asks for a single object back. We find the first balanced `{...}` block, `json.loads` it, then normalize: unknown commands → `None`; missing fields → defaults; non-mapping `args` → `{}`; out-of-range `confidence` → clamped to `[0, 1]`; unparseable input → idle with reason. A constrained decoder (`outlines`, `lm-format-enforcer`, JSON-mode adapters) was considered and deferred — it's another heavy dep, and the tolerant parser already handles every failure path the model creates in practice. Move to constrained decoding when the failure log shows enough valid-but-misaligned outputs to justify the dep.
- **`next_command` resolves against `CommandName`'s wire values.** The schema hint enumerates valid commands; the parser collapses anything unrecognized to `None`. New protocol commands automatically become available to the policy without code changes here. When the parser sets `next_command=None`, `args` is wiped — args attached to a rejected action would be misleading downstream.
- **`_LLMClient` is a one-method duck type:** `generate(prompt: str) -> str`. The default implementation wraps `transformers` (tokenizer + `AutoModelForCausalLM`, with the Gemma chat template applied when the tokenizer ships one). Tests inject a `_FakeLLM` directly. Decoupling the adapter from `transformers`'s surface area keeps the unit tests trivial and gives v2 backends (llama.cpp, vLLM, hosted endpoints) a one-method seam to fit through.
- **Default model is `google/gemma-2-2b-it`.** Smallest instruction-tuned Gemma 2 — the most plausible candidate for CPU-bound or modest-GPU hosts running this codebase. Override via `model=` on the constructor. Defaults for `max_new_tokens` (128) and `temperature` (0.1) come from the same "boring is correct" line: enough budget for a small JSON object, low enough temperature to keep parsing reliable.
- **Prompt construction is a free function.** `build_prompt(intent, scene, world)` is importable and testable in isolation. Same logic for `parse_decision`. Pulling these out of the class kept the unit tests one-liners and drew a clean line between LLM I/O (the class) and the prompt/output contract (the functions).
- **No real-dep smoke test.** `transformers` is heavy enough that some local installs hang or SIGFPE on `import transformers` in ways that even subprocess-isolated probes (`subprocess.run(timeout=...)`) can't escape — the child can wedge in uninterruptible kernel state. The structural tests via `gemma_factory` injection cover the full contract; a real-dep smoke test would only validate that the host's `transformers` install isn't broken, which is not this codebase's responsibility. CI's "import smoke" step still imports the `freemotion.mission_control` module to confirm the lazy-import path stays clean.
- **`make_mission_from_config(cfg)` mirrors `make_vision_from_config` and `make_controller_from_config`.** Same lazy-import discipline. `FREEMOTION_MISSION_BACKEND` is parsed in `Config.from_env` (only `mock` / `gemma` valid in v1; unknown values warn and fall back to `mock`).

**Status.** Locked. Adapter ships under `freemotion/mission_control/gemma.py` with 37 CI-clean tests in [`tests/test_mission_gemma.py`](../tests/test_mission_gemma.py) (protocol satisfaction, offline degradation, prompt construction, JSON parsing, command normalization, factory selection, and the lazy-import escape hatch via monkeypatched `sys.modules`). The `MissionPolicy` Protocol stays frozen — `GemmaMissionControl` proves the contract is sufficient. A second real adapter (e.g. a llama.cpp-backed alternative for embedded hosts, or a hosted-endpoint adapter for cloud inference) would test that further; the interface is not yet considered final-final.

---

## ADR-0009 — PiCameraSource v1: picamera2-backed, callable frame producer, transient-failure tolerant — 2026-05-04

**Context.** `YoloVision` shipped (ADR-0007) with a deliberate seam: a caller-injected `frame_source: Callable[[], Any]`. That kept the v1 vision adapter ~200 lines and free of platform-specific I/O. With the YOLO and Gemma adapters real, the next bottleneck on the Pi path was equally real perception input — i.e. an actual camera feeding live frames into `YoloVision.scene()`. The alternative ("everyone writes their own `frame_source` lambda") works for an example or two; it does not scale to a closed-loop demo where camera lifecycle, transient failure handling, and resolution config all need to be the same across files. The pressure was to land **one canonical Pi camera adapter** without (a) bricking the runtime when picamera2 is absent, (b) coupling the source to YOLO directly (the source must work for any consumer), or (c) over-engineering v1 before a second camera backend (USB / RTSP / Jetson CSI) exists.

**Decisions.**

- **`picamera2` is the Pi camera library.** Official, libcamera-based, the only stack supported on Pi OS Bookworm and newer. Heavy/Pi-only deps live behind `pip install -e .[picam]`. ADR-0003's "real adapters land behind config flags, not extras-by-default" precedent is preserved, and the base install stays stdlib + `python-telegram-bot`. The legacy `picamera` (mmal-based) is dead on Bookworm; supporting both would double the surface for zero practical gain.
- **`picamera2` is imported lazily inside `__init__`.** Module imports cleanly on a host without it (CI, dev laptop, Jetson). Same defensive pattern as `PiHardwareController` (ADR-0006), `YoloVision` (ADR-0007), and `GemmaMissionControl` (ADR-0008). Failure modes (import missing, camera busy, configure raises, start raises) all flip the source offline rather than crashing. The agent loop never sees a camera-induced exception.
- **`PiCameraSource` is callable, not a `VisionBackend`.** It does not implement `scene()` and is not a peer of `YoloVision` / `MockVision`. It's a **frame producer** that satisfies the existing `frame_source: Callable[[], Any]` seam on `YoloVision`. That keeps the source's responsibility narrow (capture frames, return `None` on failure, support `close()`), and lets it compose with future consumers (a frame logger, an alternative inference adapter, a multi-consumer fanout) without subclassing or rewriting. Crucially, it **does not** give `YoloVision` a "Pi knows about camera" leak — `YoloVision` still doesn't import `picamera2`.
- **Per-call capture failures do not flip the source offline.** A single bad frame returns `None` for that call; the source stays available for the next call. This matches how real cameras behave (the occasional dropped frame is normal). The total `capture_failures` counter is exposed as a property so the closed-loop demo (Step 2) can surface it in `/status` telemetry without scraping logs. **Construction failures**, on the other hand, are sticky: if `start()` raised, the source is offline forever — calling `__call__` again won't magically fix a camera that wasn't there at boot.
- **The constructor never leaks a partial camera handle.** A failed `configure()` or `start()` calls `stop()` + `close()` on the partially initialized picam before flipping the source offline. Without this, a re-run of the demo after a failed first start would find the camera busy.
- **Resolution is fixed at construction.** picamera2 supports runtime reconfiguration, but mid-stream resolution changes are an order of magnitude more complex than this v1 needs. Set `resolution=(w, h)` once; if you need a different resolution, build a new source. The default `(640, 480)` matches the YOLO nano sweet spot — low enough that capture + inference fits in ~200 ms on a Pi 4 CPU, high enough that person detection is reliable at room scale.
- **`close()` is idempotent and never raises.** Examples, tests, and systemd shutdowns all need to call it without ceremony. Multiple `close()` calls hit the same `_closed` flag and return; underlying `stop()`/`close()` exceptions are caught and logged.
- **Per-call capture does NOT acquire the source's lock.** The lock guards the start/close lifecycle only. A slow `capture_array()` (which can take 10–50 ms on a real Pi) must not block `close()` from a SIGTERM handler, must not block `available` readers from a `/status` handler, and must not block any future thread that wants to read `capture_failures`. This is the architectural pre-requisite for "Step 1 acceptance criterion: `/status` still works while camera is active."
- **No camera plumbing inside `YoloVision`.** The vision module continues to know nothing about picamera2, OpenCV, MJPEG, or any specific source. Adding the source as a sibling module under `freemotion/vision/picamera.py` (rather than under a new `freemotion/sources/` subpackage) is a v1 simplification: with one source, a flat layout is clearest. When a second source ships (USB, RTSP), they can move into `freemotion/vision/sources/` together — the `__init__.py` re-export keeps the public surface stable.
- **Real-dep smoke test imports `picamera2` only.** It does **not** instantiate `PiCameraSource()` — `picamera2.Picamera2()` will fail on any host that isn't a Pi with a wired-in CSI camera, and we don't want the test suite gating on that. Verifying that the import path doesn't blow up the runner is enough; the structural tests via `picam_factory` injection cover the rest.
- **USB webcams are explicitly out of scope for this adapter.** They work just fine via `cv2.VideoCapture(0).read`-shaped lambdas — no wrapper class is needed. Adding one would be cargo-culting `PiCameraSource`'s shape onto a backend that doesn't share its lifecycle quirks (libcamera vs. v4l2 vs. AVFoundation). When a second canonical source is needed, ADR-0010 will record the design.

**Status.** Locked. Source ships under `freemotion/vision/picamera.py` with 17 CI-clean tests via fakes plus one `pytest.importorskip("picamera2")` smoke that runs only when the optional dep is installed. The standalone `examples/pi_camera_demo/` proves the live-camera + YOLO pair end-to-end and is the prerequisite for `examples/pi_closed_loop_demo/` (Step 2: Telegram → YOLO → world → Gemma → hardware → status).

## Pending

If you make an architectural call that future contributors will ask "why?" about, write a four-line ADR here. Bias toward writing them down. Reverse-engineering decisions is more expensive than recording them.
