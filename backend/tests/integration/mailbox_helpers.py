"""Helpers for building mailbox rows in integration tests.

Deliberately free of pytest fixtures: pytest only auto-registers fixtures it
discovers in ``conftest.py`` or in collected test modules, so this module holds
plain builders and ``conftest.py`` wires them up.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db import Email, MailboxConnection, SessionRecord, User

ALEMBIC_INI = "alembic.ini"


def make_user(db: Session, email: str) -> User:
    user = User(email=email)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_mailbox(
    db: Session,
    user: User,
    *,
    folders: list[str] | None = None,
    ai_consent: bool = False,
) -> MailboxConnection:
    mailbox = MailboxConnection(
        user_id=user.id,
        ms_tenant_id="organizations",
        ms_user_id=f"ms-{user.id}",
        account_email=user.email,
        granted_scopes="Mail.Read",
        status="active",
        index_scope={"folders": folders or ["inbox", "sentitems"], "lookback_days": 365},
        external_ai_consent_at=datetime.now(UTC) if ai_consent else None,
    )
    db.add(mailbox)
    db.commit()
    db.refresh(mailbox)
    return mailbox


def make_email(
    db: Session,
    user: User,
    mailbox: MailboxConnection,
    *,
    graph_id: str,
    folder: str = "inbox",
    subject: str = "Subject",
    sender_name: str = "Sender",
    sender_address: str = "sender@example.test",
    received_at: datetime | None = None,
    body_text: str = "Body text.",
    body_html: str | None = None,
    is_read: bool = True,
    importance: str = "normal",
    is_flagged: bool = False,
    has_attachments: bool = False,
    conversation_id: str | None = None,
    categories: list[str] | None = None,
    web_link: str | None = None,
    to: list[dict] | None = None,
    cc: list[dict] | None = None,
) -> Email:
    stamp = received_at or datetime.now(UTC)
    email = Email(
        user_id=user.id,
        mailbox_connection_id=mailbox.id,
        graph_message_id=graph_id,
        conversation_id=conversation_id,
        thread_key=conversation_id,
        folder_id=folder,
        folder_alias=folder,
        subject=subject,
        sender_name=sender_name,
        sender_address=sender_address,
        recipients={"to": to or [], "cc": cc or [], "bcc": []},
        received_at=stamp,
        sent_at=stamp,
        body_text=body_text,
        body_html=body_html,
        body_preview=body_text[:120],
        has_attachments=has_attachments,
        is_read=is_read,
        importance=importance,
        is_flagged=is_flagged,
        categories=categories or [],
        content_hash=uuid.uuid4().hex * 2,
        normalized_content_hash=uuid.uuid4().hex * 2,
        web_link=web_link,
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


def bulk_seed(
    db: Session,
    user: User,
    mailbox: MailboxConnection,
    count: int,
    *,
    folder: str = "inbox",
) -> None:
    """Insert ``count`` messages with deterministic, strictly increasing stamps.

    One transaction for the whole batch: committing per row makes the 5,000-row
    pagination test take minutes instead of seconds.
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Email] = []
    for index in range(count):
        stamp = base + timedelta(minutes=index)
        rows.append(
            Email(
                user_id=user.id,
                mailbox_connection_id=mailbox.id,
                graph_message_id=f"bulk-{folder}-{index}",
                folder_id=folder,
                folder_alias=folder,
                subject=f"Message {index}",
                sender_name="Sender",
                sender_address="sender@example.test",
                recipients={"to": [], "cc": [], "bcc": []},
                received_at=stamp,
                sent_at=stamp,
                body_text="Routine message.",
                body_preview="Routine message.",
                is_read=index % 2 == 0,
                importance="normal",
                is_flagged=False,
                categories=[],
                content_hash=uuid.uuid4().hex * 2,
                normalized_content_hash=uuid.uuid4().hex * 2,
            )
        )
    db.add_all(rows)
    db.commit()


def sign_in(db: Session, client, user: User) -> None:
    """Attach a session cookie for ``user`` without going through OAuth."""
    token = f"test-{uuid.uuid4().hex}"
    db.add(
        SessionRecord(
            id=token,
            user_id=user.id,
            csrf_token="test-csrf-token",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.commit()
    client.cookies.set("email_finder_session", token)
    client.cookies.set("csrf_token", "test-csrf-token")