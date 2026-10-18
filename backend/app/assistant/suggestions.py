"""Follow-up suggestions: what this reader might reasonably ask next.

Suggestions are *offered*, never applied. Nothing here writes into the composer,
and the reader can always ignore the list -- the chat works identically without
it. That is the difference between a suggestion and an assistant prefill, which
the design explicitly rules out.

They come from the cheap interpret model, not the answer model: a suggestion is
a label, not an answer, and spending the answer budget on three one-line
questions would be the wrong use of the cap.

Grounded in two things only -- the conversation so far and the subject lines
already retrieved -- so a suggestion cannot invent a thread the reader has not
seen. The model is instructed to produce questions only; anything that is not a
question is dropped rather than shown.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

import anthropic
import httpx
from sqlalchemy.orm import Session

from app.assistant import store
from app.config import Settings
from app.llm.client import JsonMessageClient

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
MAX_SUGGESTION_CHARS = 120
MIN_SUGGESTION_CHARS = 8

SUGGEST_SYSTEM = """You suggest follow-up questions a person might ask about their own email.

Return only a JSON object with a key "suggestions" holding an array of at most three short questions. Each must be a question the person could ask next in this conversation, phrased the way they would ask it. Base them only on what has been discussed; do not invent threads, people or topics. No preamble, no numbering."""

# A suggestion that is not a question is not a suggestion. Cheap to check and
# it stops a label like "Summary of the thread" being offered as something to
# ask.
QUESTIONISH = re.compile(r"^(what|who|when|where|which|why|how|is|are|was|were|did|does|do|can|could|should|would|has|have|had|any)\b", re.IGNORECASE)


def _clean(candidate: Any) -> str | None:
    if not isinstance(candidate, str):
        return None
    text = " ".join(candidate.split()).strip(" ?!.")
    if len(text) < MIN_SUGGESTION_CHARS or len(text) > MAX_SUGGESTION_CHARS:
        return None
    if not QUESTIONISH.match(text):
        return None
    return f"{text}?"


def build_context(
    *, question: str | None, answer: str | None, subjects: list[str], notes: list[str]
) -> str:
    """What the suggester is allowed to draw on.

    Subjects and stated preferences only. No message bodies: a suggestion must
    not be generated from mail the reader has not been shown.
    """
    parts: list[str] = []
    if question:
        parts.append(f"The person just asked: {question}")
    if answer:
        parts.append(f"The assistant answered: {answer[:600]}")
    if subjects:
        parts.append(
            "Emails under discussion:\n"
            + "\n".join(f"- {subject}" for subject in subjects[:6])
        )
    if notes:
        parts.append(
            "Preferences the person has stated:\n"
            + "\n".join(f"- {note}" for note in notes[:5])
        )
    return "\n\n".join(parts)


def suggest(
    db: Session,
    *,
    user_id: UUID,
    conversation_id: UUID,
    settings: Settings,
    client: JsonMessageClient | None,
) -> list[str]:
    """Suggest up to three follow-ups, or return nothing.

    Never raises. A suggestion is optional; if the provider is down or the cap
    is spent the reader simply gets an empty list and a working chat.
    """
    if client is None:
        return []

    messages = store.list_messages(db, user_id=user_id, conversation_id=conversation_id)
    if not messages:
        return []

    last_user = next((m.text for m in reversed(messages) if m.role == "user"), None)
    last_assistant = next((m.text for m in reversed(messages) if m.role == "assistant"), None)

    subjects: list[str] = []
    for message in reversed(messages):
        for citation in message.citations_json or []:
            if isinstance(citation, dict):
                subject = citation.get("subject")
                if isinstance(subject, str) and subject not in subjects:
                    subjects.append(subject)

    context = build_context(
        question=last_user,
        answer=last_assistant,
        subjects=subjects,
        notes=_notes(db, user_id=user_id),
    )
    if not context.strip():
        return []

    try:
        payload, _, _ = client.json_message(SUGGEST_SYSTEM, context, max_tokens=400)
    except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.info(
            "Follow-up suggestions unavailable using provider %s",
            settings.llm_provider,
        )
        return []

    raw = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[str] = []
    for candidate in raw:
        cleaned = _clean(candidate)
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def _notes(db: Session, *, user_id: UUID) -> list[str]:
    from app.assistant import memory

    try:
        return [note.note_text for note in memory.list_notes(db, user_id=user_id)]
    except Exception:
        logger.warning("Could not read stated preferences for suggestions", exc_info=True)
        return []