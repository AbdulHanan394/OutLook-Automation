import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import Attachment, Email, EmailChunk
from app.retrieval.confidence import confidence
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.parse import SEARCH_STOP_WORDS, Criteria, required_query_terms
from app.retrieval.relevance import (
    apply_relevance_cutoff,
    contains_phrase,
    contains_term,
    lexical_score,
)

__all__ = [
    "MatchReason",
    "collapse_ranked_emails",
    "contains_phrase",
    "contains_term",
    "feedback_tie_break",
    "keyword_search",
    "match_reasons",
    "token_similarity",
]


@dataclass(frozen=True)
class MatchReason:
    type: Literal["sender", "date", "phrase", "subject", "semantic", "attachment"]
    detail: str


def _quoted(terms: Sequence[str]) -> str:
    return ", ".join(f'"{term}"' for term in terms)


def match_reasons(email: Email, criteria: Criteria) -> list[MatchReason]:
    """Why this message matched, as chips a reader can act on.

    Two rules keep the chips honest enough to be worth reading:

    * stop words are dropped. "The message mentions \\"the\\"" is true of every
      email ever sent, and a reason that cannot discriminate is not a reason.
    * one chip per lane. A subject matching three query words is one fact about
      that message, not three, and three near-identical sentences buried the
      sender and date chips that actually explained the result.
    """
    subject = (email.subject or "").lower()
    body = (email.body_text or "").lower()
    sender = " ".join(filter(None, [email.sender_name, email.sender_address])).lower()
    reasons: list[MatchReason] = []

    for phrase in criteria.phrases:
        if contains_phrase(f"{subject} {body}", phrase):
            reasons.append(MatchReason("phrase", f'Includes the phrase "{phrase}".'))

    terms = [term for term in criteria.terms if term not in SEARCH_STOP_WORDS]
    subject_hits = [term for term in terms if contains_term(subject, term)]
    # A term already reported against the subject is not repeated against the
    # body: "Budget" in a subject titled "Budget review" is one match, not two.
    body_hits = [
        term
        for term in terms
        if term not in subject_hits and contains_term(body, term)
    ]
    if subject_hits:
        reasons.append(MatchReason("subject", f"The subject mentions {_quoted(subject_hits)}."))
    if body_hits:
        reasons.append(MatchReason("phrase", f"The message mentions {_quoted(body_hits)}."))

    if criteria.sender and criteria.sender.lower() in sender:
        reasons.append(MatchReason("sender", f'Sender matches "{criteria.sender}".'))

    if criteria.from_dt or criteria.to_dt:
        reasons.append(MatchReason("date", "Matched the selected received-date filter."))

    return reasons[:5]


def token_similarity(left: str, right: str) -> float:
    left_terms = set(re.findall(r"\w+", left.lower()))
    right_terms = set(re.findall(r"\w+", right.lower()))
    union = left_terms | right_terms
    return len(left_terms & right_terms) / len(union) if union else 0.0


def feedback_tie_break(
    ranked: list[tuple[str, float]],
    feedback_signals: dict[str, int],
    boost: float,
) -> list[tuple[str, float]]:
    return sorted(
        ranked,
        key=lambda item: (
            item[1],
            max(-1, min(1, feedback_signals.get(item[0], 0))) * boost,
        ),
        reverse=True,
    )


def collapse_ranked_emails(
    ranked: list[tuple[str, float]],
    emails_by_id: dict[str, Email],
) -> list[tuple[str, float]]:
    parents = {email_id: email_id for email_id in emails_by_id}
    owners: dict[str, str] = {}

    def root(email_id: str) -> str:
        while parents[email_id] != email_id:
            parents[email_id] = parents[parents[email_id]]
            email_id = parents[email_id]
        return email_id

    for email_id, email in emails_by_id.items():
        keys = []
        if email.thread_key:
            keys.append(f"thread:{email.thread_key}")
        if email.normalized_content_hash:
            keys.append(f"content:{email.normalized_content_hash}")
        for key in keys:
            other_id = owners.setdefault(key, email_id)
            left, right = root(email_id), root(other_id)
            if left != right:
                parents[right] = left

    collapsed: list[tuple[str, float]] = []
    seen_groups: set[str] = set()
    for email_id, score in ranked:
        if email_id not in parents:
            continue
        group_id = root(email_id)
        if group_id in seen_groups:
            continue
        seen_groups.add(group_id)
        collapsed.append((email_id, score))
    return collapsed


