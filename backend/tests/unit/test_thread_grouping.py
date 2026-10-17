from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes_search import related_emails
from app.db import Base, Email
from app.ingest.extract import normalized_content_hash
from app.retrieval.pipeline import collapse_ranked_emails


def test_collapse_groups_conversation_and_normalized_content_transitively():
    messages = {
        "a": SimpleNamespace(thread_key="thread-a", normalized_content_hash="hash-a"),
        "b": SimpleNamespace(thread_key="thread-a", normalized_content_hash="hash-b"),
        "c": SimpleNamespace(thread_key="thread-c", normalized_content_hash="hash-b"),
        "d": SimpleNamespace(thread_key="thread-d", normalized_content_hash="hash-d"),
    }

    assert collapse_ranked_emails(
        [("a", 0.8), ("b", 0.7), ("c", 0.6), ("d", 0.5)],
        messages,
    ) == [("a", 0.8), ("d", 0.5)]


def test_normalized_content_hash_ignores_case_and_whitespace():
    assert normalized_content_hash("Planning Notes", "  Q4\n Budget  ") == normalized_content_hash(
        "planning notes", "q4 budget"
    )
    assert normalized_content_hash(None, None) is None


def test_thread_expansion_is_transitive_and_user_scoped(monkeypatch):
    monkeypatch.setattr("app.api.routes_search.set_user_scope", lambda db, user_id: None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Email.__table__])
    user_id, other_user_id, mailbox_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    emails = [
        Email(
            id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
            graph_message_id="a", folder_id="inbox", received_at=now,
            content_hash="a", thread_key="thread-a", normalized_content_hash="hash-a",
            created_at=now, updated_at=now,
        ),
        Email(
            id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
            graph_message_id="b", folder_id="inbox", received_at=now,
            content_hash="b", thread_key="thread-a", normalized_content_hash="hash-b",
            created_at=now, updated_at=now,
        ),
        Email(
            id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
            graph_message_id="c", folder_id="inbox", received_at=now,
            content_hash="c", thread_key="thread-c", normalized_content_hash="hash-b",
            created_at=now, updated_at=now,
        ),
        Email(
            id=uuid4(), user_id=other_user_id, mailbox_connection_id=mailbox_id,
            graph_message_id="private", folder_id="inbox", received_at=now,
            content_hash="private", thread_key="thread-private", normalized_content_hash="hash-b",
            created_at=now, updated_at=now,
        ),
    ]
    with Session(engine) as db:
        db.add_all(emails)
        db.commit()
        expanded = related_emails(db, user_id, mailbox_id, emails[0])

    assert {email.graph_message_id for email in expanded} == {"a", "b", "c"}
    engine.dispose()
