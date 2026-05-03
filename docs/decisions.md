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
- **`MissionPolicy.plan` takes vision + world as inputs.** Mission control can react to scene state without owning the camera. World state (`freemotion.world`, M3) becomes the carrier for everything else (current_state, last_seen, next_action). Until that lands, callers pass `world={}`.
- **Real adapters land behind config flags, not extras-by-default.** `FREEMOTION_VISION_BACKEND=mock|yolo` and `FREEMOTION_MISSION_BACKEND=mock|gemma`, defaulting to mock. The flags themselves don't ship until the adapters do — adding flags before they're meaningful would be cargo culting.
- **Heavy deps go behind `pyproject.toml` extras.** `pip install -e .[yolo]` and `pip install -e .[gemma]`. The base install stays stdlib + `python-telegram-bot`, the same as today. Tests for real adapters skip cleanly when their dep isn't installed.

**Status.** Locked. Real adapters are tracked as separate issues in [`docs/issues/m2-m3.md`](issues/m2-m3.md) (#3 and #4). The interfaces stay frozen until at least one real adapter on each side ships and tells us what's missing.

## Pending

If you make an architectural call that future contributors will ask "why?" about, write a four-line ADR here. Bias toward writing them down. Reverse-engineering decisions is more expensive than recording them.
