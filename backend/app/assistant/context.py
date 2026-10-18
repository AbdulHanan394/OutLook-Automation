"""What the assistant remembers about a conversation while answering a turn.

Two things, kept deliberately separate:

* **Transcript turns** -- the recent question/answer pairs, verbatim. This is
  what makes "what about the second one?" mean anything.
* **A running summary** -- older turns, folded into prose once they fall out of
  the transcript window, so a long chat does not grow without bound.

Neither ever contains an email body. Excerpts are re-read from ``emails`` when
a turn is answered, and a turn's stored text is the question and the verified
answer only. The working set holds email ids, nothing else.

The summary is produced by the *interpret* model, not the answer model: it is
bookkeeping, not an answer, and spending the answer budget on it would make the
cap go further on the wrong work.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import store
from app.config import Settings
from app.db import ChatMessage, Email, set_user_scope
from app.llm.client import JsonMessageClient

logger = logging.getLogger(__name__)

# Turns kept verbatim in the prompt. A follow-up needs the shape of the recent
# exchange, not the whole chat; older material is represented by the summary.
RECENT_TURNS = 6
# Above this many messages the summary is refreshed. Folding on every turn would
# spend an interpret call per question for a conversation nobody has finished.
FOLD_EVERY = 12
# Cap on the stored summary, so a pathological chat cannot grow it forever.
MAX_SUMMARY_CHARS = 4000

SUMMARY_SYSTEM = """You are keeping a running summary of a conversation between a person and an assistant that answers questions about their own email.

Write a compact handover summary in plain prose. Preserve: what was asked, what was concluded, names of people and projects, dates and deadlines, decisions made, and anything still open. Do not add anything that was not said. Do not answer questions. Do not invent detail. Return only a JSON object with a single string field named "summary"."""

FOLD_SYSTEM = """You are updating a running summary of a conversation between a person and an assistant that answers questions about their own email.

Merge the existing summary with the new turns. Keep what is still relevant, drop what has been superseded, and preserve open questions and commitments. Never invent anything. Return only a JSON object with a single string field named "summary"."""


def _escape(text: str) -> str:
    """Neutralise anything that could look like a block delimiter."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class Turn:
    """One question/answer pair, as the assistant remembers it."""

    role: str
    text: str
    created_at: datetime


@dataclass
class ConversationContextView:
    """Everything the answer prompt needs to know about the conversation so far."""

    summary: str | None = None
    recent: list[Turn] = field(default_factory=list)
    working_set: list[str] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        """Render as untrusted, clearly delimited context for the model.

        Conversation text is the reader's own words, which is exactly what a
        prompt-injection payload looks like, so angle brackets are neutralised
        and the whole thing is labelled as data. The model is told to treat it
        as material to answer about, never as instructions.

        ``escape_email_content`` is deliberately *not* used here: it only folds
        ``</email>``, which is the right rule for an excerpt but the wrong one
        for these delimiters -- a ``</recent_turns>`` in the transcript would
        otherwise close the block and let the rest of the reader's text read as
        instructions. Claims are verified against excerpts, never against this
        text, so escaping it costs nothing.
        """
        parts: list[str] = []
        if self.summary:
            parts.append(
                "<conversation_summary>\n"
                f"{_escape(self.summary)}\n"
                "</conversation_summary>"
            )
        if self.recent:
            lines = []
            for turn in self.recent:
                who = "Person" if turn.role == "user" else "Assistant"
                lines.append(f"{who}: {_escape(turn.text)}")
            parts.append(
                "<recent_turns>\n" + "\n".join(lines) + "\n</recent_turns>"
            )
        return "\n\n".join(parts)


def load(
    db: Session, *, user_id: Any, conversation_id: Any, limit: int = RECENT_TURNS
) -> ConversationContextView:
    """Read the stored rolling state for one conversation."""
    summary, _ = store.summary_state(db, user_id=user_id, conversation_id=conversation_id)
    messages = store.list_messages(db, user_id=user_id, conversation_id=conversation_id)
    # Only the tail matters for follow-ups; the head is represented by `summary`.
    tail = messages[-limit * 2 :] if len(messages) > limit * 2 else messages
    return ConversationContextView(
        summary=summary,
        recent=[Turn(role=m.role, text=m.text, created_at=m.created_at) for m in tail],
        working_set=store.working_set(db, user_id=user_id, conversation_id=conversation_id),
    )


