"""Result shaping on the Ask endpoint, without Postgres.

``tests/integration/test_ask_endpoint.py`` covers these paths end to end, but it
needs a real database. The pieces that decide *which* rows a reader sees --
ordering, the cutoff, duplicate collapsing and the reason chips -- are plain
Python over rows that are already in memory, so they are pinned here against
SQLite as well. A regression in the shape of a result list should fail in a
second, not need a migrated database.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, Text, create_engine
from sqlalchemy.orm import Session

from app.api.routes_ask import _best_email_id, _rows_for_citations, run_find
from app.config import get_settings
from app.db import Base, Email
from app.llm.intent import keyword_only
from app.retrieval.operators import parse_operators

BASE = datetime(2026, 1, 1, tzinfo=UTC)
QUESTION = "what did they say about the budget?"


class Mailbox:
    """One user's mailbox in a throwaway SQLite database."""

    def __init__(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[Email.__table__])
        # keyword_search always reads attachment chunks; those two tables use
        # Postgres-only types, so they are declared by hand (as test_retrieval
        # does) rather than created from the model metadata.
        metadata = MetaData()
        Table(
            "attachments", metadata,
            Column("id", String, primary_key=True), Column("user_id", String),
            Column("mailbox_connection_id", String), Column("email_id", String),
            Column("filename", Text), Column("file_type", String),
        )
        Table(
            "email_chunks", metadata,
            Column("user_id", String), Column("mailbox_connection_id", String),
            Column("email_id", String), Column("attachment_id", String),
            Column("source", String), Column("chunk_text", Text),
        )
        metadata.create_all(self.engine)
        self.user_id = uuid4()
        self.mailbox_id = uuid4()

    def session(self) -> Session:
        return Session(self.engine)

    def add(self, subject: str, body: str, *, days: int = 0) -> Email:
        with self.session() as session:
            row = Email(
                id=uuid4(),
                user_id=self.user_id,
                mailbox_connection_id=self.mailbox_id,
                graph_message_id=f"{subject}-{days}-{uuid4().hex[:8]}",
                folder_id="inbox",
                subject=subject,
                sender_name="Morgan Lee",
                sender_address="morgan@example.test",
                body_text=body,
                body_preview=body[:120],
                received_at=BASE + timedelta(days=days),
                content_hash=uuid4().hex * 2,
                # SQLite has no now(); the columns are set server-side on Postgres.
                created_at=BASE,
                updated_at=BASE,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def close(self) -> None:
        self.engine.dispose()


@pytest.fixture
def mailbox() -> Iterator[Mailbox]:
    box = Mailbox()
    yield box
    box.close()


def _find(mailbox: Mailbox, query: str):
    return run_find(
        mailbox.session(),
        user_id=mailbox.user_id,
        mailbox_id=mailbox.mailbox_id,
        raw_query=query,
        operators=parse_operators(query),
        interpretation=keyword_only(query, parse_operators(query)),
        settings=get_settings(),
    )


def test_the_answer_list_puts_the_citation_first_and_cuts_the_rest(mailbox):
    strong = mailbox.add("Revised budget approval", "The revised budget was approved on 12 March.")
    lunch = mailbox.add("Lunch", "Shall we get lunch on Friday?", days=1)

    rows = _rows_for_citations(
        mailbox.session(),
        mailbox.user_id,
        [{"email_id": str(strong.id)}, {"email_id": str(lunch.id)}],
        {str(strong.id)},
        question=QUESTION,
    )

    assert [row["email_id"] for row in rows] == [str(strong.id)]
    assert any(reason["detail"] == "Cited in the answer" for reason in rows[0]["reasons"])


def test_a_citation_leads_even_when_it_scores_worse_than_an_uncited_row(mailbox):
    weak_citation = mailbox.add("Notes", "One line mentioning the budget.")
    strong = mailbox.add(
        "Revised budget approval timeline", "The revised budget was approved on 12 March."
    )

    rows = _rows_for_citations(
        mailbox.session(),
        mailbox.user_id,
        [{"email_id": str(strong.id)}, {"email_id": str(weak_citation.id)}],
        {str(weak_citation.id)},
        question=QUESTION,
    )

    assert [row["email_id"] for row in rows] == [str(weak_citation.id), str(strong.id)]


def test_the_answer_list_reports_what_matched_not_what_was_asked(mailbox):
    row = mailbox.add("Revised budget approval", "The revised budget was approved on 12 March.")

    rows = _rows_for_citations(
        mailbox.session(), mailbox.user_id, [{"email_id": str(row.id)}], {str(row.id)},
        question=QUESTION,
    )

    details = [reason["detail"] for reason in rows[0]["reasons"]]
    assert "Closest match to your question" not in details
    assert any("budget" in detail.lower() for detail in details), details


def test_an_uncited_row_is_never_badged(mailbox):
    row = mailbox.add("Revised budget approval", "The revised budget was approved on 12 March.")

    rows = _rows_for_citations(
        mailbox.session(), mailbox.user_id, [{"email_id": str(row.id)}], set(),
        question=QUESTION,
    )

    assert _best_email_id(rows, set()) is None
    assert all(reason["detail"] != "Cited in the answer" for reason in rows[0]["reasons"])


def test_a_duplicate_of_a_cited_message_collapses_into_it(mailbox):
    strong = mailbox.add("Revised budget approval", "The revised budget was approved on 12 March.")
    copy = mailbox.add("Revised budget approval", "The revised budget was approved on 12 March.")
    # Same subject and body is what makes it a duplicate, and the stored content
    # hash is exactly what the sync job derives from those two fields.
    with mailbox.session() as session:
        stored = session.get(Email, copy.id)
        stored.normalized_content_hash = strong.normalized_content_hash
        session.commit()

    rows = _rows_for_citations(
        mailbox.session(),
        mailbox.user_id,
        [{"email_id": str(strong.id)}, {"email_id": str(copy.id)}],
        {str(strong.id)},
        question=QUESTION,
    )

    assert [row["email_id"] for row in rows] == [str(strong.id)]


def test_the_keyword_fallback_path_cuts_and_explains(mailbox):
    mailbox.add("Revised budget approval", "The revised budget was approved on 12 March.")
    mailbox.add("Lunch", "Shall we get lunch on Friday?", days=1)

    outcome = _find(mailbox, "revised budget")

    rows = outcome["results"]
    assert [row["subject"] for row in rows] == ["Revised budget approval"]
    assert any("budget" in detail.lower() for detail in rows[0]["why"])


def test_the_find_path_reports_no_match_when_the_words_are_absent(mailbox):
    mailbox.add("Lunch", "Shall we get lunch on Friday?")

    outcome = _find(mailbox, "quarterly headcount forecast")

    assert outcome["results"] == []
    assert outcome["confidence"] == "none"
    assert outcome.get("message"), "an empty result has to say why"


def test_another_users_email_is_never_returned(mailbox):
    intruder = mailbox.add("Revised budget approval", "The revised budget was approved.")
    with mailbox.session() as session:
        session.get(Email, intruder.id).user_id = uuid4()
        session.commit()

    rows = _rows_for_citations(
        mailbox.session(), mailbox.user_id, [{"email_id": str(intruder.id)}], set(),
        question=QUESTION,
    )

    assert rows == []


@pytest.mark.parametrize("item", [{"email_id": "not-a-uuid"}, {"email_id": ""}, {"email_id": "1234"}, {}])
def test_unparseable_citation_ids_are_ignored(mailbox, item):
    assert _rows_for_citations(mailbox.session(), mailbox.user_id, [item], set(), question=QUESTION) == []


def test_best_email_id_is_none_without_rows_or_citations():
    assert _best_email_id([], set()) is None
    assert _best_email_id([{"email_id": str(uuid4())}], set()) is None


def test_best_email_id_points_at_row_one_when_it_is_cited():
    identifier = str(UUID(int=1))
    assert _best_email_id([{"email_id": identifier}], {identifier}) == identifier