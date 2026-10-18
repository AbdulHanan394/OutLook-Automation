"""Operator search: deterministic, indexed, and free of model calls.

This is path A of the Ask bar. Anything the reader expressed with an operator is
answered here, in SQL, against the same generated ``search_tsv`` column the Ask
path already uses (GIN-indexed, migration 0001). It deliberately does **not** go
through ``keyword_search``, which loads up to 1,000 rows into Python and scores
them there -- unusable at the 5,000-message scale the mail client targets.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.db import EMAIL_SEARCH_VECTOR, Attachment, Email
from app.mail.folders import folder_display_name
from app.retrieval.fts import build_match_filter, build_queries, build_rank_query, query_units
from app.retrieval.operators import Operators, date_window, sender_matches
from app.retrieval.relevance import rank_rows

MAX_RESULTS = 25


def operator_query(
    db: Session,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    operators: Operators,
    limit: int = MAX_RESULTS,
) -> list[Email]:
    """Messages matching every operator, best first. No Graph, no model."""
    after, before = date_window(operators)
    predicates = [
        Email.user_id == user_id,
        Email.mailbox_connection_id == mailbox_id,
        Email.is_deleted.is_(False),
    ]

    if operators.folders:
        aliases = [folder.lower() for folder in operators.folders]
        predicates.append(
            func.lower(func.coalesce(Email.folder_alias, Email.folder_id)).in_(aliases)
        )
    if operators.has_attachment:
        predicates.append(Email.has_attachments.is_(True))
    if operators.is_unread is not None:
        predicates.append(Email.is_read.is_(operators.is_unread))
    if operators.is_flagged:
        predicates.append(Email.is_flagged.is_(True))
    if after is not None:
        predicates.append(Email.received_at >= after)
    if before is not None:
        predicates.append(Email.received_at < before)

    for wanted in operators.senders:
        predicates.append(
            or_(
                func.lower(func.coalesce(Email.sender_address, "")).like(f"%{wanted.lower()}%"),
                func.lower(func.coalesce(Email.sender_name, "")).like(f"%{wanted.lower()}%"),
            )
        )

    for wanted in operators.recipients:
        # Recipients live in a JSONB blob, so this stays a text match rather
        # than a structured column query. A mailbox-wide recipient filter is a
        # rare operator and the row set is already narrowed by folder.
        predicates.append(
            func.lower(cast(Email.recipients, String)).like(
                f"%{wanted.lower()}%"
            )
        )

    query = select(Email).where(*predicates)

    # Free text left over after the operators is lifted still has to match
    # something, or "from:sara march budget" would return every email from Sara.
    text = operators.free_text.strip()
    if text:
        units = query_units(text)
        if units:
            match_filter = build_match_filter(EMAIL_SEARCH_VECTOR, build_queries(units))
            if match_filter is not None:
                query = query.where(match_filter)
            rank_query = build_rank_query(build_queries(units))
        else:
            rank_query = None
    else:
        rank_query = None

    if rank_query is not None:
        query = query.order_by(
            func.ts_rank(EMAIL_SEARCH_VECTOR, rank_query).desc(),
            Email.received_at.desc(),
        )
    else:
        query = query.order_by(Email.received_at.desc(), Email.id.desc())

    return list(db.scalars(query.limit(limit)))


def operator_attachment_names(
    db: Session, user_id: UUID, email_ids: list[UUID]
) -> dict[UUID, list[str]]:
    if not email_ids:
        return {}
    rows = db.execute(
        select(Attachment.email_id, Attachment.filename)
        .where(Attachment.user_id == user_id, Attachment.email_id.in_(email_ids))
        .order_by(Attachment.filename)
    ).all()
    names: dict[UUID, list[str]] = {}
    for email_id, filename in rows:
        names.setdefault(email_id, []).append(filename)
    return names


def serialize_rows(
    rows: list[Email], reasons: list[list[dict[str, str]]]
) -> list[dict[str, Any]]:
    """Rows plus their per-result reasons, in the shape the UI expects."""
    out: list[dict[str, Any]] = []
    for email, row_reasons in zip(rows, reasons, strict=True):
        sender = email.sender_name or email.sender_address or "(unknown sender)"
        if email.sender_name and email.sender_address:
            sender = f"{email.sender_name} <{email.sender_address}>"
        out.append(
            {
                "email_id": str(email.id),
                "subject": email.subject,
                "sender": sender,
                "received_at": email.received_at.isoformat(),
                "preview": (email.body_preview or email.body_text or "")[:300],
                "body": (email.body_text or email.body_preview or "")[:4000],
                "outlook_url": email.web_link,
                "why": [reason["detail"] for reason in row_reasons],
                "reasons": row_reasons,
                "thread_count": 1,
            }
        )
    return out


def reasons_for(email: Email, operators: Operators) -> list[dict[str, str]]:
    """Human-readable 'why this matched', mirroring the Find pipeline's shape."""
    reasons: list[dict[str, str]] = []
    for wanted in operators.senders:
        if sender_matches(email.sender_address, wanted) or sender_matches(email.sender_name, wanted):
            reasons.append({"type": "sender", "detail": f"From {wanted}"})
            break
    for wanted in operators.subjects:
        if wanted.lower() in (email.subject or "").lower():
            reasons.append({"type": "subject", "detail": f'Subject contains "{wanted}"'})
            break
    if operators.has_attachment and email.has_attachments:
        reasons.append({"type": "attachment", "detail": "Has an attachment"})
    if operators.is_unread is True and not email.is_read:
        reasons.append({"type": "date", "detail": "Unread"})
    if operators.is_unread is False and email.is_read:
        reasons.append({"type": "date", "detail": "Already read"})
    if operators.is_flagged and email.is_flagged:
        reasons.append({"type": "attachment", "detail": "Flagged"})
    if operators.after:
        reasons.append({"type": "date", "detail": f"Received after {operators.after.isoformat()}"})
    if operators.before:
        reasons.append({"type": "date", "detail": f"Received before {operators.before.isoformat()}"})
    if operators.folders:
        reasons.append({"type": "folder", "detail": f"In {folder_display_name(operators.folders[0])}"})
    return reasons[:5]


def rank_operator_rows(rows: list[Email], operators: Operators) -> list[Email]:
    """Drop operator results that never matched the words the reader typed.

    Every row here already satisfies the indexed predicates, so ``from:sara
    has:attachment`` is fully answered by SQL and needs nothing further. The
    exception is ``subject:``, which is not a predicate in the query at all --
    without this it returned the whole mailbox, newest first, with a "Subject
    contains" chip on the handful that actually matched. Scoring the subject
    words and applying the shared cutoff is what turns it back into a filter.
    """
    if not operators.subjects:
        # No words to compare on, so nothing is less relevant than anything else.
        return rows
    return rank_rows(rows, terms=operators.subjects, phrases=[])

