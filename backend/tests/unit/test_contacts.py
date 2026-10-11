from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.api import routes_contacts
from app.config import Settings
from app.db import Base, ClaudeDailyUsage, Commitment, Email, MailboxConnection, User


class FakeBriefClient:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.last_prompt = ""

    def json_message(self, _system, prompt, max_tokens=1200):
        self.calls += 1
        self.last_prompt = prompt
        return self.payloads.pop(0), 1, 1


def _contact_database(monkeypatch):
    from app.llm import commitments

    monkeypatch.setattr(routes_contacts, "set_user_scope", lambda _db, _user_id: None)
    monkeypatch.setattr(commitments, "set_user_scope", lambda _db, _user_id: None)
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            MailboxConnection.__table__,
            Email.__table__,
            Commitment.__table__,
            ClaudeDailyUsage.__table__,
        ],
    )
    return engine


def test_contact_metadata_counts_threads_commitments_and_isolates_users(monkeypatch):
    engine = _contact_database(monkeypatch)
    owner_id, other_id = uuid4(), uuid4()
    mailbox_id, other_mailbox_id = uuid4(), uuid4()
    incoming_id, outgoing_id, private_id = uuid4(), uuid4(), uuid4()
    address = "person@example.test"
    day1 = datetime(2026, 2, 1, tzinfo=UTC)
    day2 = datetime(2026, 2, 3, tzinfo=UTC)

    with Session(engine) as db:
        db.add_all([
            User(id=owner_id, email="owner@example.test", created_at=day1),
            User(id=other_id, email="other@example.test", created_at=day1),
        ])
        db.commit()
        db.add_all([
            MailboxConnection(
                id=mailbox_id, user_id=owner_id, ms_tenant_id="tenant", ms_user_id="owner",
                account_email="owner@example.test", granted_scopes="Mail.Read", encrypted_refresh_token=None,
                index_scope={"folders": ["inbox", "sentitems"]}, created_at=day1, updated_at=day1, consented_at=day1,
            ),
            MailboxConnection(
                id=other_mailbox_id, user_id=other_id, ms_tenant_id="tenant", ms_user_id="other",
                account_email="other@example.test", granted_scopes="Mail.Read", encrypted_refresh_token=None,
                index_scope={"folders": ["inbox"]}, created_at=day1, updated_at=day1, consented_at=day1,
            ),
        ])
        db.commit()
        db.add_all([
            Email(
                id=incoming_id, user_id=owner_id, mailbox_connection_id=mailbox_id,
                graph_message_id="incoming", folder_id="inbox", thread_key="conversation-1",
                sender_address="Person@Example.Test", sender_name="Person", subject="Question",
                received_at=day1, content_hash="incoming", created_at=day1, updated_at=day1,
                body_text="We discussed the draft in detail.",
            ),
            Email(
                id=outgoing_id, user_id=owner_id, mailbox_connection_id=mailbox_id,
                graph_message_id="outgoing", folder_id="sentitems", thread_key="conversation-1",
                sender_address="owner@example.test", subject="Reply",
                recipients={"to": [{"emailAddress": {"address": "PERSON@example.test"}}]},
                received_at=day2, sent_at=day2, content_hash="outgoing", created_at=day2, updated_at=day2,
                body_text="I will send the draft tomorrow.",
            ),
            Email(
                id=private_id, user_id=other_id, mailbox_connection_id=other_mailbox_id,
                graph_message_id="private", folder_id="inbox", thread_key="private-thread",
                sender_address="person@example.test", subject="Private",
                received_at=day2, content_hash="private", created_at=day2, updated_at=day2,
            ),
        ])
        db.commit()
        db.add(Commitment(
            user_id=owner_id,
            email_id=outgoing_id,
            thread_id="conversation-1",
            text="Send the draft",
            quote="I will send the draft tomorrow.",
            quote_hash="owner-hash",
            due_text="tomorrow",
            status="open",
        ))
        db.add(Commitment(
            user_id=other_id,
            email_id=private_id,
            thread_id="private-thread",
            text="Private commitment",
            quote="Private quote",
            quote_hash="other-hash",
            status="open",
        ))
        db.commit()

        own = routes_contacts.get_contact(
            "  PERSON@EXAMPLE.TEST ", user_id=owner_id, db=db,
            settings=Settings(_env_file=None, session_secret="test", token_encryption_key="test"),
        )
        empty = routes_contacts.get_contact(
            "nobody@example.test", user_id=owner_id, db=db,
            settings=Settings(_env_file=None, session_secret="test", token_encryption_key="test"),
        )

        direct_owner_count = db.scalar(
            select(func.count())
            .select_from(Email)
            .where(
                Email.user_id == owner_id,
                Email.id.in_([incoming_id, outgoing_id]),
                ~Email.is_deleted,
            )
        )
        assert own["address"] == address
        assert own["message_count"] == direct_owner_count
        assert own["sent_count"] == 1
        assert own["received_count"] == 1
        assert own["first_date"].startswith("2026-02-01")
        assert own["last_date"].startswith("2026-02-03")
        assert own["thread_count"] == 1
        assert "email_ids" not in own["threads"][0]
        assert [item["text"] for item in own["open_commitments"]] == ["Send the draft"]
        assert empty["message_count"] == 0
        assert empty["threads"] == []
        assert empty["open_commitments"] == []

    engine.dispose()


