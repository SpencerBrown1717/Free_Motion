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

## Pending

If you make an architectural call that future contributors will ask "why?" about, write a four-line ADR here. Bias toward writing them down. Reverse-engineering decisions is more expensive than recording them.
