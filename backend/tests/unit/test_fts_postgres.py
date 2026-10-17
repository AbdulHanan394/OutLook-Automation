"""Integration tests that execute the assistant retrieval query against Postgres.

The SQL-compilation tests in ``test_fts.py`` were green while the query was still
broken twice over: the OR group was unparenthesised, and then the tsquery values
were OR-ed instead of the boolean results (Postgres rejects that outright with
``argument of OR must be type boolean, not type tsquery``). Only executing the
statement catches this class of bug.

Set ``TEST_DATABASE_URL`` to run these. Each test creates and drops its own
throwaway schema, so it never touches the real ``public`` schema.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session

from app.db import EMAIL_SEARCH_VECTOR, Email
from app.retrieval.fts import build_match_filter, build_queries, build_rank_query, query_units

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL is not set; skipping Postgres integration tests"
)

# Mirrors migration 0001 for the columns this query touches, without the foreign
# keys (the referenced tables live in the real public schema).
EMAILS_DDL = """
CREATE TABLE emails (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    mailbox_connection_id uuid NOT NULL,
    graph_message_id text NOT NULL,
    internet_message_id text,
    conversation_id text,
    thread_key text,
    normalized_content_hash varchar(64),
    folder_id text NOT NULL,
    folder_alias text,
    subject text,
    sender_name text,
    sender_address text,
    recipients jsonb,
    received_at timestamptz NOT NULL,
    sent_at timestamptz,
    commitment_extracted_at timestamptz,
    has_attachments boolean NOT NULL DEFAULT false,
    body_text text,
    body_preview text,
    web_link text,
    content_hash varchar(64) NOT NULL,
    is_deleted boolean NOT NULL DEFAULT false,
    search_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(subject, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(body_text, '')), 'B')
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
)
"""


@pytest.fixture
def seeded_db():
    """A throwaway schema holding a realistic hiring email plus decoys."""
    engine = create_engine(DATABASE_URL)
    schema = f"fts_test_{uuid.uuid4().hex[:12]}"
    user_id, mailbox_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    rows = [
        # The target. "software" appears only in the body, "hiring" in the subject.
        ("Hiring for SE", "Honey OP", "We are hiring a Software Developer for the platform team."),
        ("Lunch options", "Canteen", "Choose your meal for the team lunch on Friday."),
        ("Invoice 991", "Billing", "Your invoice is attached and due next week."),
    ]
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET search_path TO "{schema}"'))
        conn.execute(text(EMAILS_DDL))
        for index, (subject, sender, body) in enumerate(rows):
            conn.execute(
                text(
                    "INSERT INTO emails (id, user_id, mailbox_connection_id, graph_message_id, "
                    "folder_id, subject, sender_name, body_text, received_at, content_hash) "
                    "VALUES (:id, :uid, :mid, :gid, 'inbox', :subject, :sender, :body, :at, :hash)"
                ),
                {
                    "id": uuid.uuid4(), "uid": user_id, "mid": mailbox_id,
                    "gid": f"msg-{index}", "subject": subject, "sender": sender,
                    "body": body, "at": now, "hash": f"hash-{index}",
                },
            )

    with engine.connect() as conn:
        conn.execute(text(f'SET search_path TO "{schema}"'))
        conn.commit()
        yield Session(bind=conn), user_id, mailbox_id

    engine.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def retrieve(db, user_id, mailbox_id, question, keywords=""):
    """Run exactly the query routes_assistant runs for a non-summary question."""
    queries = build_queries(query_units(question, keywords))
    match_filter = build_match_filter(EMAIL_SEARCH_VECTOR, queries)
    rank_query = build_rank_query(queries)
    return [
        row.subject
        for row in db.query(Email.subject)
        .filter(
            Email.user_id == user_id,
            Email.mailbox_connection_id == mailbox_id,
            Email.is_deleted.is_(False),
            match_filter,
        )
        .order_by(func.ts_rank(EMAIL_SEARCH_VECTOR, rank_query).desc())
        .all()
    ]


def test_full_sentence_question_matches_the_right_email(seeded_db):
    """The exact reported failure: natural language returned nothing."""
    db, user_id, mailbox_id = seeded_db
    assert retrieve(db, user_id, mailbox_id, "tell me about the software hiring email") == ["Hiring for SE"]


def test_keywords_and_sentence_retrieve_identically(seeded_db):
    db, user_id, mailbox_id = seeded_db
    sentence = retrieve(db, user_id, mailbox_id, "tell me about the software hiring email")
    keywords = retrieve(db, user_id, mailbox_id, "software hiring email")
    assert sentence == keywords == ["Hiring for SE"]


def test_single_unit_question_still_matches(seeded_db):
    db, user_id, mailbox_id = seeded_db
    assert retrieve(db, user_id, mailbox_id, "tell me about the invoice") == ["Invoice 991"]


def test_irrelevant_question_matches_nothing_rather_than_everything(seeded_db):
    """Guards the unparenthesised-OR defect that would return the whole mailbox."""
    db, user_id, mailbox_id = seeded_db
    assert retrieve(db, user_id, mailbox_id, "tell me about the server migration") == []


def test_subject_matches_outrank_body_matches(seeded_db):
    """setweight(subject,'A') should surface a subject hit above a body-only hit."""
    db, user_id, mailbox_id = seeded_db
    assert retrieve(db, user_id, mailbox_id, "hiring lunch")[0] == "Hiring for SE"