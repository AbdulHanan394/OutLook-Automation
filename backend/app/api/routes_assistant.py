import json
import logging
from datetime import UTC
from typing import Any
from uuid import UUID

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import EMAIL_SEARCH_VECTOR, Email, MailboxConnection, SessionRecord, get_db
from app.llm.answer import ANSWER_SYSTEM, Excerpt, build_context, verify_answer
from app.llm.client import JsonMessageClient
from app.llm.commitments import reserve_claude_call
from app.llm.factory import create_json_client
from app.llm.questions import extract_search_terms, is_recent_summary_question
from app.llm.timeline import build_timeline, is_timeline_question
from app.retrieval.fts import build_match_filter, build_queries, build_rank_query, query_units
from app.security.csrf import require_csrf
from app.security.sessions import require_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
logger = logging.getLogger(__name__)

SEARCH_SYSTEM = """The user is asking a natural-language question about their own emails. Extract up to 6 concise search terms or short phrases that are likely to appear in relevant email subjects or bodies. Prefer the most distinctive words in the question and drop conversational filler entirely. Preserve names, organizations, dates, and distinctive topic words. Do not add synonyms or words that the user did not imply, do not answer the question, and do not invent details. Return only a JSON object with a string array named keywords."""

class AskRequest(BaseModel):
    mailbox_id: UUID
    question: str = Field(min_length=3, max_length=1000)


def create_answer_client(settings: Settings) -> JsonMessageClient:
    return create_json_client(settings, "answer")


@router.post("/ask")
def ask(
    request: AskRequest,
    user_id: UUID = Depends(require_user),
    csrf_session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    mailbox = db.scalar(select(MailboxConnection).where(MailboxConnection.id == request.mailbox_id, MailboxConnection.user_id == user_id))
    if not mailbox: raise HTTPException(404, "Mailbox not found")
    if not mailbox.external_ai_consent_at:
        raise HTTPException(409, "AI processing consent is required")
    timeline_question = is_timeline_question(request.question)
    recent_summary = is_recent_summary_question(request.question)
    client = create_answer_client(settings)
    if recent_summary:
        search_terms = ""
    else:
        try:
            if settings.llm_provider == "anthropic" and not reserve_claude_call(db, user_id, settings):
                raise HTTPException(429, "The daily Claude request limit has been reached. Try again tomorrow.")
            search_payload, _, _ = client.json_message(SEARCH_SYSTEM, request.question, max_tokens=1024)
            search_terms = extract_search_terms(search_payload, request.question)
        except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.exception("Email question understanding failed using provider %s", settings.llm_provider)
            raise HTTPException(502, "The configured AI provider could not understand your question.") from None

    # Retrieval runs in two modes. Questions that ask for a broad "what's new"
    # recap bypass keywords entirely; everything else is matched full-text.
    unit_queries = [] if recent_summary else build_queries(query_units(request.question, search_terms))
    match_filter = build_match_filter(EMAIL_SEARCH_VECTOR, unit_queries)
    rank_query = build_rank_query(unit_queries)

    if match_filter is None:
        candidates = db.scalars(
            select(Email)
            .where(
                Email.user_id == user_id,
                Email.mailbox_connection_id == request.mailbox_id,
                ~Email.is_deleted,
            )
            .order_by(Email.received_at.desc())
            .limit(settings.rag_max_excerpts * 3)
        )
        # Keep one message per conversation so a busy thread cannot crowd out
        # everything else.
        selected = []
        seen_conversations = set()
        for email in candidates:
            conversation = email.thread_key or str(email.id)
            if conversation in seen_conversations:
                continue
            seen_conversations.add(conversation)
            selected.append(email)
            if len(selected) == settings.rag_max_excerpts:
                break
    else:
        # Emails.search_tsv is a stored generated column with a GIN index
        # (migration 0001). Matching against it -- rather than recomputing a
        # tsvector over email_chunks.chunk_text at query time -- is indexed, and
        # weights subject matches above body matches.
        selected = list(db.scalars(
            select(Email)
            .where(
                Email.user_id == user_id,
                Email.mailbox_connection_id == request.mailbox_id,
                ~Email.is_deleted,
                match_filter,
            )
            .order_by(func.ts_rank(EMAIL_SEARCH_VECTOR, rank_query).desc(), Email.received_at.desc())
            .limit(settings.rag_max_excerpts)
        ))

    excerpts = [
        Excerpt(
            label=f"E{index}",
            email_id=str(email.id),
            subject=email.subject or "(no subject)",
            sender=" ".join(filter(None, [email.sender_name, email.sender_address])) or "(unknown)",
            date=email.received_at.astimezone(UTC).date().isoformat(),
            text=(email.body_text or email.body_preview or email.subject or "")[: settings.rag_excerpt_tokens * 4],
            outlook_url=email.web_link,
        )
        for index, email in enumerate(selected, start=1)
    ]
    if not excerpts:
        result: dict[str, Any] = {
            "sufficient_evidence": False,
            "answer": None,
            # The curly apostrophe is deliberate: this string is shown to the user.
            "explanation": (
                "I couldn’t find a matching message just yet. Try asking another "  # noqa: RUF001
                "way, or check that your Outlook mailbox has finished syncing."
            ),
            "closest_emails": [],
        }
        if timeline_question:
            result["response_type"] = "timeline"
            result["timeline"] = []
        return result

    task = (
        "Create a chronological timeline of the developments described in these emails. Use the "
        "metadata dates as the only dates for ordering, include a source quote for every timeline "
        "item, and state when chronology is uncertain."
        if timeline_question
        else ""
    )
    prompt = f"Question:\n{request.question}\n{task}\n\nRetrieved email excerpts:\n{build_context(excerpts)}"
    try:
        if settings.llm_provider == "anthropic" and not reserve_claude_call(db, user_id, settings):
            raise HTTPException(429, "The daily Claude request limit has been reached. Try again tomorrow.")
        payload, _, _ = client.json_message(ANSWER_SYSTEM, prompt)
        result = verify_answer(payload, excerpts)
    except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.exception("Email answer generation failed using provider %s", settings.llm_provider)
        raise HTTPException(502, "The configured AI provider could not generate a valid answer.") from None

    result["closest_emails"] = [
        {
            "email_id": excerpt.email_id,
            "subject": excerpt.subject,
            "sender": excerpt.sender,
            "received_at": excerpt.date,
            "outlook_url": excerpt.outlook_url,
        }
        for excerpt in excerpts
    ]
    if timeline_question:
        result["response_type"] = "timeline"
        result["timeline"] = build_timeline(result, excerpts)
    return result
