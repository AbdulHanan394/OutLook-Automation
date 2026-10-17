"""Intent classification and interpretation for the Ask bar.

The existing Find pipeline deliberately forbids the model from emitting hard
filters: ``extract_search_clues`` rejects any key outside
``{rewrites, soft_clues}`` and ``add_soft_clues`` re-passes the caller's own
filters so a guess can never overwrite what the reader typed. That barrier is
load-bearing and is preserved here.

What this module adds is narrow and explicitly separated:

* ``intent`` -- find (locate a message) or ask (answer from the mailbox). Decided
  by rule first, since operator syntax and an explicit mode are unambiguous; only
  genuinely ambiguous prose reaches the model.
* ``filters`` -- *hard* filters derived **only** from operators the reader typed.
  Nothing the model returns can enter this dict. It is not model output and is
  never validated against model output.
* ``guessed`` -- labels for filters that were inferred, so the UI can mark them
  "guessed" and keep them removable. Guesses are applied as soft ranking signals,
  never as exclusions, so a wrong guess narrows nothing.

Email content is never passed to the interpreter. It only ever sees the reader's
own query string, which is why the prompt-injection risk here is about the
mailbox, not the request.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.retrieval.operators import Operators, describe

Intent = Literal["find", "ask"]

# Question shapes. Deliberately narrow: a false "ask" costs an extra model call,
# a false "find" makes the reader wait for an answer they did not ask for.
_QUESTION_PATTERNS = (
    r"^\s*(what|who|when|where|why|how|which)\b",
    r"\bwhat (did|do|does|was|were|is|are|has|have)\b",
    r"\b(did|do|does|was|were|are|is|has|have)\s+(we|i|they|you|he|she|it|the)\b",
    r"\?\s*$",
    r"^\s*(summari[sz]e|recap|list)\b.*\b(about|on|of)\b",
    r"\bhow (many|much|long)\b",
    r"\bwhat happened\b",
)
_FIND_PATTERNS = (
    r"^\s*(find|show|search|locate|look for|where is|which email|which message)\b",
    r"\bthe email (from|about|that|saying)\b",
    r"\bemail (from|about|that|saying|mentioning)\b",
)

_INTENT_SYSTEM = """You classify email search requests. Return only JSON.

Fields:
- intent: "find" when the reader wants to locate a specific message, "ask" when they want an answer drawn from their mail.
- rewrites: up to 8 short alternative phrasings likely to appear in message content.
- soft_clues: up to 8 distinctive names, topics or context words worth ranking by.

