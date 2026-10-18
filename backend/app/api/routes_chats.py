"""Conversation endpoints for the Ask page.

Read-only with respect to the mailbox: nothing here writes, moves or sends an
email. Every handler scopes to the signed-in user, and a conversation that is
someone else's is reported as 404 rather than 403 so the API does not confirm
that an id exists.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any
from uuid import UUID

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import context as conversation_context
from app.assistant import memory, store, suggestions
from app.assistant.tool_loop import ToolsUnavailable
from app.assistant.turn import answer_question, answer_with_tools
from app.config import Settings, get_settings
from app.db import Email, MailboxConnection, SessionRecord, get_db, set_user_scope
from app.llm.client import JsonMessageClient
from app.llm.factory import create_json_client
from app.security.csrf import require_csrf
from app.security.rate_limit import enforce_user_rate_limit
from app.security.sessions import require_user

router = APIRouter(prefix="/api/chats", tags=["chats"])
logger = logging.getLogger(__name__)

# Same headers as /api/mail/ask: nginx buffers event-streams by default, which
# would collapse the whole turn into one delivery.
SSE_HEADERS = {
    "Cache-Control": "no-store, private",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def text_chunks(text: str, size: int = 240) -> list[str]:
    """Split verified text for delivery.

    The answer is complete and quote-checked before the first chunk leaves, so
    this controls how it arrives and never what it says.
    """
    words = text.split(" ")
    out: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        if len(" ".join(current)) >= size:
            out.append(" ".join(current) + " ")
            current = []
    if current:
        out.append(" ".join(current))
    return out


# ------------------------------------------------------------------ payloads


class CreateChatRequest(BaseModel):
    mailbox_id: UUID | None = None
    title: str | None = Field(default=None, max_length=120)


class UpdateChatRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    pinned: bool | None = None
    archived: bool | None = None


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    temporary: bool = False


class TemporaryMessageRequest(BaseModel):
    mailbox_id: UUID
    message: str = Field(min_length=1, max_length=4000)


def _chat_json(conversation: Any, message_count: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(conversation.id),
        "mailbox_id": str(conversation.mailbox_connection_id)
        if conversation.mailbox_connection_id
        else None,
        "title": conversation.title,
        "pinned": conversation.pinned,
        "archived": conversation.archived,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }
    if message_count is not None:
        payload["message_count"] = message_count
    return payload


def _message_json(message: Any) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "role": message.role,
        "text": message.text,
        "content": message.content_json or {},
        "citations": message.citations_json or [],
        "steps": message.tool_trace_json or [],
        "created_at": message.created_at.isoformat(),
    }


def _require_owned(db: Session, user_id: UUID, conversation_id: UUID) -> Any:
    conversation = store.get_conversation(db, user_id=user_id, conversation_id=conversation_id)
    if conversation is None:
        # 404 rather than 403: a 403 would confirm the id exists.
        raise HTTPException(404, "Conversation not found")
    return conversation


# ------------------------------------------------------------------ endpoints


@router.get("")
def list_chats(
    user_id: Annotated[UUID, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
    include_archived: bool = Query(default=False),
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    conversations = store.list_conversations(
        db, user_id=user_id, include_archived=include_archived
    )
    return {"conversations": [_chat_json(item) for item in conversations]}


@router.post("", status_code=201)
def create_chat(
    request: CreateChatRequest,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    conversation = store.create_conversation(
        db, user_id=user_id, mailbox_id=request.mailbox_id, title=request.title
    )
    return _chat_json(conversation, message_count=0)


@router.get("/search")
def search_chats(
    user_id: Annotated[UUID, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(default="", max_length=200),
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    found = store.search_conversations(db, user_id=user_id, query=q)
    return {"conversations": [_chat_json(item) for item in found]}


# ------------------------------------------------------------------- memory


class MemoryNoteRequest(BaseModel):
    note_text: str = Field(min_length=3, max_length=400)
    mailbox_id: UUID | None = None


@router.get("/memory")
def list_memory_notes(
    user_id: Annotated[UUID, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Every preference the assistant holds, so the reader can audit it.

    A memory the reader cannot see is not consent, so this lists exactly what is
    stored and each entry can be removed by id.
    """
    set_user_scope(db, user_id)
    notes = memory.list_notes(db, user_id=user_id)
    return {
        "notes": [
            {
                "id": str(note.id),
                "note_text": note.note_text,
                "mailbox_id": str(note.mailbox_connection_id)
                if note.mailbox_connection_id
                else None,
                "use_count": note.use_count,
                "created_at": note.created_at.isoformat(),
            }
            for note in notes
        ]
    }


