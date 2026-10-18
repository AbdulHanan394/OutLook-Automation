"""The Ask bar's streaming endpoint.

One request, typed server-sent events, so the UI can show retrieval results the
moment they exist instead of waiting for the answer text to finish:

    interpretation -> results -> answer_delta* -> citations -> done

Three execution paths, decided in this order:

* **A. Operators** (``from:sara has:attachment``): indexed SQL, zero model calls.
* **B. Find** ("the email from Sara about the March budget"): the interpretation
  step, then the existing ``keyword_search`` pipeline, so recall stays comparable
  to ``/api/search/email``.
* **C. Ask** ("what did the client say about the deadline"): retrieval, then the
  existing ``verify_answer`` RAG step, whose quote-containment check is the only
  thing between a model and a confident lie.

Every answer is verified server-side. Confidence is always high/low/none and
never "exact". If the provider is unavailable or the daily cap is reached the
endpoint degrades to a keyword search and says so in one line rather than
failing -- and it never retries, because a spend cap must not become a loop.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any, Literal
from uuid import UUID

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import EMAIL_SEARCH_VECTOR, AskHistory, Email, MailboxConnection, get_db, set_user_scope
from app.llm.answer import ANSWER_SYSTEM, Excerpt, build_context, verify_answer
from app.llm.commitments import provider_limit_reason, reserve_claude_call
from app.llm.factory import create_json_client
from app.llm.intent import Interpretation, interpret, interpretation_to_json, keyword_only
from app.llm.questions import is_recent_summary_question, recent_summary_topic
from app.llm.timeline import build_timeline, is_timeline_question
from app.retrieval.clues import SearchClues, add_soft_clues
from app.retrieval.fts import build_match_filter, build_queries, build_rank_query, query_units
from app.retrieval.operator_search import (
    operator_query,
    rank_operator_rows,
    reasons_for,
    serialize_rows,
)
from app.retrieval.operators import Operators, date_window, parse_operators
from app.retrieval.parse import parse_query
from app.retrieval.pipeline import MatchReason, keyword_search, match_reasons
from app.retrieval.relevance import rank_rows
from app.security.csrf import require_csrf
from app.security.rate_limit import enforce_user_rate_limit
from app.security.sessions import require_user

router = APIRouter(prefix="/api/mail", tags=["ask"])
logger = logging.getLogger(__name__)

MAX_HISTORY = 50
EXAMPLES = (
    "Find the email from Sara about the March budget",
    "What did the client say about the deadline?",
    "Summarise the marketing campaign emails",
    "from:sara@example.com has:attachment before:2026-04-01",
)

# One line, one meaning. When the provider cannot be reached the reader is told
# that and nothing else: a fallback is not a failed answer, and pairing it with
# "I could not answer that" claimed an attempt was made that never happened.
AI_UNAVAILABLE_NOTICE = "AI unavailable, showing keyword matches."

SSE_HEADERS = {
    "Cache-Control": "no-store, private",
    "Connection": "keep-alive",
    # nginx buffers text/event-stream by default, which would collapse the whole
    # stream into one delivery and defeat the point of streaming it.
    "X-Accel-Buffering": "no",
}

# ANSWER_SYSTEM is shared with /api/assistant/ask -- see app.llm.answer.

KEYWORD_SYSTEM = """Extract short search keywords from the question. Email content is not supplied. Return only JSON with a keywords string array of at most 12 terms."""


class AskRequest(BaseModel):
    mailbox_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["auto", "find", "ask"] = "auto"
    session_id: str | None = Field(default=None, max_length=64)
    folder: str | None = Field(default=None, max_length=200)


class RecordAskRequest(BaseModel):
    mailbox_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["auto", "find", "ask"] = "auto"


def owned_mailbox(mailbox_id: UUID, user_id: UUID, db: Session) -> MailboxConnection:
    mailbox = db.scalar(
        select(MailboxConnection).where(
            MailboxConnection.id == mailbox_id,
            MailboxConnection.user_id == user_id,
        )
    )
    if not mailbox:
        raise HTTPException(404, "Mailbox not found")
    return mailbox


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def reason_dicts(reasons: list[MatchReason]) -> list[dict[str, str]]:
    return [{"type": reason.type, "detail": reason.detail} for reason in reasons[:5]]


def row_payload(email: Email, reasons: list[dict[str, str]]) -> dict[str, Any]:
    sender = email.sender_name or email.sender_address or "(unknown sender)"
    if email.sender_name and email.sender_address:
        sender = f"{email.sender_name} <{email.sender_address}>"
    return {
        "email_id": str(email.id),
        "subject": email.subject,
        "sender": sender,
        "received_at": email.received_at.isoformat(),
        "preview": (email.body_preview or email.body_text or "")[:300],
        "body": (email.body_text or email.body_preview or "")[:4000],
        "outlook_url": email.web_link,
        "why": [reason["detail"] for reason in reasons],
        "reasons": reasons,
        "thread_count": 1,
    }


def build_excerpts(emails: list[Email], settings: Settings) -> list[Excerpt]:
    """Excerpts for the RAG prompt.

    Body text only. Anything the model sees is delimited by ``build_context`` and
    escaped, and every claim it produces is re-checked against these strings.
    """
    return [
        Excerpt(
            label=f"E{index}",
            email_id=str(email.id),
            subject=email.subject or "(no subject)",
            sender=email.sender_address or email.sender_name or "(unknown)",
            date=email.received_at.isoformat(),
            text=(email.body_text or email.body_preview or email.subject or "")[
                : settings.rag_excerpt_tokens * 4
            ],
            outlook_url=email.web_link,
        )
        for index, email in enumerate(emails, start=1)
    ]


def search_terms_for(client: Any, question: str) -> list[str]:
    """Keyword extraction with a deterministic fallback.

    A provider failure here must not lose the question, so the tokens of the
    question itself are used instead.
    """
    try:
        payload, _, _ = client.json_message(KEYWORD_SYSTEM, question, 1024)
        keywords = payload.get("keywords")
        if isinstance(keywords, list):
            terms = [str(item)[:60] for item in keywords if isinstance(item, str)]
            if terms:
                return terms[:12]
    except (anthropic.APIError, httpx.HTTPError, ValueError, TypeError, KeyError):
        pass
    return [term for term in parse_query(question).terms if len(term) > 2][:12]


def _recent_candidates(
    db: Session, base: list[Any], limit: int
) -> list[Email]:
    """Most recent messages, at most one per conversation.

    Without the conversation filter a single long thread fills every excerpt
    slot, and a "summarise my latest emails" question ends up describing one
    conversation instead of the inbox. This mirrors the /api/assistant/ask
    retrieval so both surfaces answer a recap question from comparable evidence.
    """
    candidates = db.scalars(
        select(Email).where(*base).order_by(Email.received_at.desc()).limit(limit * 3)
    )
    selected: list[Email] = []
    seen_conversations: set[str] = set()
    for email in candidates:
        conversation = email.thread_key or str(email.id)
        if conversation in seen_conversations:
            continue
        seen_conversations.add(conversation)
        selected.append(email)
        if len(selected) == limit:
            break
    return selected


def retrieve_for_question(
    db: Session, *, user_id: UUID, mailbox_id: UUID, question: str, settings: Settings, client: Any
) -> list[Email]:
    base = [
        Email.user_id == user_id,
        Email.mailbox_connection_id == mailbox_id,
        Email.is_deleted.is_(False),
    ]

    if is_recent_summary_question(question) and not recent_summary_topic(question):
        # "summarise my recent emails" has no topic; recency is the query.
        return _recent_candidates(db, base, settings.rag_max_excerpts)

    # A recap that names a subject -- "summarise my latest emails about SE
    # hiring" -- is a search on that subject, not a request for whatever is
    # newest. Treating it as a bare recap returned unrelated recent messages.
    terms = recent_summary_topic(question) or question
    units = query_units(" ".join(search_terms_for(client, terms)))
    queries = build_queries(units)
    match_filter = build_match_filter(EMAIL_SEARCH_VECTOR, queries)
    if match_filter is None:
        return []
    # Ranked by relevance, not by date. The excerpt budget is small and the
    # candidate list is capped just after it, so recency ordering spent those
    # slots on the newest twelve messages that matched a single common word and
    # the message the reader actually meant never reached the model at all.
    # Recency stays as the tie-break so equally relevant messages read newest first.
    rank_query = build_rank_query(queries)
    ordering = [func.ts_rank(EMAIL_SEARCH_VECTOR, rank_query).desc(), Email.received_at.desc()]
    return list(
        db.scalars(
            select(Email)
            .where(*base, match_filter)
            .order_by(*(ordering if rank_query is not None else [Email.received_at.desc()]))
            .limit(settings.rag_max_excerpts)
        )
    )


def run_answer(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    question: str,
    settings: Settings,
    client: Any,
) -> dict[str, Any]:
    emails = retrieve_for_question(
        db, user_id=user_id, mailbox_id=mailbox_id, question=question,
        settings=settings, client=client,
    )
    if not emails:
        return {
            "sufficient_evidence": False,
            "answer": None,
            "explanation": "I could not find this in your emails.",
            "closest_emails": [],
        }

    excerpts = build_excerpts(emails, settings)
    # The question has to be in the prompt. Without it the answer model sees a
    # pile of excerpts and an instruction to answer a question it never
    # received, and correctly reports that it has no evidence -- which is what
    # made "summarize my latest emails" fail here while /app/ask answered it.
    answer_prompt = (
        f"Question:\n{question}\n\nRetrieved email excerpts:\n{build_context(excerpts)}"
    )
    payload, _, _ = client.json_message(ANSWER_SYSTEM, answer_prompt, 1200)
    result = verify_answer(payload, excerpts)

    if not result.get("sufficient_evidence"):
        result["closest_emails"] = [
            {
                "email_id": excerpt.email_id,
                "subject": excerpt.subject,
                "sender": excerpt.sender,
                "received_at": excerpt.date[:10],
                "outlook_url": excerpt.outlook_url,
            }
            for excerpt in excerpts
        ]
        return result

    result["closest_emails"] = [
        {
            "email_id": excerpt.email_id,
            "subject": excerpt.subject,
            "sender": excerpt.sender,
            "received_at": excerpt.date[:10],
            "outlook_url": excerpt.outlook_url,
        }
        for excerpt in excerpts
    ]
    if is_timeline_question(question):
        # build_timeline re-verifies each quote against the excerpt itself; it is
        # a second check, not a restatement of verify_answer.
        result["response_type"] = "timeline"
        result["timeline"] = build_timeline(result, excerpts)
    return result


def run_find(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    raw_query: str,
    operators: Operators,
    interpretation: Interpretation,
    settings: Settings,
) -> dict[str, Any]:
    """Path B: the existing keyword pipeline, so recall matches /api/search."""
    criteria = parse_query(operators.free_text or raw_query)
    if interpretation.rewrites or interpretation.soft_clues:
        criteria = add_soft_clues(
            criteria,
            SearchClues(rewrites=interpretation.rewrites, soft_clues=interpretation.soft_clues),
        )
    if operators.any:
        start, end = date_window(operators)
        criteria = type(criteria)(
            query=criteria.query,
            terms=criteria.terms,
            phrases=criteria.phrases,
            sender=operators.senders[0] if operators.senders else criteria.sender,
            from_dt=start or criteria.from_dt,
            to_dt=end or criteria.to_dt,
            folder=operators.folders[0] if operators.folders else criteria.folder,
        )

    status, results, reasons = keyword_search(
        db,
        user_id=user_id,
        mailbox_id=mailbox_id,
        criteria=criteria,
        limit=settings.max_alternatives,
        min_score=settings.conf_min_score,
        margin=settings.conf_margin,
    )

    rows = [row_payload(email, reason_dicts(reasons.get(email.id, []))) for email in results]
    if status == "high" and rows:
        return {"results": rows, "confidence": "high", "best_email_id": rows[0]["email_id"]}
    if status == "low" and rows:
        return {"results": rows, "confidence": "low"}
    return {
        "results": [],
        "confidence": "none",
        "message": "No reliable match found. Try rewording, or remove a filter.",
    }


def text_chunks(text: str, size: int = 240) -> list[str]:
    """Split an already-verified answer for incremental delivery.

    The text is complete and verified before the first chunk is emitted; this
    only controls how it arrives, never what it says.
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