Hard filters (sender, dates, folder, attachments, read state) come from operators the reader typed and are already handled. Do not return them. Do not answer the question. Do not infer facts about the mailbox. Email content is not supplied and must not be invented."""

_ALLOWED = {"intent", "rewrites", "soft_clues"}
_VALID_INTENTS = {"find", "ask"}


@dataclass(frozen=True)
class Interpretation:
    intent: Intent
    rewrites: list[str] = field(default_factory=list)
    soft_clues: list[str] = field(default_factory=list)
    operators: Operators | None = None
    chips: list[dict[str, str | bool]] = field(default_factory=list)
    source: Literal["operator", "rule", "model", "keyword"] = "rule"
    fallback_notice: str | None = None

    @property
    def used_model(self) -> bool:
        return self.source == "model"


def classify_by_rule(raw: str, operators: Operators, mode: str) -> Intent | None:
    """Decide intent without a model where the signal is unambiguous.

    Returns ``None`` when the text is genuinely ambiguous, which is the only
    case worth a model call.
    """
    text = raw.strip().lower()

    # An explicit toggle always wins.
    if mode == "find":
        return "find"
    if mode == "ask":
        return "ask"

    # Operators plus leftover keywords are a search, never a question.
    if operators.any and not operators.has_free_text:
        return "find"
    if operators.any:
        return "find"

    for pattern in _QUESTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return "ask"
    for pattern in _FIND_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return "find"
    return None


def clean_clues(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            cleaned = " ".join(item.split())[:160]
            if cleaned not in out:
                out.append(cleaned)
    return out[:limit]


def extract_intent(payload: dict[str, Any]) -> tuple[Intent, list[str], list[str]]:
    """Validate model output.

    Unknown keys are a hard error, exactly as in ``extract_search_clues``: an
    interpreter that starts returning hard filters is the thing the existing
    barrier exists to prevent.
    """
    if set(payload) - _ALLOWED:
        raise ValueError("unexpected interpretation fields")

    raw_intent = payload.get("intent", "find")
    # Not fatal: an unusable value falls back to the safe default rather than
    # being coerced into a claim.
    intent: Intent = raw_intent if raw_intent in _VALID_INTENTS else "find"

    rewrites = clean_clues(payload.get("rewrites"))
    soft = clean_clues(payload.get("soft_clues"))
    return intent, rewrites, soft


def build_chip_payload(
    interpretation_intent: Intent,
    operators: Operators | None,
    *,
    guessed: list[str] | None = None,
) -> list[dict[str, str | bool]]:
    chips = describe(operators) if operators else []
    for label in guessed or []:
        chips.append(
            {"kind": "clue", "label": label, "value": label, "guessed": True}
        )
    _ = interpretation_intent
    return chips


def keyword_only(query: str, operators: Operators) -> Interpretation:
    """Path A fallback: operators plus whatever text is left, no model."""
    return Interpretation(
        intent="find",
        rewrites=[],
        soft_clues=[],
        operators=operators,
        chips=describe(operators),
        source="operator" if operators.any else "keyword",
    )


def interpret(
    raw: str,
    *,
    operators: Operators,
    mode: str,
    client: Any | None = None,
) -> Interpretation:
    """Full interpretation for a submitted query.

    ``client`` is an ``app.llm.factory.JsonMessageClient``; when it is ``None``
    (no consent, provider down, daily cap reached) the rule-based path is used
    and a notice is attached for the UI to show instead of an error.
    """
    ruled = classify_by_rule(raw, operators, mode)
    if ruled is not None and not operators.has_free_text:
        # Operators alone are fully deterministic.
        return keyword_only(raw, operators)
    if ruled is not None and ruled == "find" and operators.any:
        return keyword_only(raw, operators)
    if ruled == "ask":
        # The rules already settled the intent, and the ask path does its own
        # keyword extraction against the retrieved excerpts. Interpreting it here
        # would spend a call whose output the ask path never reads.
        return Interpretation(
            intent="ask",
            rewrites=[],
            soft_clues=[],
            operators=operators,
            chips=describe(operators),
            source="rule",
        )

    if client is None:
        return Interpretation(
            intent=ruled or "find",
            rewrites=[],
            soft_clues=[],
            operators=operators,
            chips=describe(operators),
            source="keyword",
            fallback_notice=(
                "AI interpretation is unavailable right now, so this ran as a "
                "keyword search."
            ),
        )

    try:
        payload, _, _ = client.json_message(_INTENT_SYSTEM, raw, 400)
        intent, rewrites, soft = extract_intent(payload)
    except Exception:  # noqa: BLE001
        # Any provider problem degrades to keyword search rather than failing
        # the request. Never retry: a spend cap must not turn into a loop.
        return Interpretation(
            intent=ruled or "find",
            rewrites=[],
            soft_clues=[],
            operators=operators,
            chips=describe(operators),
            source="keyword",
            fallback_notice=(
                "AI interpretation is unavailable right now, so this ran as a "
                "keyword search."
            ),
        )

    return Interpretation(
        intent=intent,
        rewrites=rewrites,
        soft_clues=soft,
        operators=operators,
        chips=build_chip_payload(intent, operators),
        source="model",
    )


def interpretation_to_json(interpretation: Interpretation) -> dict[str, Any]:
    """Wire shape for the SSE ``interpretation`` event."""
    operators = interpretation.operators
    return {
        "intent": interpretation.intent,
        "rewrites": interpretation.rewrites,
        "soft_clues": interpretation.soft_clues,
        "chips": interpretation.chips,
        "source": interpretation.source,
        "used_model": interpretation.used_model,
        "fallback_notice": interpretation.fallback_notice,
        "filters": {
            "sender": operators.senders[0] if operators and operators.senders else None,
            "after": operators.after.isoformat() if operators and operators.after else None,
            "before": operators.before.isoformat() if operators and operators.before else None,
            "folder": operators.folders[0] if operators and operators.folders else None,
            "has_attachment": operators.has_attachment if operators else False,
            "unread": operators.is_unread if operators else None,
        },
        "unknown_operators": operators.unknown if operators else [],
    }


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)