def keyword_search(
    db: Session,
    user_id: UUID,
    mailbox_id: UUID,
    criteria: Criteria,
    limit: int,
    min_score: float,
    margin: float,
    excluded_ids: set[UUID] | None = None,
    received_before: datetime | None = None,
    rejected_emails: list[Email] | None = None,
    negative_similarity_threshold: float = 0.35,
    feedback_signals: dict[str, int] | None = None,
    feedback_tie_break_boost: float = 0.0,
    file_types: Sequence[str] | None = None,
) -> tuple[str, list[Email], dict[UUID, list[MatchReason]]]:
    query = select(Email).where(Email.user_id == user_id, Email.mailbox_connection_id == mailbox_id, ~Email.is_deleted)
    if excluded_ids:
        query = query.where(Email.id.not_in(excluded_ids))
    if received_before:
        query = query.where(Email.received_at < received_before)
    if criteria.from_dt:
        query = query.where(Email.received_at >= criteria.from_dt)
    if criteria.to_dt:
        query = query.where(Email.received_at < criteria.to_dt)
    if criteria.folder:
        query = query.where(Email.folder_id == criteria.folder)
    emails = list(db.scalars(query.order_by(Email.received_at.desc()).limit(1000)))
    all_terms = criteria.terms + criteria.phrases
    required_terms, required_phrases = required_query_terms(criteria.query)
    email_text = {e.id: " ".join(filter(None, [e.subject, e.body_text])).lower() for e in emails}
    attachment_query = (
        select(
            EmailChunk.email_id,
            Attachment.filename,
            Attachment.file_type,
            EmailChunk.chunk_text,
        )
        .join(Attachment, Attachment.id == EmailChunk.attachment_id)
        .where(
            EmailChunk.user_id == user_id,
            EmailChunk.mailbox_connection_id == mailbox_id,
            EmailChunk.source == "attachment",
            Attachment.user_id == user_id,
            Attachment.mailbox_connection_id == mailbox_id,
            Attachment.email_id.in_([email.id for email in emails]),
        )
    )
    if file_types:
        attachment_query = attachment_query.where(Attachment.file_type.in_(file_types))
    if all_terms:
        attachment_query = attachment_query.where(or_(*[
            func.lower(EmailChunk.chunk_text).contains(term.lower(), autoescape=True)
            for term in all_terms
        ]))
        attachment_rows = list(db.execute(attachment_query))
    else:
        attachment_rows = []
    matched_attachments: dict[UUID, set[str]] = {}
    attachment_text: dict[UUID, list[str]] = {}
    for email_id, filename, _, chunk_text in attachment_rows:
        attachment_text.setdefault(email_id, []).append(chunk_text.lower())
        if not all_terms or any(
            contains_term(chunk_text, term) or contains_phrase(chunk_text, term)
            for term in all_terms
        ):
            matched_attachments.setdefault(email_id, set()).add(filename)
    attachment_ids = set(matched_attachments)
    phrase_list = [
        str(e.id) for e in emails
        if any(contains_phrase(email_text[e.id], p) for p in criteria.phrases)
        or any(
            contains_phrase(" ".join(attachment_text.get(e.id, [])), p)
            for p in criteria.phrases
        )
    ]
    all_list = [
        str(e.id) for e in emails
        if all(
            contains_term(email_text[e.id], term) or contains_phrase(email_text[e.id], term)
            for term in all_terms
        )
        or (
            e.id in attachment_text
            and all(
                contains_term(" ".join(attachment_text[e.id]), term)
                or contains_phrase(" ".join(attachment_text[e.id]), term)
                for term in all_terms
            )
        )
    ] if all_terms else []
    any_list = [
        str(e.id) for e in emails
        if any(
            contains_term(email_text[e.id], t) or contains_phrase(email_text[e.id], t)
            for t in all_terms
        ) or e.id in attachment_ids
    ]
    subject_list = [
        str(e.id) for e in emails
        if any(
            contains_term(e.subject or "", t) or contains_phrase(e.subject or "", t)
            for t in all_terms
        )
    ]
    sender_list = [str(e.id) for e in emails if criteria.sender and criteria.sender.lower() in " ".join(filter(None, [e.sender_name, e.sender_address])).lower()]
    lists = [item for item in [phrase_list, all_list, any_list, subject_list, sender_list] if item]
    fused = reciprocal_rank_fusion(lists)
    if attachment_ids:
        attachment_list = [str(e.id) for e in emails if e.id in attachment_ids]
        fused = reciprocal_rank_fusion([*lists, attachment_list])
    if file_types:
        eligible_ids = set(db.scalars(
            select(Attachment.email_id).where(
                Attachment.user_id == user_id,
                Attachment.mailbox_connection_id == mailbox_id,
                Attachment.email_id.in_([email.id for email in emails]),
                Attachment.file_type.in_(file_types),
            )
        ))
        fused = [item for item in fused if UUID(item[0]) in eligible_ids]
    if required_terms or required_phrases:
        fused = [
            (email_id, score)
            for email_id, score in fused
            if (
                all(
                    contains_term(
                        email_text[UUID(email_id)]
                        + " "
                        + " ".join(attachment_text.get(UUID(email_id), [])),
                        term,
                    )
                    for term in required_terms
                )
            )
            and all(
                contains_phrase(
                    email_text[UUID(email_id)]
                    + " "
                    + " ".join(attachment_text.get(UUID(email_id), [])),
                    phrase,
                )
                for phrase in required_phrases
            )
        ]
    by_id = {str(email.id): email for email in emails}
    usable_rejections = [email for email in rejected_emails or [] if email.subject or email.body_text]
    if usable_rejections:
        reranked = []
        for result_id, score in fused:
            candidate = by_id.get(result_id)
            if candidate is None:
                continue
            candidate_text = " ".join(filter(None, [candidate.subject, candidate.body_text]))
            similarity = max(token_similarity(
                candidate_text, " ".join(filter(None, [rejected.subject, rejected.body_text]))
            ) for rejected in usable_rejections)
            penalty = similarity if similarity >= negative_similarity_threshold else 0.0
            reranked.append((result_id, score * (1.0 - penalty)))
        fused = sorted(reranked, key=lambda item: item[1], reverse=True)
    if feedback_signals and feedback_tie_break_boost:
        fused = feedback_tie_break(fused, feedback_signals, feedback_tie_break_boost)
    if required_terms or required_phrases:
        subject_by_id = {str(email.id): (email.subject or "") for email in emails}
        relevance_scores: dict[str, float] = {}
        subject_rank: dict[str, int] = {}
        for email in emails:
            key = str(email.id)
            subject = subject_by_id[key]
            attachments = " ".join(attachment_text.get(email.id, []))
            relevance_scores[key] = lexical_score(
                subject,
                email_text[email.id],
                required_terms,
                required_phrases,
                attachment_text=attachments,
            )
            subject_rank[key] = sum(
                contains_term(subject, term) for term in required_terms
            ) + sum(contains_phrase(subject, phrase) for phrase in required_phrases)

        def relevance(item: tuple[str, float]) -> tuple[int, int, float]:
            email_id, fused_score = item
            return subject_rank[email_id], int(relevance_scores[email_id] * 100), fused_score

        fused.sort(key=relevance, reverse=True)
        # The cutoff runs after ordering so "the best" means the row the reader
        # actually sees at the top, not the row that happened to be retrieved
        # first. It runs before collapse_ranked_emails so a thread whose members
        # were all cut cannot contribute a group representative.
        fused = apply_relevance_cutoff(fused, lambda item: relevance_scores[item[0]])
    fused = collapse_ranked_emails(fused, by_id)
    selected = [by_id[email_id] for email_id, _ in fused[:limit] if email_id in by_id]
    scores = [score for _, score in fused]
    status = confidence(scores, min_score=min_score, margin=margin, agreeing_retrievers=sum(bool(x) for x in lists))
    reasons = {
        email.id: match_reasons(email, criteria)
        + [
            MatchReason("attachment", f"Matched in {name}.")
            for name in sorted(matched_attachments.get(email.id, set()))[:3]
        ]
        for email in selected
    }
    return status, selected, reasons
