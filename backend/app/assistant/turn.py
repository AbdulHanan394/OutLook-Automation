"""One grounded answer to one question about one mailbox.

Extracted from ``/api/assistant/ask`` so the conversational endpoint answers a
turn exactly the way the one-shot endpoint does, rather than the two drifting
apart. The endpoint that calls this owns the mailbox lookup and the consent
check; everything after that -- retrieval, the model call, verification -- is
here.

Read-only by construction: this reads ``emails`` and writes nothing back to the
mailbox. Email text is sent to the model only as excerpts, and every claim the
model returns is re-checked against those excerpts by ``verify_answer`` before
it can be shown.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from typing import Any
from uuid import UUID

import anthropic
import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import EMAIL_SEARCH_VECTOR, Email
from app.llm.answer import ANSWER_SYSTEM, Excerpt, build_context, verify_answer
from app.llm.client import JsonMessageClient
from app.llm.commitments import reserve_claude_call
from app.llm.questions import extract_search_terms, is_recent_summary_question
from app.llm.timeline import build_timeline, is_timeline_question
from app.retrieval.fts import build_match_filter, build_queries, build_rank_query, query_units

logger = logging.getLogger(__name__)

SEARCH_SYSTEM = """The user is asking a natural-language question about their own emails. Extract up to 6 concise search terms or short phrases that are likely to appear in relevant email subjects or bodies. Prefer the most distinctive words in the question and drop conversational filler entirely. Preserve names, organizations, dates, and distinctive topic words. Do not add synonyms or words that the user did not imply, do not answer the question, and do not invent details. Return only a JSON object with a string array named keywords."""

NO_MATCH_EXPLANATION = (
    "I couldn’t find a matching message just yet. Try asking another "  # noqa: RUF001
    "way, or check that your Outlook mailbox has finished syncing."
)


def answer_with_tools(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    question: str,
    settings: Settings,
    client: JsonMessageClient,
    conversation: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """Answer via the bounded tool loop, or say why it could not.

    Returns the verified answer, the per-step trace (tool names and row counts
    only -- never a query, a subject or a body), and the provider accounting.

    Raises ``ToolsUnavailable`` when the provider cannot drive tools, which the
    caller answers by falling back to single-turn retrieval. That fallback is
    the point: a provider without tool support must degrade, not fail.
    """
    from app.assistant.tool_loop import ToolLoopOutcome, ToolsUnavailable, finalise, run_tool_loop

    outcome: ToolLoopOutcome = run_tool_loop(
        db,
        user_id=user_id,
        mailbox_id=mailbox_id,
        question=question,
        conversation=conversation,
        settings=settings,
        client=client,
    )
    if not outcome.excerpts:
        raise ToolsUnavailable("the tool loop collected nothing to answer from")
    try:
        result = finalise(
            question=question,
            conversation=conversation,
            outcome=outcome,
            settings=settings,
            client=client,
        )
    except ToolsUnavailable:
        raise
    except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.exception("Tool-loop answer generation failed using provider %s", settings.llm_provider)
        raise HTTPException(502, "The configured AI provider could not generate a valid answer.") from None
    return result, outcome.as_trace(), outcome.usage()


def _with_working_set(
    selected: list[Email], pinned: list[Email], limit: int
) -> list[Email]:
    """Put the conversation's working set in front of the ranked results.

    Pinned first, in the order the assistant was given them, then everything
    else ranked, de-duplicated by id and capped. A pinned message that retrieval
    did not rank this time -- because the follow-up wording no longer contains
    its words -- is exactly the one a follow-up question is usually about.
    """
    ordered: list[Email] = []
    seen: set[str] = set()
    for email in [*pinned, *selected]:
        key = str(email.id)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(email)
        if len(ordered) >= limit:
            break
    return ordered


def answer_question(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    question: str,
    settings: Settings,
    client: JsonMessageClient,
    conversation: str = "",
    pinned_emails: list[Email] | None = None,
) -> dict[str, Any]:
    """Answer ``question`` from ``mailbox_id``'s mail, with every quote verified.

    ``conversation`` carries earlier turns of the same chat and ``pinned_emails``
    the messages already under discussion. Both are optional: with neither, this
    is the original single-turn behaviour.

    Raises ``HTTPException`` for the cases the reader has to be told about
    (429 when the daily call cap is spent, 502 when the provider fails). Callers
    that need to degrade rather than fail -- the chat transcript, for instance --
    should catch 502 and fall back to keyword results.
    """
    timeline_question = is_timeline_question(question)
    recent_summary = is_recent_summary_question(question)

    if recent_summary:
        search_terms = ""
    else:
        try:
            _reserve(db, user_id, settings)
            search_payload, _, _ = client.json_message(SEARCH_SYSTEM, question, max_tokens=1024)
            search_terms = extract_search_terms(search_payload, question)
        except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.exception("Email question understanding failed using provider %s", settings.llm_provider)
            raise HTTPException(502, "The configured AI provider could not understand your question.") from None

    # Retrieval runs in two modes. Questions that ask for a broad "what's new"
    # recap bypass keywords entirely; everything else is matched full-text.
    unit_queries = [] if recent_summary else build_queries(query_units(question, search_terms))
    match_filter = build_match_filter(EMAIL_SEARCH_VECTOR, unit_queries)
    rank_query = build_rank_query(unit_queries)

    if match_filter is None:
        candidates = db.scalars(
            select(Email)
            .where(
                Email.user_id == user_id,
                Email.mailbox_connection_id == mailbox_id,
                ~Email.is_deleted,
            )
            .order_by(Email.received_at.desc())
            .limit(settings.rag_max_excerpts * 3)
        )
        # Keep one message per conversation so a busy thread cannot crowd out
        # everything else.
        selected: list[Email] = []
        seen_conversations: set[str] = set()
        for email in candidates:
            conversation = email.thread_key or str(email.id)
            if conversation in seen_conversations:
                continue
            seen_conversations.add(conversation)
            selected.append(email)
            if len(selected) == settings.rag_max_excerpts:
                break
    else:
        # emails.search_tsv is a stored generated column with a GIN index
        # (migration 0001). Matching against it -- rather than recomputing a
        # tsvector over email_chunks.chunk_text at query time -- is indexed, and
        # weights subject matches above body matches.
        selected = list(
            db.scalars(
                select(Email)
                .where(
                    Email.user_id == user_id,
                    Email.mailbox_connection_id == mailbox_id,
                    ~Email.is_deleted,
                    match_filter,
                )
                .order_by(func.ts_rank(EMAIL_SEARCH_VECTOR, rank_query).desc(), Email.received_at.desc())
                .limit(settings.rag_max_excerpts)
            )
        )

    # Messages already under discussion come first. "What about the second one?"
    # only resolves if last turn's candidates are still in front of the model, and
    # full-text ranking alone would not put them there.
    selected = _with_working_set(selected, pinned_emails or [], settings.rag_max_excerpts)

    excerpts = [
        Excerpt(
            label=f"E{index}",
            email_id=str(email.id),
            subject=email.subject or "(no subject)",
            sender=" ".join(filter(None, [email.sender_name, email.sender_address])) or "(unknown)",
            date=email.received_at.astimezone(UTC).date().isoformat(),
            text=(email.body_text or email.body_preview or email.subject or "")
            [: settings.rag_excerpt_tokens * 4],
            outlook_url=email.web_link,
        )
        for index, email in enumerate(selected, start=1)
    ]

    if not excerpts:
        result: dict[str, Any] = {
            "sufficient_evidence": False,
            "answer": None,
            "explanation": NO_MATCH_EXPLANATION,
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
    # Earlier turns are included only when there are any, so the single-turn path
    # sends byte-for-byte what it always sent.
    history = f"\n\nEarlier in this conversation:\n{conversation}" if conversation else ""
    prompt = (
        f"Question:\n{question}\n{task}{history}"
        f"\n\nRetrieved email excerpts:\n{build_context(excerpts)}"
    )
    try:
        _reserve(db, user_id, settings)
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


def _reserve(db: Session, user_id: UUID, settings: Settings) -> None:
    """Charge one call against the daily cap, if the provider has one."""
    if settings.llm_provider == "anthropic" and not reserve_claude_call(db, user_id, settings):
        raise HTTPException(429, "The daily Claude request limit has been reached. Try again tomorrow.")