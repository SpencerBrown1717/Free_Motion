"""Gemma mission-control backend (post-M4, first real decision adapter).

Implements `MissionPolicy` against a small Gemma instruction-tuned LLM
served through `transformers`. v1 scope is intentionally narrow per
ADR-0008:

- One inference per `plan()` call. No multi-step plans.
- Output shape is a single `MissionDecision`. The LLM is asked to
  produce a small JSON object; we extract, parse, and normalize it.
  Anything we can't normalize collapses to an idle decision instead of
  raising.
- Heavy deps (`transformers`, `torch`) live behind ``pip install -e
  .[gemma]``. We import them **lazily** inside ``__init__`` so this
  module is safe to import on any host. Tests inject a fake LLM via
  the ``gemma_factory`` arg.

The LLM-shaped seam is intentionally tiny: anything with a
``generate(prompt: str) -> str`` method is a valid client. That keeps
this module decoupled from the Hugging Face surface area and makes
unit tests a one-liner.

Failure model, in order:

1. `transformers` not installed → backend is offline; ``plan()``
   returns an idle decision with a clear reason.
2. Model load (`gemma_factory`) raises → backend is offline; same
   behavior.
3. Inference (``client.generate``) raises → idle decision with the
   exception summarized in ``reason``.
4. Output isn't valid JSON, or has fields we don't recognize → idle
   decision; nothing crashes upstream.

The agent loop never sees a Gemma-induced exception.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Mapping, Optional

from freemotion.protocol import CommandName
from freemotion.vision import VisionResult
from freemotion.world import WorldStateSnapshot

from .interface import MissionDecision

LOG = logging.getLogger("freemotion.mission_control.gemma")

# Names the LLM is allowed to choose from. Anything else collapses to
# `next_command=None`. Keep this in sync with `CommandName` so newly
# added wire commands become available to the policy automatically.
_VALID_COMMANDS: frozenset[str] = frozenset(c.value for c in CommandName)


def _idle(reason: str, *, confidence: float = 0.0) -> MissionDecision:
    """Canonical "do nothing this tick" decision."""
    return MissionDecision(
        next_command=None,
        args={},
        reason=reason,
        confidence=confidence,
    )


def build_prompt(
    *,
    intent: str,
    scene: VisionResult,
    world: WorldStateSnapshot,
) -> str:
    """Render a deterministic prompt the LLM can answer with a small
    JSON object. Pulled out as a free function so tests can pin its
    shape without instantiating the whole adapter.
    """
    detections = [
        {
            "label": d.label,
            "confidence": round(float(d.confidence), 3),
            "bbox": [round(float(v), 3) for v in d.bbox],
        }
        for d in scene.detections
    ]
    world_summary = {
        "target": world.target,
        "current_state": world.current_state,
        "confidence": round(float(world.confidence), 3),
        "last_seen": dict(world.last_seen),
    }
    schema_hint = (
        '{"next_command": "<one of: '
        + ", ".join(sorted(_VALID_COMMANDS))
        + ' or null>", '
        '"args": {<command-specific kwargs, may be empty>}, '
        '"reason": "<one short sentence>", '
        '"confidence": <float in [0, 1]>}'
    )
    return (
        "You are the mission control of a small robot. Choose ONE next "
        "action based on the user's intent, the vision scene, and the "
        "world state. Reply with a single JSON object matching this "
        "schema and nothing else:\n"
        f"{schema_hint}\n"
        "Use null for next_command when no action is appropriate. The "
        "`stop` command is always safe; prefer it when uncertain and "
        "the user clearly wants the robot to halt.\n\n"
        f"intent: {intent!r}\n"
        f"scene.detections: {json.dumps(detections, sort_keys=True)}\n"
        f"world: {json.dumps(world_summary, sort_keys=True)}\n"
        "JSON:"
    )


def parse_decision(raw: str) -> MissionDecision:
    """Pull a `MissionDecision` out of the LLM's text response.

    Tolerant by design: extracts the first balanced ``{...}`` block,
    accepts missing fields, ignores unknown commands, clamps
    confidence to ``[0, 1]``. Anything we can't parse cleanly returns
    an idle decision with a reason.
    """
    if not raw or not raw.strip():
        return _idle("model returned empty response")

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return _idle("model output had no JSON object")

    payload = raw[start : end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return _idle(f"JSON parse failed: {exc.msg}")

    if not isinstance(data, Mapping):
        return _idle("model JSON wasn't an object")

    return _normalize(data)


def _normalize(data: Mapping[str, Any]) -> MissionDecision:
    raw_cmd = data.get("next_command")
    next_command: Optional[CommandName] = None
    if isinstance(raw_cmd, str):
        candidate = raw_cmd.strip().lower()
        if candidate in _VALID_COMMANDS:
            next_command = CommandName(candidate)

    raw_args = data.get("args")
    if isinstance(raw_args, Mapping):
        args: dict[str, Any] = {str(k): v for k, v in raw_args.items()}
    else:
        args = {}

    raw_reason = data.get("reason")
    reason = str(raw_reason) if raw_reason is not None else ""

    raw_conf = data.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(raw_conf)))
    except (TypeError, ValueError):
        confidence = 0.0

    # `next_command=None` is the explicit idle signal. Wipe any args
    # the model offered for an action it didn't actually pick — they'd
    # only be misleading downstream.
    if next_command is None:
        args = {}

    return MissionDecision(
        next_command=next_command,
        args=args,
        reason=reason,
        confidence=confidence,
    )


class _TransformersClient:
    """Default `_LLMClient`: a Hugging Face Gemma model + tokenizer.

    Constructed lazily on first request. Any import or load failure
    propagates so the outer adapter can flip to offline.
    """

    def __init__(
        self,
        model_id: str,
        *,
        max_new_tokens: int,
        temperature: float,
    ) -> None:
        # transformers is a heavy import; only paid for here, after
        # the user has explicitly opted into the gemma backend.
        from transformers import (  # type: ignore[import-not-found]
            AutoModelForCausalLM,
            AutoTokenizer,
        )

        self._tokenizer = AutoTokenizer.from_pretrained(model_id)
        self._model = AutoModelForCausalLM.from_pretrained(model_id)
        self._max_new_tokens = max_new_tokens
        self._temperature = temperature

    def generate(self, prompt: str) -> str:
        tokenizer = self._tokenizer
        # Use the chat template when the tokenizer ships one (Gemma
        # IT does); fall back to the plain prompt otherwise so this
        # client still works against future base-model checkpoints
        # someone might wire in.
        try:
            formatted = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}],
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception:  # pragma: no cover - tokenizer-specific
            formatted = prompt

        inputs = tokenizer(formatted, return_tensors="pt")
        outputs = self._model.generate(
            **inputs,
            max_new_tokens=self._max_new_tokens,
            do_sample=self._temperature > 0,
            temperature=self._temperature,
        )
        # Strip the prompt tokens off the front so we only decode the
        # model's reply.
        prompt_len = inputs["input_ids"].shape[1]
        new_tokens = outputs[0][prompt_len:]
        return tokenizer.decode(new_tokens, skip_special_tokens=True)


class GemmaMissionControl:
    """`MissionPolicy` backed by an instruction-tuned Gemma model.

    Construction never raises on missing dependencies, missing
    weights, or transformer init failures. If anything goes wrong
    during construction, the adapter stays offline:
    ``available is False`` and ``plan()`` returns an idle decision
    with a clear reason.
    """

    name = "gemma"

    DEFAULT_MODEL = "google/gemma-2-2b-it"
    DEFAULT_MAX_NEW_TOKENS = 128
    DEFAULT_TEMPERATURE = 0.1

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        gemma_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._model_id = model or self.DEFAULT_MODEL
        self._max_new_tokens = max(1, int(max_new_tokens))
        self._temperature = max(0.0, float(temperature))

        self._client: Any = None
        self._ready = False
        self._offline_reason = ""

        if gemma_factory is None:
            try:
                # Verify the heavy dep is present without paying for
                # the model load yet; the actual load happens below.
                import transformers  # type: ignore[import-not-found]  # noqa: F401
            except Exception as exc:  # pragma: no cover - non-gemma path
                self._offline_reason = (
                    f"transformers unavailable ({exc}); "
                    "install with `pip install -e .[gemma]`"
                )
                LOG.warning(
                    "%s; GemmaMissionControl is offline", self._offline_reason
                )
                return

            def gemma_factory(path: str) -> Any:  # type: ignore[misc]
                return _TransformersClient(
                    path,
                    max_new_tokens=self._max_new_tokens,
                    temperature=self._temperature,
                )

        try:
            self._client = gemma_factory(self._model_id)
            self._ready = True
        except Exception as exc:
            self._offline_reason = f"Gemma model load failed ({exc})"
            LOG.warning(
                "%s; GemmaMissionControl is offline", self._offline_reason
            )
            self._client = None
            self._ready = False

    @property
    def available(self) -> bool:
        return self._ready

    @property
    def model_id(self) -> str:
        return self._model_id

    def plan(
        self,
        *,
        intent: str,
        scene: VisionResult,
        world: WorldStateSnapshot,
    ) -> MissionDecision:
        if not self._ready:
            reason = self._offline_reason or "GemmaMissionControl is offline"
            return _idle(reason)

        prompt = build_prompt(intent=intent, scene=scene, world=world)
        try:
            raw = self._client.generate(prompt)
        except Exception as exc:
            LOG.warning("Gemma inference failed: %s", exc)
            return _idle(f"inference error: {exc}")

        if not isinstance(raw, str):
            return _idle("model client returned a non-string response")

        return parse_decision(raw)