def test_contact_brief_retries_invalid_citations_once_and_obeys_daily_cap(monkeypatch):

    engine = _contact_database(monkeypatch)
    owner_id, mailbox_id, email_id = uuid4(), uuid4(), uuid4()
    sent_at = datetime(2026, 4, 1, tzinfo=UTC)
    settings = Settings(
        _env_file=None, session_secret="test", token_encryption_key="test",
        claude_daily_call_cap=5,
    )
    exact_quote = "We will review the proposal next week."
    client = FakeBriefClient(
        {
            "sufficient_evidence": True,
            "answer": "The proposal is active.",
            "claims": [{"text": "The proposal is active.", "type": "explicit", "sources": ["C1"], "quote": "unsupported quote"}],
        },
        {
            "sufficient_evidence": True,
            "answer": "The proposal will be reviewed.",
            "claims": [{"text": "The proposal will be reviewed.", "type": "explicit", "sources": ["C1"], "quote": exact_quote}],
        },
    )
    monkeypatch.setattr(routes_contacts, "create_claude_interpret_client", lambda _settings: client)

    with Session(engine) as db:
        db.add(User(id=owner_id, email="owner@example.test", created_at=sent_at))
        db.commit()
        db.add(MailboxConnection(
            id=mailbox_id, user_id=owner_id, ms_tenant_id="tenant", ms_user_id="owner",
            account_email="owner@example.test", granted_scopes="Mail.Read", encrypted_refresh_token=None,
            index_scope={"folders": ["sentitems"]}, created_at=sent_at, updated_at=sent_at,
            consented_at=sent_at, external_ai_consent_at=sent_at,
        ))
        db.commit()
        db.add(Email(
            id=email_id, user_id=owner_id, mailbox_connection_id=mailbox_id,
            graph_message_id="message", folder_id="sentitems", folder_alias="sentitems",
            thread_key="thread-1", sender_address="person@example.test", subject="Proposal",
            received_at=sent_at, sent_at=sent_at, body_text=f"Hello. {exact_quote}",
            content_hash="content", created_at=sent_at, updated_at=sent_at,
        ))
        db.commit()

        result = routes_contacts.brief_contact(
            "PERSON@example.test", user_id=owner_id, _csrf=None, db=db, settings=settings,
        )

        assert result["sufficient_evidence"] is True
        assert result["claims"][0]["email_ids"] == [str(email_id)]
        assert result["sources"][0]["email_id"] == str(email_id)
        assert client.calls == 2
        assert "<email" in client.last_prompt
        assert "We will review the proposal next week." in client.last_prompt
        usage = db.scalar(select(ClaudeDailyUsage).where(ClaudeDailyUsage.user_id == owner_id))
        assert usage.call_count == 2

    engine.dispose()
