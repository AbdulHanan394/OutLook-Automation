"""Path selection for the Ask bar's find side.

Both the streaming endpoint and the evaluation harness need the same decision --
operators go to indexed SQL, everything else goes through the existing keyword
pipeline -- so it lives here rather than being written twice and drifting.

This is the single place that guarantees the OL2 guarantee: an operator query
never reaches a model client, because ``client`` is only ever consulted on the
non-operator branch.
"""

from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.intent import Interpretation, interpret, keyword_only
from app.retrieval.clues import SearchClues, add_soft_clues
from app.retrieval.operator_search import operator_query, reasons_for, serialize_rows
from app.retrieval.operators import Operators, date_window, parse_operators
from app.retrieval.parse import parse_query
from app.retrieval.pipeline import keyword_search

Confidence = Literal["high", "low", "none"]


def interpret_query(
    raw: str,
    *,
    operators: Operators,
    mode: str,
    client: Any | None,
) -> Interpretation:
    return interpret(raw, operators=operators, mode=mode, client=client)


def run_operator_find(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    operators: Operators,
) -> tuple[list[dict[str, Any]], Confidence]:
    """Path A. Deterministic, indexed, and returns before any client is touched."""
    rows = operator_query(db, user_id=user_id, mailbox_id=mailbox_id, operators=operators)
    results = serialize_rows(rows, [reasons_for(row, operators) for row in rows])
    # These are explicit constraints the reader typed, so a match is not a guess.
    return results, "high" if results else "none"


def run_keyword_find(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    raw_query: str,
    operators: Operators,
    interpretation: Interpretation,
    settings: Settings,
) -> tuple[list[dict[str, Any]], Confidence]:
    """Path B. The same pipeline ``/api/search/email`` uses, so recall compares."""
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

    rows = [
        {
            "email_id": str(email.id),
            "subject": email.subject,
            "sender": email.sender_name or email.sender_address or "(unknown sender)",
            "received_at": email.received_at.isoformat(),
            "preview": (email.body_preview or email.body_text or "")[:300],
            "body": (email.body_text or email.body_preview or "")[:4000],
            "outlook_url": email.web_link,
            "why": [reason.detail for reason in reasons.get(email.id, [])],
            "reasons": [
                {"type": reason.type, "detail": reason.detail}
                for reason in reasons.get(email.id, [])
            ],
            "thread_count": 1,
        }
        for email in results
    ]

    if status == "high" and rows:
        return rows[:1], "high"
    if status == "low" and rows:
        return rows[: settings.max_alternatives], "low"
    return [], "none"


def find_with_intent(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    raw_query: str,
    settings: Settings,
    client: Any | None,
    mode: str = "auto",
) -> tuple[list[dict[str, Any]], Confidence, Interpretation]:
    """Route a find query and report which path ran.

    ``client`` may be ``None``; on the operator path it is never used.
    """
    operators = parse_operators(raw_query)

    if operators.any and not operators.has_free_text:
        results, confidence = run_operator_find(
            db, user_id=user_id, mailbox_id=mailbox_id, operators=operators
        )
        return results, confidence, keyword_only(raw_query, operators)

    interpretation = interpret(raw_query, operators=operators, mode=mode, client=client)
    if interpretation.intent != "find":
        # The caller asked for an answer; fall back to locating a message so the
        # reader still gets something actionable.
        interpretation = Interpretation(
            intent="find",
            rewrites=interpretation.rewrites,
            soft_clues=interpretation.soft_clues,
            operators=operators,
            chips=interpretation.chips,
            source=interpretation.source,
            fallback_notice=interpretation.fallback_notice,
        )

    results, confidence = run_keyword_find(
        db,
        user_id=user_id,
        mailbox_id=mailbox_id,
        raw_query=raw_query,
        operators=operators,
        interpretation=interpretation,
        settings=settings,
    )
    return results, confidence, interpretation