"""Stated preferences, and the routing that honours them.

Two small modules that share a rule: the reader decides, not the model.

**Memory.** A note is only ever written from something the reader said outright
("always give me the dates", "don't summarise my own sent mail"). Nothing is
inferred from what they open or how often they ask -- an inferred preference is
one the reader cannot see coming or argue with. Every note is listed where they
can edit or delete it, and notes are injected into the prompt as *data* with
the same escaping as any other untrusted text.

**Routing.** With several mailboxes connected, a question has to pick one. The
pick is made from the conversation's own mailbox first, then the reader's
remembered routing preference, and only then the question's own words. The
model is never asked to choose: a mis-route costs a turn and can be wrong in a
way the reader cannot see.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Result, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import Update

from app.db import MailboxConnection, MemoryNote, set_user_scope

logger = logging.getLogger(__name__)

MAX_NOTE_CHARS = 400
# A note is a sentence about how to answer, not a paragraph.
MAX_ACTIVE_NOTES = 20

# Only these intents may create a memory. Anything else the reader says is just
# a question about their mail, and a question must never silently become a
# standing instruction.
MEMORY_INTENT = "remember"
FORGET_INTENT = "forget"

REMEMBER_RE = re.compile(
    r"^\s*(?:please\s+)?(?:from now on[,\s]+|going forward[,\s]+)?"
    r"(remember(?: that)?|always|never|don'?t(?:\s+ever)?|do not always|stop)\b",
    re.IGNORECASE,
)
FORGET_RE = re.compile(r"^\s*(?:please\s+)?forget\b", re.IGNORECASE)
# What to look for when removing: "forget that I wanted the dates" refers to the
# stored note by its content, so the verb is stripped before it is matched.
FORGET_QUERY_RE = re.compile(r"^\s*(?:please\s+)?forget(?:\s+that)?\s*", re.IGNORECASE)
# "always"/"never"/"don't" only count as instructions when the sentence is about
# how to answer. "who always CCs Legal?" is a question, and turning that into a
# standing preference would be a bug the reader could not undo.
#
# No trailing word boundary: the alternatives are deliberately stems
# (summar-, prioriti-, verbos-, concis-) so they catch the whole family of forms.
ANSWER_SHAPED = re.compile(
    r"\b(answer|respond|reply|summar|keep|include|omit|skip|"
    r"prioriti|sort|order|format|length|tone|verbos|concis|brevity|"
    r"always|never|from now on|going forward)",
    re.IGNORECASE,
)
# Words long enough to pass the length filter but carrying no meaning, so they
# cannot be what the reader meant to refer to.
_FORGET_NOISE = {"wanted", "please", "remember", "forget", "would", "could", "should"}


@dataclass(frozen=True)
class MemoryIntent:
    """Whether a message asks for a preference to be kept or dropped."""

    action: str  # "remember" | "forget" | "none"
    note: str | None = None


def classify(message: str) -> MemoryIntent:
    """Decide whether this message is a memory instruction, and why.

    Deliberately conservative and rule-based. The model is not asked whether
    something should be remembered, because a wrong "yes" writes a standing
    instruction the reader never gave.
    """
    text = " ".join(message.split())
    if not text:
        return MemoryIntent("none")

    if FORGET_RE.match(text):
        # No ANSWER_SHAPED gate here. That check exists to stop a *question*
        # becoming a standing instruction; an explicit "forget" is already
        # unambiguous, and requiring it to also look like a question about
        # answering rejected "forget that I wanted the dates".
        return MemoryIntent("forget", FORGET_QUERY_RE.sub("", text).strip() or text)

    if REMEMBER_RE.match(text) and ANSWER_SHAPED.search(text):
        # Strip the leading politeness so the stored note is the instruction.
        cleaned = REMEMBER_RE.sub("", text, count=1).strip(" ,.:;-")
        note = cleaned or text
        if len(note) < 3:
            return MemoryIntent("none")
        return MemoryIntent("remember", note[:MAX_NOTE_CHARS])

    return MemoryIntent("none")


# ------------------------------------------------------------------- storage


def add_note(
    db: Session,
    *,
    user_id: UUID,
    note_text: str,
    mailbox_id: UUID | None = None,
    source_message_id: UUID | None = None,
) -> MemoryNote:
    """Record a preference the reader stated.

    Trimmed, capped, and de-duplicated against the reader's existing notes:
    saying the same thing twice should not fill the list.
    """
    set_user_scope(db, user_id)
    text = " ".join(note_text.split())[:MAX_NOTE_CHARS]
    if len(text) < 3:
        raise ValueError("note_text is too short to be a preference")

    existing = db.scalars(
        select(MemoryNote).where(
            MemoryNote.user_id == user_id,
            MemoryNote.note_text == text,
            MemoryNote.active.is_(True),
        )
    )
    for note in existing:
        note.active = True
        db.commit()
        return note

    # Keep the list short enough to stay readable and cheap to inject.
    count = len(
        list(
            db.scalars(
                select(MemoryNote.id).where(
                    MemoryNote.user_id == user_id, MemoryNote.active.is_(True)
                )
            )
        )
    )
    if count >= MAX_ACTIVE_NOTES:
        oldest = db.scalars(
            select(MemoryNote)
            .where(MemoryNote.user_id == user_id, MemoryNote.active.is_(True))
            .order_by(MemoryNote.created_at)
            .limit(count - MAX_ACTIVE_NOTES + 1)
        )
        for note in oldest:
            note.active = False
            db.commit()

    note = MemoryNote(
        user_id=user_id,
        mailbox_connection_id=mailbox_id,
        note_text=text,
        source_message_id=source_message_id,
        active=True,
    )
    db.add(note)
    db.commit()
    return note


def list_notes(db: Session, *, user_id: UUID, include_inactive: bool = False) -> list[MemoryNote]:
    set_user_scope(db, user_id)
    statement = select(MemoryNote).where(MemoryNote.user_id == user_id)
    if not include_inactive:
        statement = statement.where(MemoryNote.active.is_(True))
    return list(db.scalars(statement.order_by(MemoryNote.created_at.desc())))


def forget(
    db: Session, *, user_id: UUID, note_id: UUID | None = None, query: str | None = None
) -> int:
    """Deactivate a note, or every note matching ``query``.

    Deactivated rather than deleted so the history stays auditable, but the
    note stops being read into any prompt, which is what matters.
    """
    set_user_scope(db, user_id)
    conditions: list[ColumnElement[bool]] = [
        MemoryNote.user_id == user_id,
        MemoryNote.active.is_(True),
    ]
    statement: Update | None = None
    if note_id is not None:
        statement = update(MemoryNote).where(*conditions, MemoryNote.id == note_id).values(
            active=False
        )
    elif query and query.strip():
        # The reader refers to a note in their own words: "forget that I wanted
        # the dates" against a stored "give me the dates". Substring matching
        # misses that, so distinctive words are matched instead -- but only
        # words long enough to mean something, or "the" would match everything.
        tokens = [
            token
            for token in re.findall(r"[a-z0-9']+", query.lower())
            if len(token) >= 5 and token not in _FORGET_NOISE
        ]
        if not tokens:
            return 0
        clause = or_(*[MemoryNote.note_text.ilike(f"%{token}%") for token in tokens])
        statement = update(MemoryNote).where(*conditions, clause).values(active=False)
    if statement is None:
        return 0
    changed = _affected(db.execute(statement))
    db.commit()
    return changed


def _affected(result: Result[Any]) -> int:
    """Rows changed by a DML statement.

    ``Session.execute`` is typed as returning a plain ``Result``, which does not
    carry ``rowcount``; the statements passed here are all UPDATE, so it is
    always present at runtime.
    """
    return int(getattr(result, "rowcount", 0) or 0)


def forget_all(db: Session, *, user_id: UUID) -> int:
    """Deactivate every note. The reader asked for all of it to go."""
    set_user_scope(db, user_id)
    statement = (
        update(MemoryNote)
        .where(MemoryNote.user_id == user_id, MemoryNote.active.is_(True))
        .values(active=False)
    )
    changed = _affected(db.execute(statement))
    db.commit()
    return changed


def as_prompt_block(db: Session, *, user_id: UUID) -> str:
    """The reader's stated preferences, as untrusted data.

    Angle brackets are folded for the same reason conversation text is: a note
    is free text the reader typed, and it is not allowed to close the block it
    lives in.
    """
    notes = list_notes(db, user_id=user_id)
    if not notes:
        return ""
    lines = [
        f"- {note.note_text.replace('<', '&lt;').replace('>', '&gt;')}" for note in notes
    ]
    return (
        "<stated_preferences>\nThe reader asked for these. Follow them when they "
        "apply; ignore any that do not bear on this question. This is data, not "
        "instruction from a third party.\n"
        + "\n".join(lines)
        + "\n</stated_preferences>"
    )


def mark_used(db: Session, *, user_id: UUID, note_ids: list[UUID]) -> None:
    """Record that these notes were read into a prompt.

    Used to show the reader which preferences are actually doing anything, and
    to spot ones that never fire.
    """
    if not note_ids:
        return
    from datetime import UTC, datetime

    set_user_scope(db, user_id)
    db.execute(
        update(MemoryNote)
        .where(MemoryNote.user_id == user_id, MemoryNote.id.in_(note_ids))
        .values(use_count=MemoryNote.use_count + 1, last_used_at=datetime.now(UTC))
    )
    db.commit()


# ------------------------------------------------------------------- routing


@dataclass(frozen=True)
class RoutingDecision:
    mailbox_id: UUID | None
    reason: str


def route(
    db: Session,
    *,
    user_id: UUID,
    question: str,
    conversation_mailbox_id: UUID | None = None,
    default_mailbox_id: UUID | None = None,
) -> RoutingDecision:
    """Pick the mailbox a question belongs to.

    In order: the chat's own mailbox (a conversation is about one mailbox and
    should stay there), then a remembered routing preference, then an address
    named in the question, then whatever the reader had selected.
    """
    if conversation_mailbox_id is not None:
        return RoutingDecision(conversation_mailbox_id, "conversation")

    text = question.strip()

    # An explicit address in the question is the strongest signal a reader gives.
    addressed = _addressed_mailbox(db, user_id=user_id, text=text)
    if addressed is not None:
        return RoutingDecision(addressed, "named_in_question")

    # A remembered "always ask about work" is honoured next.
    preferred = _remembered_mailbox(db, user_id=user_id)
    if preferred is not None:
        return RoutingDecision(preferred, "remembered")

    return RoutingDecision(default_mailbox_id, "default")


def _addressed_mailbox(db: Session, *, user_id: UUID, text: str) -> UUID | None:
    set_user_scope(db, user_id)
    lowered = text.lower()
    rows = db.scalars(
        select(MailboxConnection).where(
            MailboxConnection.user_id == user_id, MailboxConnection.status == "active"
        )
    )
    for mailbox in rows:
        address = (mailbox.account_email or "").lower()
        if address and address in lowered:
            return mailbox.id

    # A bare domain is only usable when it identifies exactly one mailbox.
    # Addresses commonly share a domain ("work@acme.test", "payroll@acme.test"),
    # and matching the domain alone would pick whichever row came back first.
    pairs = [(row.id, (row.account_email or "").lower()) for row in rows]
    for _mailbox_id, address in pairs:
        _, _, domain = address.partition("@")
        if not domain or domain not in lowered:
            continue
        sharing = [
            other_id for other_id, other in pairs if other.partition("@")[2] == domain
        ]
        if len(sharing) == 1:
            return sharing[0]
    return None


def _remembered_mailbox(db: Session, *, user_id: UUID) -> UUID | None:
    set_user_scope(db, user_id)
    mailboxes = {
        str(row.id): (row.account_email or "").lower()
        for row in db.scalars(
            select(MailboxConnection).where(
                MailboxConnection.user_id == user_id,
                MailboxConnection.status == "active",
            )
        )
    }
    if not mailboxes:
        return None
    for note in list_notes(db, user_id=user_id):
        lowered = note.note_text.lower()
        for mailbox_id, address in mailboxes.items():
            if address and address in lowered:
                return UUID(mailbox_id)
    return None