def resolve_working_set(
    db: Session, *, user_id: Any, conversation_id: Any, ids: list[str]
) -> list[Email]:
    """Re-read the working set from ``emails``.

    Ids only are stored, so this is where a stale or deleted id simply drops out
    instead of the chat holding a copy of mail that may no longer exist. Scoped
    twice on purpose: the explicit ``user_id`` predicate and the RLS policy.
    """
    set_user_scope(db, user_id)
    wanted = [str(item) for item in ids]
    if not wanted:
        return []
    rows = db.scalars(
        select(Email).where(
            Email.user_id == user_id,
            Email.id.in_(wanted),
            ~Email.is_deleted,
        )
    )
    found = {str(row.id): row for row in rows}
    # Preserve the order the assistant was given, so "the second one" still means
    # the second one.
    return [found[item] for item in wanted if item in found]


def message_count(db: Session, *, user_id: Any, conversation_id: Any) -> int:
    set_user_scope(db, user_id)
    rows = db.scalars(
        select(ChatMessage.id).where(
            ChatMessage.user_id == user_id,
            ChatMessage.conversation_id == conversation_id,
        )
    )
    return len(list(rows))


def maybe_fold(
    db: Session,
    *,
    user_id: Any,
    conversation_id: Any,
    client: JsonMessageClient | None,
    settings: Settings,
) -> str | None:
    """Refresh the summary when the transcript has grown past the fold interval.

    Best effort by design. A summary is an optimisation for a long chat, not a
    correctness requirement, so a provider failure leaves the existing summary
    in place and the turn proceeds. It never raises.
    """
    if client is None:
        return None
    total = message_count(db, user_id=user_id, conversation_id=conversation_id)
    # Fold at every threshold crossing, and never before one. Using <= here
    # meant a chat that landed exactly on the interval never summarised at all.
    if total < FOLD_EVERY or total % FOLD_EVERY != 0:
        return None

    summary, upto = store.summary_state(db, user_id=user_id, conversation_id=conversation_id)

    # Fold from the last message the summary covers, not from the start, so the
    # same turns are never summarised twice.
    boundary = upto
    messages = store.list_messages(db, user_id=user_id, conversation_id=conversation_id)
    tail: list[ChatMessage] = []
    started = boundary is None
    for message in messages:
        if not started:
            if message.id == boundary:
                started = True
            continue
        tail.append(message)
    if not tail:
        return None

    transcript = "\n".join(
        f"{'Person' if m.role == 'user' else 'Assistant'}: {m.text}" for m in tail
    )
    system = FOLD_SYSTEM if summary else SUMMARY_SYSTEM
    prompt = (
        f"Existing summary:\n{summary}\n\nNew turns:\n{transcript}"
        if summary
        else f"Conversation so far:\n{transcript}"
    )

    try:
        payload, _, _ = client.json_message(system, prompt, max_tokens=1200)
        text = _read_summary(payload)
    except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning(
            "Conversation summary could not be refreshed using provider %s",
            settings.llm_provider,
            exc_info=True,
        )
        return summary

    if not text:
        return summary

    text = text[:MAX_SUMMARY_CHARS]
    store.set_summary(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        summary_text=text,
        upto_message_id=tail[-1].id,
    )
    return text


def _read_summary(payload: Any) -> str | None:
    """Pull the summary out of a provider response, or give up quietly."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("summary")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def record_working_set(
    db: Session, *, user_id: Any, conversation_id: Any, email_ids: list[str]
) -> list[str]:
    """Persist the emails a turn actually cited, so the next turn can re-read them."""
    return store.set_working_set(
        db, user_id=user_id, conversation_id=conversation_id, email_ids=email_ids
    )


def now() -> datetime:
    return datetime.now(UTC)