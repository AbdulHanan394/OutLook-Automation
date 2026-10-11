from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.api import routes_search
from app.db import Base, Email, MailboxConnection, PinnedEmail, SavedSearch, User


def test_saved_searches_and_pins_are_user_scoped_and_cascade_on_cleanup(monkeypatch):
    monkeypatch.setattr(routes_search, "set_user_scope", lambda db, user_id: None)
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            MailboxConnection.__table__,
            Email.__table__,
            SavedSearch.__table__,
            PinnedEmail.__table__,
        ],
    )
    user_id, other_user_id = uuid4(), uuid4()
    mailbox_id, other_mailbox_id = uuid4(), uuid4()
    email_id = uuid4()
    now = datetime.now(timezone.utc)

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
                encrypted_refresh_token=None, index_scope={}, created_at=now, updated_at=now,
                consented_at=now,
            ),
            MailboxConnection(
                id=other_mailbox_id, user_id=other_user_id, ms_tenant_id="tenant", ms_user_id="other",
                account_email="other@example.test", granted_scopes="Mail.Read",
                encrypted_refresh_token=None, index_scope={}, created_at=now, updated_at=now,
                consented_at=now,
            ),
        ])
        db.commit()
        db.add(
            Email(
                id=email_id, user_id=user_id, mailbox_connection_id=mailbox_id,
                graph_message_id="message-1", folder_id="inbox", subject="Budget review",
                received_at=now, content_hash="hash", created_at=now, updated_at=now,
            )
        )
        db.commit()

        saved = routes_search.save_search(
            routes_search.SaveSearchRequest(mailbox_id=mailbox_id, name="Budget", query="budget"),
            user_id=user_id,
            _=None,
            db=db,
        )
        pinned = routes_search.pin_email(
            routes_search.PinEmailRequest(mailbox_id=mailbox_id, email_id=email_id),
            user_id=user_id,
            _=None,
            db=db,
        )
        assert pinned == {"email_id": str(email_id), "pinned": True}
        assert [item["id"] for item in routes_search.list_saved_searches(mailbox_id, user_id, db)] == [saved["id"]]
        assert routes_search.list_saved_searches(other_mailbox_id, other_user_id, db) == []
        assert routes_search.list_pinned_emails(other_mailbox_id, other_user_id, db) == []

        with pytest.raises(HTTPException) as delete_error:
            routes_search.delete_saved_search(UUID(saved["id"]), other_user_id, None, db)
        assert delete_error.value.status_code == 404
        with pytest.raises(HTTPException) as pin_error:
            routes_search.pin_email(
                routes_search.PinEmailRequest(mailbox_id=other_mailbox_id, email_id=email_id),
                user_id=other_user_id,
                _=None,
                db=db,
            )
        assert pin_error.value.status_code == 404
        with pytest.raises(HTTPException) as unpin_error:
            routes_search.unpin_email(email_id, other_user_id, None, db)
        assert unpin_error.value.status_code == 404

        db.delete(db.get(Email, email_id))
        db.commit()
        assert db.scalar(select(PinnedEmail).where(PinnedEmail.email_id == email_id)) is None
        db.delete(db.get(MailboxConnection, mailbox_id))
        db.commit()
        assert db.scalar(select(SavedSearch).where(SavedSearch.id == UUID(saved["id"]))) is None

    engine.dispose()
