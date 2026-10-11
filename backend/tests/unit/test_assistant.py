from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.api import routes_assistant
from app.api.routes_assistant import AskRequest
from app.config import Settings
from app.db import Base, Email, MailboxConnection, User


class FakeAnswerClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.prompts: list[str] = []

    def json_message(self, _system: str, prompt: str, max_tokens: int = 1200):
        self.calls += 1
        self.prompts.append(prompt)
        return self.payload, 1, 1


def test_recent_summary_uses_latest_user_mail_without_keyword_hits(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, MailboxConnection.__table__, Email.__table__],
    )
    user_id, other_user_id = uuid4(), uuid4()
    mailbox_id, other_mailbox_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    client = FakeAnswerClient({
        "sufficient_evidence": True,
        "answer": "Alex shared a status update about the design.",
        "claims": [{
            "text": "Alex shared a status update about the design.",
            "type": "explicit",
            "sources": ["E1"],
            "quote": "The design review is complete.",
        }],
    })
    monkeypatch.setattr(routes_assistant, "create_answer_client", lambda _settings: client)
    settings = Settings(
        _env_file=None,
        session_secret="test-session",
        token_encryption_key="test-encryption",
        llm_provider="groq",
        rag_max_excerpts=5,
    )

    with Session(engine) as db:
        db.add_all([
            User(id=user_id, email="owner@example.test", created_at=now),
            User(id=other_user_id, email="other@example.test", created_at=now),
        ])
        db.commit()
        db.add_all([
            MailboxConnection(
                id=mailbox_id, user_id=user_id, ms_tenant_id="tenant", ms_user_id="owner",
                account_email="owner@example.test", granted_scopes="Mail.Read",
                index_scope={"folders": ["inbox"]}, external_ai_consent_at=now,
                created_at=now, updated_at=now, consented_at=now,
            ),
            MailboxConnection(
                id=other_mailbox_id, user_id=other_user_id, ms_tenant_id="tenant", ms_user_id="other",
                account_email="other@example.test", granted_scopes="Mail.Read",
                index_scope={"folders": ["inbox"]}, external_ai_consent_at=now,
                created_at=now, updated_at=now, consented_at=now,
            ),
        ])
        db.commit()
        first_id, thread_reply_id = uuid4(), uuid4()
        db.add_all([
            Email(
                id=first_id, user_id=user_id, mailbox_connection_id=mailbox_id,
                graph_message_id="recent-1", folder_id="inbox", thread_key="design-thread",
                subject="Design update", sender_name="Alex", received_at=now,
                body_text="The design review is complete.", body_preview="Design review complete",
                content_hash="recent-1", created_at=now, updated_at=now,
            ),
            Email(
                id=thread_reply_id, user_id=user_id, mailbox_connection_id=mailbox_id,
                graph_message_id="recent-2", folder_id="inbox", thread_key="design-thread",
                subject="Design follow-up", sender_name="Alex", received_at=now + timedelta(minutes=1),
                body_text="The design review is complete.", body_preview="Design review complete",
                content_hash="recent-2", created_at=now, updated_at=now,
            ),
            Email(
                id=uuid4(), user_id=other_user_id, mailbox_connection_id=other_mailbox_id,
                graph_message_id="private", folder_id="inbox", thread_key="private-thread",
                subject="Private update", sender_name="Other", received_at=now,
                body_text="The private status is confidential.", content_hash="private",
                created_at=now, updated_at=now,
            ),
        ])
        db.commit()

        result = routes_assistant.ask(
            AskRequest(mailbox_id=mailbox_id, question="Give me a gentle summary of my recent conversations"),
            user_id=user_id,
            csrf_session=None,
            db=db,
            settings=settings,
        )

    assert client.calls == 1
    assert "The design review is complete." in client.prompts[0]
    assert "Private update" not in client.prompts[0]
    assert result["sufficient_evidence"] is True
    assert [email["email_id"] for email in result["closest_emails"]] == [str(thread_reply_id)]
    engine.dispose()
