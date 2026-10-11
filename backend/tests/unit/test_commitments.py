from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.api import routes_commitments
from app.config import Settings
from app.db import (
    Attachment,
    Base,
    ClaudeDailyUsage,
    Commitment,
    Email,
    EmailChunk,
    MailboxConnection,
    User,
)
from app.ingest.sync import upsert_message
from app.llm.commitments import extract_for_folder, reserve_claude_call, resolve_due_date
from app.security.rate_limit import enforce_user_rate_limit


class FakeClient:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def json_message(self, _system, _prompt, max_tokens=1200):
        self.calls += 1
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload, 1, 1


def test_due_dates_are_resolved_only_when_unambiguous():
    sent_at = datetime(2026, 3, 11, 12, tzinfo=UTC)

    assert resolve_due_date("by Friday", sent_at) == date(2026, 3, 13)
    assert resolve_due_date("tomorrow", sent_at) == date(2026, 3, 12)
    assert resolve_due_date("sometime soon", sent_at) is None
    assert resolve_due_date("by Friday", None) is None


def test_commitment_quotes_are_verified_retry_once_and_reruns_preserve_dismissal(monkeypatch):
    from app.llm import commitments as module

    monkeypatch.setattr(module, "set_user_scope", lambda _db, _user_id: None)
    monkeypatch.setattr(routes_commitments, "set_user_scope", lambda _db, _user_id: None)
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            MailboxConnection.__table__,
            Email.__table__,
            Attachment.__table__,
            EmailChunk.__table__,
            Commitment.__table__,
            ClaudeDailyUsage.__table__,
        ],
    )
    user_id, other_user_id = uuid4(), uuid4()
    mailbox_id, other_mailbox_id = uuid4(), uuid4()
    email_id, other_email_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    settings = Settings(
        _env_file=None,
        session_secret="test-session",
        token_encryption_key="test-encryption",
        claude_daily_call_cap=10,
    )

    with Session(engine) as db:
        db.add_all([
            User(id=user_id, email="first@example.test", created_at=now),
            User(id=other_user_id, email="second@example.test", created_at=now),
        ])
        db.commit()
        db.add_all([
            MailboxConnection(
                id=mailbox_id, user_id=user_id, ms_tenant_id="tenant", ms_user_id="first",
                account_email="first@example.test", granted_scopes="Mail.Read",
                encrypted_refresh_token=None, index_scope={"folders": ["sentitems"]},
                created_at=now, updated_at=now, consented_at=now,
            ),
            MailboxConnection(
                id=other_mailbox_id, user_id=other_user_id, ms_tenant_id="tenant", ms_user_id="second",
                account_email="second@example.test", granted_scopes="Mail.Read",
                encrypted_refresh_token=None, index_scope={"folders": ["sentitems"]},
                created_at=now, updated_at=now, consented_at=now,
            ),
        ])
        db.commit()
        db.add_all([
            Email(
                id=email_id, user_id=user_id, mailbox_connection_id=mailbox_id,
                graph_message_id="first-message", folder_id="folder-id", folder_alias="sentitems",
                thread_key="thread-1", subject="Follow up", body_text="I will send the revised draft by Friday.",
                received_at=now, sent_at=now, content_hash="first-hash", created_at=now, updated_at=now,
            ),
            Email(
                id=other_email_id, user_id=other_user_id, mailbox_connection_id=other_mailbox_id,
                graph_message_id="second-message", folder_id="folder-id", folder_alias="sentitems",
                thread_key="thread-2", subject="Private", body_text="I will send the private file tomorrow.",
                received_at=now, sent_at=now, content_hash="second-hash", created_at=now, updated_at=now,
            ),
        ])
        db.commit()
        quote = "I will send the revised draft by Friday."
        client = FakeClient(
            {"commitments": [{"text": "Send the revised draft", "quote": "not in the message", "due_text": "Friday"}]},
            {"commitments": [{"text": "Send the revised draft", "quote": quote, "due_text": "by Friday"}]},
        )

        result = extract_for_folder(
            db, user_id=user_id, mailbox_id=mailbox_id, folder="sentitems",
            settings=settings, client=client,
        )
        assert result == {"processed": 1, "created": 1, "stopped_reason": None}
        assert client.calls == 2
        item = db.scalar(select(Commitment).where(Commitment.user_id == user_id))
        assert item.quote == quote
        assert item.due_date == now.date() + timedelta(days=(4 - now.weekday()) % 7)
        item.status = "dismissed"
        db.get(Email, email_id).commitment_extracted_at = None
        db.commit()

        rerun_client = FakeClient({"commitments": [{"text": "Send the revised draft", "quote": quote, "due_text": "by Friday"}]})
        rerun = extract_for_folder(
            db, user_id=user_id, mailbox_id=mailbox_id, folder="sentitems",
            settings=settings, client=rerun_client,
        )
        assert rerun["created"] == 0
        assert rerun_client.calls == 1
        assert db.scalar(select(Commitment).where(Commitment.user_id == user_id)).status == "dismissed"
        assert db.scalar(select(Email.commitment_extracted_at).where(Email.id == other_email_id)) is None
        assert len(list(db.scalars(select(Commitment)))) == 1

        db.add(Commitment(
            user_id=other_user_id,
            email_id=other_email_id,
            thread_id="thread-2",
            text="Send the private file",
            quote="I will send the private file tomorrow.",
            quote_hash="other-quote-hash",
            due_date=None,
            due_text="tomorrow",
            status="open",
        ))
        db.commit()
        own_items = routes_commitments.list_commitments(
            mailbox_id=mailbox_id, status="all", user_id=user_id, db=db, settings=settings,
        )
        other_items = routes_commitments.list_commitments(
            mailbox_id=other_mailbox_id, status="all", user_id=other_user_id, db=db, settings=settings,
        )
        assert [entry["email_id"] for entry in own_items] == [str(email_id)]
        assert [entry["email_id"] for entry in other_items] == [str(other_email_id)]

        upsert_message(
            db,
            user_id=user_id,
            mailbox_id=mailbox_id,
            folder_id="sentitems",
            message={"id": "first-message", "@removed": {"reason": "deleted"}},
            target_tokens=400,
            overlap_tokens=40,
        )
        assert db.scalar(select(Commitment).where(Commitment.email_id == email_id)) is None
        assert db.scalar(select(Email.is_deleted).where(Email.id == email_id)) is True
        db.delete(db.get(Email, email_id))
        db.commit()
        assert db.scalar(select(Commitment).where(Commitment.email_id == email_id)) is None
        assert db.scalar(select(Commitment).where(Commitment.email_id == other_email_id)) is not None

    engine.dispose()


def test_daily_claude_cap_is_user_scoped(monkeypatch):
    from app.llm import commitments as module

    monkeypatch.setattr(module, "set_user_scope", lambda _db, _user_id: None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, ClaudeDailyUsage.__table__])
    user_id, other_user_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    settings = Settings(
        _env_file=None,
        session_secret="test-session",
        token_encryption_key="test-encryption",
        claude_daily_call_cap=1,
    )
    with Session(engine) as db:
        db.add_all([
            User(id=user_id, email="first@example.test", created_at=now),
            User(id=other_user_id, email="second@example.test", created_at=now),
        ])
        db.commit()

        assert reserve_claude_call(db, user_id, settings)
        assert not reserve_claude_call(db, user_id, settings)
        assert reserve_claude_call(db, other_user_id, settings)
        assert len(list(db.scalars(select(ClaudeDailyUsage)))) == 2
    engine.dispose()


def test_commitment_routes_are_rate_limited_per_user():
    user_id = uuid4()
    enforce_user_rate_limit(user_id, "commitment-test", 1)

    with pytest.raises(HTTPException) as error:
        enforce_user_rate_limit(user_id, "commitment-test", 1)

    assert error.value.status_code == 429