@router.post("/ask")
def ask(
    request: AskRequest,
    user_id: UUID = Depends(require_user),
    _csrf: Any = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    mailbox = owned_mailbox(request.mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "ask", settings.rate_limit_ask_per_min)

    operators = parse_operators(request.query)
    operators_only = operators.any and not operators.has_free_text

    def stream() -> Iterator[str]:
        # ---- Path A: operators only. Deterministic, zero model calls. -------
        if operators_only:
            rows = rank_operator_rows(
                operator_query(db, user_id=user_id, mailbox_id=mailbox.id, operators=operators),
                operators,
            )
            results = serialize_rows(rows, reasons_for_all(rows, operators))
            yield sse("interpretation", interpretation_to_json(keyword_only(request.query, operators)))
            yield sse(
                "results",
                {
                    "results": results,
                    "confidence": "high" if results else "none",
                    "message": None if results else "No messages matched those operators.",
                },
            )
            yield sse("done", {"used_model": False, "path": "operator"})
            return

        notice: dict[str, Any] | None = None
        client: Any | None = None
        if mailbox.external_ai_consent_at:
            if reserve_claude_call(db, user_id, settings):
                try:
                    client = create_json_client(settings, "interpret")
                except HTTPException:
                    # The provider is unconfigured or the spend cap is spent;
                    # both already mean "fall back to keywords".
                    client = None
            if client is None:
                notice = {
                    "message": "The daily AI request limit has been reached. "
                    "Showing keyword matches.",
                    "fallback": True,
                }
        else:
            notice = {
                "message": "AI interpretation is off for this mailbox. "
                "Showing keyword matches.",
                "fallback": True,
            }

        interpretation = interpret(
            request.query, operators=operators, mode=request.mode, client=client
        )
        payload_interpretation = interpretation_to_json(interpretation)
        if notice:
            # The route knows the real reason (daily cap, or no consent) and that
            # is more useful to the reader than the generic message interpret()
            # falls back to when handed a null client.
            payload_interpretation["fallback_notice"] = notice["message"]
            payload_interpretation["fallback"] = True
            if not payload_interpretation["used_model"]:
                payload_interpretation["source"] = "keyword"
        yield sse("interpretation", payload_interpretation)

        # ---- Path C: answer ------------------------------------------------
        if interpretation.intent == "ask":
            if client is None:
                # No provider, no answer. Say so in one line and run the keyword
                # search the reader would have got anyway: returning an empty
                # result set here used to leave the UI claiming it could not
                # answer, which is a different and much worse failure.
                yield sse(
                    "error",
                    {"message": AI_UNAVAILABLE_NOTICE, "fallback": True},
                )
                yield from _keyword_fallback(db, user_id, mailbox.id, request, operators, settings)
                return
            try:
                result = run_answer(
                    db,
                    user_id=user_id,
                    mailbox_id=mailbox.id,
                    question=operators.free_text or request.query,
                    settings=settings,
                    client=client,
                )
            except (anthropic.APIError, httpx.HTTPError) as error:
                limit = provider_limit_reason(error)
                yield sse(
                    "error",
                    {"message": AI_UNAVAILABLE_NOTICE, "fallback": True, "detail": limit},
                )
                # Fall straight through to a keyword find rather than failing.
                yield from _keyword_fallback(db, user_id, mailbox.id, request, operators, settings)
                return

            answer = result.get("answer")
            if answer:
                for chunk in text_chunks(answer):
                    yield sse("answer_delta", {"text": chunk})
            else:
                yield sse(
                    "answer_delta",
                    {"text": result.get("explanation") or "I could not find this in your emails."},
                )
            yield sse(
                "citations",
                {
                    "sufficient": bool(result.get("sufficient_evidence")),
                    "claims": result.get("claims", []),
                    "response_type": result.get("response_type"),
                    "timeline": result.get("timeline", []),
                },
            )
            cited_ids = _cited_email_ids(result.get("claims", []))
            answer_rows = _rows_for_citations(
                db,
                user_id,
                result.get("closest_emails", []),
                cited_ids,
                question=operators.free_text or request.query,
            )
            yield sse(
                "results",
                {
                    "results": answer_rows,
                    "confidence": "high" if result.get("sufficient_evidence") else "none",
                    # Row 1 is the best-scoring verified citation, so the badge,
                    # the "Cited in the answer" chip and the message the reading
                    # pane opens all agree. Without this the client falls back to
                    # the first row, which opened an unrelated message while the
                    # answer described a different one.
                    "best_email_id": _best_email_id(answer_rows, cited_ids),
                },
            )
            yield sse("done", {"used_model": True, "path": "ask"})
            return

        # ---- Path B: find ---------------------------------------------------
        yield from _find_events(db, user_id, mailbox.id, request, operators, interpretation, settings)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


def reasons_for_all(rows: list[Email], operators: Operators) -> list[list[dict[str, str]]]:
    return [reasons_for(row, operators) for row in rows]


def _best_email_id(rows: list[dict[str, Any]], cited: set[str] | None = None) -> str | None:
    """The email the reading pane should open for this answer.

    ``_rows_for_citations`` already puts the highest-scoring verified citation
    first, so row 1 is the best match and the badge belongs there. When nothing
    was verified there is no best match at all: the badge would claim the answer
    found something it did not find, and the client falls back to the first row,
    which is the correct default for "these are just the closest ones".
    """
    if not rows or not cited:
        return None
    return rows[0]["email_id"] if rows[0]["email_id"] in cited else None


def _cited_email_ids(claims: list[dict[str, Any]]) -> set[str]:
    """Email ids that appear in at least one verified claim.

    Quotes are verified against the excerpts before this runs, so membership
    here means the model actually cited the message, not merely that retrieval
    surfaced it.
    """
    ids: set[str] = set()
    for claim in claims:
        for email_id in claim.get("email_ids", []) or []:
            ids.add(str(email_id))
    return ids


def _rows_for_citations(
    db: Session,
    user_id: UUID,
    closest: list[dict[str, Any]],
    cited: set[str] | None = None,
    *,
    question: str = "",
) -> list[dict[str, Any]]:
    """Rehydrate cited emails so the list can show real subjects and previews.

    The ids come from our own verified excerpt labels, but each is re-fetched
    through the same user predicate as everything else rather than trusted
    because it appears in a model response.

    ``cited`` is the set of email ids that actually appear in a verified claim.
    Only those may be described as cited: when the model reports insufficient
    evidence the claims array is empty, and labelling those rows "Cited in the
    answer" would assert something that never happened.

    The rows go through the same ordering rules as every other result list --
    cited first, best-scoring first, duplicates collapsed, weak tail cut off --
    so the reader's first row is the message the answer actually rests on.
    """
    ids: list[UUID] = []
    for item in closest:
        try:
            ids.append(UUID(str(item["email_id"])))
        except (KeyError, ValueError):
            continue
    if not ids:
        return []
    emails = list(
        db.scalars(
            select(Email).where(
                Email.user_id == user_id,
                Email.id.in_(ids),
                Email.is_deleted.is_(False),
            )
        )
    )
    cited_ids = cited or set()
    criteria = parse_query(question) if question.strip() else None
    ordered = rank_rows(
        emails,
        terms=criteria.terms if criteria else [],
        phrases=criteria.phrases if criteria else [],
        cited=cited_ids,
    )
    rows: list[dict[str, Any]] = []
    for email in ordered:
        reasons: list[dict[str, str]] = []
        if str(email.id) in cited_ids:
            reasons.append({"type": "citation", "detail": "Cited in the answer"})
        if criteria is not None:
            # What actually matched, not a restatement of the question. A row
            # with no lexical match simply shows no chip, which is honest: the
            # model cited it for a reason the words alone do not explain.
            reasons.extend(reason_dicts(match_reasons(email, criteria)))
        rows.append(row_payload(email, reasons[:5]))
    return rows


def _find_events(
    db: Session,
    user_id: UUID,
    mailbox_id: UUID,
    request: AskRequest,
    operators: Operators,
    interpretation: Interpretation,
    settings: Settings,) -> Iterator[str]:
    try:
        outcome = run_find(
            db,
            user_id=user_id,
            mailbox_id=mailbox_id,
            raw_query=request.query,
            operators=operators,
            interpretation=interpretation,
            settings=settings,
        )
    except (anthropic.APIError, httpx.HTTPError) as error:
        yield sse(
            "error",
            {
                "message": AI_UNAVAILABLE_NOTICE,
                "fallback": True,
                "detail": provider_limit_reason(error),
            },
        )
        yield from _keyword_fallback(db, user_id, mailbox_id, request, operators, settings)
        return
    yield sse("results", outcome)
    yield sse("done", {"used_model": interpretation.used_model, "path": "find"})


def _keyword_fallback(
    db: Session,
    user_id: UUID,
    mailbox_id: UUID,
    request: AskRequest,
    operators: Operators,
    settings: Settings,
) -> Iterator[str]:
    """No model available: run the lexical find the reader could have run.

    This deliberately reuses ``run_find`` rather than a second retrieval
    implementation. The old inline version ordered by recency, returned at most
    ``max_alternatives`` rows whatever they scored, and reported operator reasons
    for what was really a free-text match -- so a fallback list was the weakest
    list in the product. Sharing the path means it is ranked, de-duplicated and
    cut off by exactly the same rules as a real find, and its rows carry the
    same real reasons.

    Confidence is capped at ``low``: a degraded retrieval did not earn a strong
    match by being the only one left.
    """
    outcome = run_find(
        db,
        user_id=user_id,
        mailbox_id=mailbox_id,
        raw_query=request.query,
        operators=operators,
        interpretation=keyword_only(request.query, operators),
        settings=settings,
    )
    results = outcome.get("results") or []
    yield sse(
        "results",
        {
            "results": results,
            "confidence": "low" if results else "none",
            "message": None if results else "No messages matched those words.",
            "best_email_id": None,
        },
    )
    yield sse("done", {"used_model": False, "path": "keyword-fallback"})


@router.get("/ask/history")
def ask_history(
    mailbox_id: UUID = Query(),
    user_id: UUID = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Recent asks for this user, newest first."""
    owned_mailbox(mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    rows = db.scalars(
        select(AskHistory)
        .where(AskHistory.user_id == user_id)
        .order_by(AskHistory.created_at.desc())
        .limit(MAX_HISTORY)
    ).all()
    return {
        "history": [
            {
                "id": str(row.id),
                "query": row.query,
                "mode": row.mode,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "examples": list(EXAMPLES),
    }


@router.post("/ask/history")
def record_ask(
    request: RecordAskRequest,
    user_id: UUID = Depends(require_user),
    _csrf: Any = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    owned_mailbox(request.mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "ask", settings.rate_limit_ask_per_min)
    db.add(
        AskHistory(
            user_id=user_id,
            query=request.query[:1000],
            mode=request.mode,
        )
    )
    db.commit()
    return {"recorded": True}


@router.delete("/ask/history")
def clear_ask_history(
    user_id: UUID = Depends(require_user),
    _csrf: Any = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    set_user_scope(db, user_id)
    db.execute(delete(AskHistory).where(AskHistory.user_id == user_id))
    db.commit()
    return {"cleared": True}