@router.post("/memory", status_code=201)
def create_memory_note(
    request: MemoryNoteRequest,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "memory", settings.rate_limit_chat_per_min)
    if request.mailbox_id is not None:
        owned = db.scalar(
            select(MailboxConnection.id).where(
                MailboxConnection.id == request.mailbox_id,
                MailboxConnection.user_id == user_id,
            )
        )
        if owned is None:
            raise HTTPException(404, "Mailbox not found")
    try:
        note = memory.add_note(
            db, user_id=user_id, note_text=request.note_text, mailbox_id=request.mailbox_id
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return {"id": str(note.id), "note_text": note.note_text, "use_count": 0}


@router.delete("/memory/{note_id}", status_code=204)
def delete_memory_note(
    note_id: UUID,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    set_user_scope(db, user_id)
    if not memory.forget(db, user_id=user_id, note_id=note_id):
        raise HTTPException(404, "Note not found")


@router.post("/memory/delete-all", status_code=200)
def delete_all_memory_notes(
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, int]:
    set_user_scope(db, user_id)
    return {"deleted": memory.forget_all(db, user_id=user_id)}

@router.get("/{conversation_id}")
def get_chat(
    conversation_id: UUID,
    user_id: Annotated[UUID, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    conversation = _require_owned(db, user_id, conversation_id)
    messages = store.list_messages(db, user_id=user_id, conversation_id=conversation_id)
    payload = _chat_json(conversation, message_count=len(messages))
    payload["messages"] = [_message_json(item) for item in messages]
    payload["working_set"] = store.working_set(db, user_id=user_id, conversation_id=conversation_id)
    return payload


@router.patch("/{conversation_id}")
def update_chat(
    conversation_id: UUID,
    request: UpdateChatRequest,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    _require_owned(db, user_id, conversation_id)
    if request.title is not None:
        store.rename_conversation(
            db, user_id=user_id, conversation_id=conversation_id, title=request.title
        )
    if request.pinned is not None:
        store.set_pinned(
            db, user_id=user_id, conversation_id=conversation_id, pinned=request.pinned
        )
    if request.archived is not None:
        store.set_archived(
            db, user_id=user_id, conversation_id=conversation_id, archived=request.archived
        )
    conversation = _require_owned(db, user_id, conversation_id)
    return _chat_json(conversation)


@router.delete("/{conversation_id}", status_code=204)
def delete_chat(
    conversation_id: UUID,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    set_user_scope(db, user_id)
    if not store.delete_conversation(
        db, user_id=user_id, conversation_id=conversation_id
    ):
        raise HTTPException(404, "Conversation not found")


def _resolve_mailbox(db: Session, user_id: UUID, mailbox_id: UUID) -> MailboxConnection:
    """The user's own, connected, consented-to mailbox, or a clear refusal."""
    mailbox = db.scalar(
        select(MailboxConnection).where(
            MailboxConnection.id == mailbox_id,
            MailboxConnection.user_id == user_id,
        )
    )
    if mailbox is None:
        raise HTTPException(404, "Mailbox not found")
    if not mailbox.external_ai_consent_at:
        raise HTTPException(409, "AI processing consent is required")
    return mailbox


@router.post("/delete-all", status_code=200)
def delete_all_chats(
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, int]:
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "chats", settings.rate_limit_chat_per_min)
    return {"deleted": store.delete_all_conversations(db, user_id=user_id)}



# -------------------------------------------------------------- suggestions


@router.get("/{conversation_id}/suggestions")
def chat_suggestions(
    conversation_id: UUID,
    user_id: Annotated[UUID, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Follow-ups this reader might ask next, or an empty list.

    Offered, never applied: nothing here writes into the composer. A provider
    failure returns ``[]`` rather than an error, because a chat without
    suggestions is a perfectly good chat.
    """
    set_user_scope(db, user_id)
    _require_owned(db, user_id, conversation_id)
    found = suggestions.suggest(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        settings=settings,
        client=_interpret_client(settings),
    )
    return {"suggestions": found}


# ------------------------------------------------------------- sending a turn


def _citation_payload(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Citations in the shape stored on the message and rendered by the reader.

    Each entry is an email id plus the claim text it supports. No email body is
    stored: the reader re-reads the message from ``/api/mail/messages/{id}``.
    """
    return [
        {
            "email_id": closest["email_id"],
            "subject": closest["subject"],
            "sender": closest["sender"],
            "received_at": closest["received_at"],
            "outlook_url": closest.get("outlook_url"),
        }
        for closest in result.get("closest_emails", [])
    ]


def _failure_stream(status_code: int, detail: str) -> StreamingResponse:
    """A refusal or a provider outage, expressed as one stream event.

    429 is the spend cap and is the reader's to act on, so it arrives as
    ``error``. Anything else is the provider's problem and degrades to
    ``unavailable``: the transcript is left untouched either way.
    """
    event = "error" if status_code == 429 else "unavailable"
    return StreamingResponse(
        iter([sse(event, {"message": detail, "fallback": True})]),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _answer_stream(result: dict[str, Any], answer_message_id: str | None) -> StreamingResponse:
    """Deliver an answer that is already complete and quote-verified.

    Chunking controls how the text arrives, never what it says: the first delta
    is emitted only after verification, so nothing unverified is ever shown
    arriving.
    """
    answer = result.get("answer") or result.get("explanation") or ""
    citations = _citation_payload(result)

    def stream() -> Iterator[str]:
        if answer_message_id is not None:
            yield sse("message_saved", {"message_id": answer_message_id})
        if answer:
            for chunk in text_chunks(answer):
                yield sse("answer_delta", {"text": chunk})
            yield sse("answer", {"text": answer})
        else:
            yield sse(
                "answer",
                {
                    "text": "",
                    "explanation": result.get("explanation", ""),
                    "sufficient_evidence": False,
                },
            )
        yield sse("citations", {"citations": citations})
        yield sse("done", {"used_model": bool(answer), "path": "grounded-answer"})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


def _notice_stream(message: str, payload: dict[str, Any]) -> StreamingResponse:
    """A turn that needed no answer at all, delivered in the same stream shape.

    Used for "remember this" and "forget that", so the client has one code path
    rather than two.
    """
    return StreamingResponse(
        iter(
            [
                sse("notice", {"message": message, **payload}),
                sse("done", {"used_model": False, "path": "memory"}),
            ]
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _interpret_client(settings: Settings) -> JsonMessageClient | None:
    """The cheaper model, for bookkeeping that is not an answer.

    Returns ``None`` rather than raising when the provider is unconfigured or
    the cap is spent: summarising is an optimisation, and a chat that cannot be
    summarised must still answer.
    """
    try:
        return create_json_client(settings, "interpret")
    except (HTTPException, anthropic.APIError, httpx.HTTPError):
        logger.warning(
            "No interpret client available; the conversation summary will not be "
            "refreshed this turn (provider %s)",
            settings.llm_provider,
        )
        return None


def _run_turn(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    message: str,
    settings: Settings,
    conversation_id: UUID | None = None,
) -> tuple[
    StreamingResponse | None,
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, int],
]:
    """Answer one turn, or report why it could not be answered.

    Returns either a ready-to-send failure stream or the raw result for the
    caller to persist and stream.

    When ``conversation_id`` is given the turn is grounded in the rest of the
    chat: earlier turns become prompt context and the messages already under
    discussion are pinned into the excerpt list, so "what about the second one?"
    resolves. With no conversation this is the plain single-turn path.
    """
    conversation = ""
    pinned: list[Email] = []
    if conversation_id is not None:
        view = conversation_context.load(
            db, user_id=user_id, conversation_id=conversation_id
        )
        conversation = view.as_prompt_block()
        pinned = conversation_context.resolve_working_set(
            db, user_id=user_id, conversation_id=conversation_id, ids=view.working_set
        )
    # Stated preferences are injected for every turn, saved chat or not.
    preferences = memory.as_prompt_block(db, user_id=user_id)
    if preferences:
        conversation = f"{preferences}\n\n{conversation}" if conversation else preferences
    try:
        client = create_json_client(settings, "answer")
        trace: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        if settings.chat_use_tools:
            # The assistant picks what to read. If the provider cannot drive
            # tools, or the loop finds nothing, fall back to single-turn
            # retrieval: degraded but grounded, never failed.
            try:
                result, trace, usage = answer_with_tools(
                    db,
                    user_id=user_id,
                    mailbox_id=mailbox_id,
                    question=message,
                    settings=settings,
                    client=client,
                    conversation=conversation,
                )
            except ToolsUnavailable as reason:
                logger.info(
                    "Falling back to single-turn retrieval: %s", reason
                )
                result = None
        else:
            result = None

        if result is None:
            result = answer_question(
                db,
                user_id=user_id,
                mailbox_id=mailbox_id,
                question=message,
                settings=settings,
                client=client,
                conversation=conversation,
                pinned_emails=pinned,
            )
    except HTTPException as error:
        return _failure_stream(error.status_code, str(error.detail)), None, [], {}
    except (anthropic.APIError, httpx.HTTPError):
        logger.warning(
            "Chat turn could not reach provider %s", settings.llm_provider, exc_info=True
        )
        return (
            _failure_stream(
                502,
                "The AI provider could not be reached. Your chat is unchanged.",
            ),
            None,
            [],
            {},
        )
    return None, result, trace, usage


# Declared before the parameterised route below: FastAPI matches in registration
# order, so "/{conversation_id}/messages" would otherwise capture "temporary"
# and reject it as a malformed UUID.
@router.post("/temporary/messages")
def send_temporary_message(
    request: TemporaryMessageRequest,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Answer a turn that must leave nothing behind.

    A temporary chat is still grounded in the reader's own mail, but no
    conversation, message or context row is created, nothing is written to
    memory, and the transcript exists only in the browser tab. Closing the chat
    discards it.
    """
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "chat_turns", settings.rate_limit_chat_per_min)
    mailbox = _resolve_mailbox(db, user_id, request.mailbox_id)

    message = request.message.strip()
    if not message:
        raise HTTPException(422, "Message must not be blank")

    failure, result, _trace, _usage = _run_turn(
        db, user_id=user_id, mailbox_id=mailbox.id, message=message, settings=settings
    )
    if failure is not None:
        return failure
    assert result is not None

    # No id is emitted: there is nothing to reference, because nothing was saved.
    return _answer_stream(result, None)


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: UUID,
    request: SendMessageRequest,
    user_id: Annotated[UUID, Depends(require_user)],
    csrf_session: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    set_user_scope(db, user_id)
    conversation = _require_owned(db, user_id, conversation_id)
    enforce_user_rate_limit(user_id, "chat_turns", settings.rate_limit_chat_per_min)

    if not conversation.mailbox_connection_id:
        raise HTTPException(409, "This chat is not linked to a mailbox.")
    mailbox = _resolve_mailbox(db, user_id, conversation.mailbox_connection_id)

    message = request.message.strip()
    if not message:
        raise HTTPException(422, "Message must not be blank")

    # "Always give me the dates" is a standing instruction, not a question about
    # mail. It is handled before retrieval so it is never answered as one.
    intent = memory.classify(message)
    if intent.action == "remember" and intent.note:
        note = memory.add_note(
            db,
            user_id=user_id,
            note_text=intent.note,
            mailbox_id=conversation.mailbox_connection_id,
        )
        return _notice_stream(
            "Saved. I’ll keep that in mind from now on.",  # noqa: RUF001 - user-facing
            {"note_id": str(note.id), "note_text": note.note_text},
        )
    if intent.action == "forget":
        removed = memory.forget(db, user_id=user_id, query=intent.note or message)
        return _notice_stream(
            (
                f"Forgotten — removed {removed} of your saved preferences."
                if removed
                else "I didn’t have that saved, so there was nothing to remove."  # noqa: RUF001
            ),
            {"removed": removed},
        )
    failure, result, trace, usage = _run_turn(
        db,
        user_id=user_id,
        mailbox_id=mailbox.id,
        message=message,
        settings=settings,
        conversation_id=conversation_id,
    )
    if failure is not None:
        return failure
    assert result is not None

    answer = result.get("answer") or result.get("explanation") or ""
    citations = _citation_payload(result)

    # Persist the turn before streaming it: if the connection drops mid-answer
    # the transcript is still complete, because the text was verified first.
    store.add_message(
        db, user_id=user_id, conversation_id=conversation_id, role="user", text_value=message
    )
    assistant_message = store.add_message(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        role="assistant",
        text_value=answer,
        citations_json=citations,
        tool_trace_json=trace,
        content_json={
            "sufficient_evidence": result.get("sufficient_evidence", False),
            "usage": usage,
        },
    )

    # Remember which messages this turn talked about, so the next turn can pin
    # them and a follow-up like "what about the second one?" resolves to
    # something the model can actually see.
    conversation_context.record_working_set(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        email_ids=[citation["email_id"] for citation in citations],
    )
    # Best effort: a chat that cannot be summarised keeps its full transcript.
    conversation_context.maybe_fold(
        db,
        user_id=user_id,
        conversation_id=conversation_id,
        client=_interpret_client(settings),
        settings=settings,
    )

    return _answer_stream(result, str(assistant_message.id))

    return _answer_stream(result, str(assistant_message.id))