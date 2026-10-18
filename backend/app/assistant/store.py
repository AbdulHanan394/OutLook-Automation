"""Storage and lifecycle for Ask conversations.

Every read and write goes through this module so the per-user rules live in one
place: an explicit ``user_id`` predicate on every query *and* the forced
row-level-security policy underneath it, matching the rest of the app.

No email content is ever written here. A citation stores an email id and the
quoted passage; the message body is re-read from ``emails`` when it is needed,
so an email deleted by the user stops resolving instead of lingering in a chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import (
    MESSAGE_SEARCH_VECTOR,
    ChatMessage,
    Conversation,
    ConversationContext,
    set_user_scope,
)

MAX_TITLE_CHARS = 120
# A title is a label, not a summary; anything longer is truncated rather than
# rejected so a bad model response cannot fail an otherwise good turn.
MAX_MESSAGES_PER_PAGE = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _scope(db: Session, user_id: UUID) -> None:
    set_user_scope(db, user_id)


# --------------------------------------------------------------------- titles


def default_title(question: str) -> str:
    """A readable fallback title derived from the question itself.

    Used before the model has produced one, and whenever title generation is
    unavailable. Truncated on a word boundary so it never ends mid-word.
    """
    cleaned = " ".join(question.split())
    if len(cleaned) <= MAX_TITLE_CHARS:
        return cleaned or "New chat"
    cut = cleaned[:MAX_TITLE_CHARS]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip() + "…"


# ---------------------------------------------------------------- lifecycle


def create_conversation(
    db: Session, *, user_id: UUID, mailbox_id: UUID | None = None, title: str | None = None
) -> Conversation:
    _scope(db, user_id)
    conversation = Conversation(
        user_id=user_id,
        mailbox_connection_id=mailbox_id,
        title=(title or "New chat")[:MAX_TITLE_CHARS],
    )
    db.add(conversation)
    db.flush()
    db.add(
        ConversationContext(
            conversation_id=conversation.id, user_id=user_id, working_set_json=[]
        )
    )
    db.commit()
    return conversation


def get_conversation(db: Session, *, user_id: UUID, conversation_id: UUID) -> Conversation | None:
    _scope(db, user_id)
    return db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )


def list_conversations(
    db: Session,
    *,
    user_id: UUID,
    include_archived: bool = False,
    limit: int = 200,
) -> list[Conversation]:
    _scope(db, user_id)
    statement: Select[Any] = select(Conversation).where(Conversation.user_id == user_id)
    if not include_archived:
        statement = statement.where(Conversation.archived.is_(False))
    statement = statement.order_by(
        Conversation.pinned.desc(), Conversation.updated_at.desc()
    ).limit(limit)
    return list(db.scalars(statement))


def touch(db: Session, conversation: Conversation) -> None:
    """Bump ``updated_at`` so the sidebar reorders.

    Done in SQL rather than on the ORM object: the row is usually loaded in a
    different session, and an ORM assignment would not be flushed.
    """
    db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id, Conversation.user_id == conversation.user_id)
        .values(updated_at=_now())
    )


def rename_conversation(db: Session, *, user_id: UUID, conversation_id: UUID, title: str) -> bool:
    _scope(db, user_id)
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .values(title=title[:MAX_TITLE_CHARS], updated_at=_now())
        ),
    )
    db.commit()
    return bool(result.rowcount)


def set_pinned(db: Session, *, user_id: UUID, conversation_id: UUID, pinned: bool) -> bool:
    _scope(db, user_id)
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .values(pinned=pinned, updated_at=_now())
        ),
    )
    db.commit()
    return bool(result.rowcount)


def set_archived(db: Session, *, user_id: UUID, conversation_id: UUID, archived: bool) -> bool:
    _scope(db, user_id)
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .values(archived=archived, updated_at=_now())
        ),
    )
    db.commit()
    return bool(result.rowcount)


def delete_conversation(db: Session, *, user_id: UUID, conversation_id: UUID) -> bool:
    """Remove a chat and everything hanging off it.

    Messages and context cascade from the conversation row; the explicit deletes
    keep that guarantee in the database rather than relying on the ORM knowing
    every child, and they run inside the same transaction as the ownership check
    so a conversation belonging to someone else is never touched.
    """
    _scope(db, user_id)
    owned = db.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if owned is None:
        return False
    db.execute(
        delete(ConversationContext).where(
            ConversationContext.conversation_id == conversation_id,
            ConversationContext.user_id == user_id,
        )
    )
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id, ChatMessage.user_id == user_id
        )
    )
    db.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    db.commit()
    return True


def purge_expired(db: Session, *, user_id: UUID) -> int:
    """Drop chats older than the retention window. ``0`` keeps them until deleted."""
    _scope(db, user_id)
    days = get_settings().chat_retention_days
    if days <= 0:
        return 0
    cutoff = _now() - timedelta(days=days)
    stale = list(
        db.scalars(
            select(Conversation.id).where(
                Conversation.user_id == user_id,
                Conversation.archived.is_(False),
                Conversation.pinned.is_(False),
                Conversation.updated_at < cutoff,
            )
        )
    )
    for conversation_id in stale:
        delete_conversation(db, user_id=user_id, conversation_id=conversation_id)
    return len(stale)


def delete_all_conversations(db: Session, *, user_id: UUID) -> int:
    _scope(db, user_id)
    ids = list(db.scalars(select(Conversation.id).where(Conversation.user_id == user_id)))
    for conversation_id in ids:
        delete_conversation(db, user_id=user_id, conversation_id=conversation_id)
    return len(ids)


def delete_conversations_for_mailbox(
    db: Session, *, user_id: UUID, mailbox_id: UUID
) -> int:
    """Drop every chat derived from one mailbox, for one user.

    Called when the mailbox is disconnected or its data deleted. Chat content is
    derived from private email and must not outlive it.

    Deliberately does not commit: the worker runs this inside the job
    transaction, and committing here would mark the job's other work as durable
    before the job itself succeeded.
    """
    _scope(db, user_id)
    ids = list(
        db.scalars(
            select(Conversation.id).where(
                Conversation.mailbox_connection_id == mailbox_id,
                Conversation.user_id == user_id,
            )
        )
    )
    if not ids:
        return 0
    db.execute(
        delete(ConversationContext).where(
            ConversationContext.conversation_id.in_(ids),
            ConversationContext.user_id == user_id,
        )
    )
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.conversation_id.in_(ids), ChatMessage.user_id == user_id
        )
    )
    result = cast(
        "CursorResult[Any]",
        db.execute(
            delete(Conversation).where(
                Conversation.id.in_(ids), Conversation.user_id == user_id
            )
        ),
    )
    return int(result.rowcount or 0)


# ------------------------------------------------------------------- messages


def add_message(
    db: Session,
    *,
    user_id: UUID,
    conversation_id: UUID,
    role: str,
    text_value: str,
    content_json: dict[str, Any] | None = None,
    citations_json: list[Any] | None = None,
    tool_trace_json: list[Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> ChatMessage:
    _scope(db, user_id)
    message = ChatMessage(
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        text=text_value,
        content_json=content_json or {},
        citations_json=citations_json or [],
        tool_trace_json=tool_trace_json or [],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(message)
    db.flush()
    touch(db, _conversation_for(db, user_id, conversation_id))
    db.commit()
    return message


def _conversation_for(db: Session, user_id: UUID, conversation_id: UUID) -> Conversation:
    found = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if found is None:  # pragma: no cover - callers check ownership first
        raise LookupError("conversation not found")
    return found


def list_messages(
    db: Session, *, user_id: UUID, conversation_id: UUID, limit: int = MAX_MESSAGES_PER_PAGE
) -> list[ChatMessage]:
    _scope(db, user_id)
    return list(
        db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.user_id == user_id,
            )
            .order_by(ChatMessage.created_at, ChatMessage.id)
            .limit(limit)
        )
    )


def delete_messages_from(
    db: Session, *, user_id: UUID, conversation_id: UUID, message_id: UUID
) -> int:
    """Remove a message and everything after it, used when a turn is edited.

    The turn is replaced rather than amended so the conversation stays a linear
    transcript; regenerating an earlier answer would otherwise leave the old
    reply underneath the new one.
    """
    _scope(db, user_id)
    anchor = db.scalar(
        select(ChatMessage.created_at).where(
            ChatMessage.id == message_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.user_id == user_id,
        )
    )
    if anchor is None:
        return 0
    deleted = cast(
        "CursorResult[Any]",
        db.execute(
            delete(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.user_id == user_id,
                ChatMessage.created_at >= anchor,
            )
        ),
    )
    db.commit()
    return int(deleted.rowcount or 0)


def replace_assistant_message(
    db: Session,
    *,
    user_id: UUID,
    message_id: UUID,
    text_value: str,
    content_json: dict[str, Any] | None = None,
    citations_json: list[Any] | None = None,
    tool_trace_json: list[Any] | None = None,
) -> bool:
    _scope(db, user_id)
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(ChatMessage)
            .where(ChatMessage.id == message_id, ChatMessage.user_id == user_id, ChatMessage.role == "assistant")
            .values(
                text=text_value,
                content_json=content_json or {},
                citations_json=citations_json or [],
                tool_trace_json=tool_trace_json or [],
            )
        ),
    )
    db.commit()
    return bool(result.rowcount)


# ------------------------------------------------------------------- context


def get_context(db: Session, *, user_id: UUID, conversation_id: UUID) -> ConversationContext:
    _scope(db, user_id)
    context = db.get(ConversationContext, conversation_id)
    if context is None or context.user_id != user_id:
        context = ConversationContext(
            conversation_id=conversation_id, user_id=user_id, working_set_json=[]
        )
        db.add(context)
        db.commit()
    return context


def working_set(db: Session, *, user_id: UUID, conversation_id: UUID) -> list[str]:
    return [str(item) for item in get_context(db, user_id=user_id, conversation_id=conversation_id).working_set_json]


def set_working_set(
    db: Session, *, user_id: UUID, conversation_id: UUID, email_ids: list[str]
) -> list[str]:
    """Replace the working set, de-duplicated and bounded.

    Ids only. The cap keeps a runaway @ mention chain from turning into an
    unbounded prompt payload.
    """
    _scope(db, user_id)
    context = get_context(db, user_id=user_id, conversation_id=conversation_id)
    seen: list[str] = []
    for email_id in email_ids:
        value = str(email_id)
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= 50:
            break
    context.working_set_json = seen
    context.updated_at = _now()
    db.commit()
    return seen


def summary_state(
    db: Session, *, user_id: UUID, conversation_id: UUID
) -> tuple[str | None, UUID | None]:
    context = get_context(db, user_id=user_id, conversation_id=conversation_id)
    return context.summary_text, context.summary_upto_message_id


def set_summary(
    db: Session,
    *,
    user_id: UUID,
    conversation_id: UUID,
    summary_text: str,
    upto_message_id: UUID | None,
) -> None:
    _scope(db, user_id)
    context = get_context(db, user_id=user_id, conversation_id=conversation_id)
    context.summary_text = summary_text
    context.summary_upto_message_id = upto_message_id
    context.updated_at = _now()
    db.commit()


# -------------------------------------------------------------------- search


def search_conversations(
    db: Session, *, user_id: UUID, query: str, limit: int = 30
) -> list[Conversation]:
    """Full-text search over titles and message bodies, scoped to one user.

    A chat is found by a word from any turn, not only from its title, which is
    what makes "that thread about the Supabase project" work as a query. The
    text is matched with ``plainto_tsquery`` so punctuation in the query is not
    an injection vector.
    """
    _scope(db, user_id)
    trimmed = query.strip()
    if not trimmed:
        return []
    matched = select(ChatMessage.conversation_id).where(
        ChatMessage.user_id == user_id,
        MESSAGE_SEARCH_VECTOR.op("@@")(func.plainto_tsquery("english", trimmed)),
    )
    title_match = or_(
        func.to_tsvector("english", Conversation.title).op("@@")(
            func.plainto_tsquery("english", trimmed)
        ),
        Conversation.title.ilike(f"%{trimmed}%"),
    )
    return list(
        db.scalars(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.archived.is_(False),
                or_(Conversation.id.in_(matched), title_match),
            )
            .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
            .limit(limit)
        )
    )