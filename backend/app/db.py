from collections.abc import Generator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import UserDefinedType

from app.config import get_settings


def _now_utc() -> datetime:
    """Timezone-aware ``onupdate`` value.

    ``datetime.utcnow`` is deprecated and returns a naive value, which would be
    reinterpreted in the session timezone by a ``DateTime(timezone=True)``
    column.
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class VectorType(UserDefinedType[Any]):
    """PostgreSQL pgvector column type without requiring a separate ORM package."""

    cache_ok = True

    def get_col_spec(self, **kwargs: Any) -> str:
        return "vector"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), onupdate=_now_utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(Text, unique=True)
    ms_object_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    verifier: Mapped[str] = mapped_column(String(256))
    nonce: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class MailboxConnection(Base, Timestamped):
    __tablename__ = "mailbox_connections"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ms_tenant_id: Mapped[str] = mapped_column(Text)
    ms_user_id: Mapped[str] = mapped_column(Text)
    account_email: Mapped[str] = mapped_column(Text)
    granted_scopes: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    index_scope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    external_ai_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Email(Base, Timestamped):
    __tablename__ = "emails"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    graph_message_id: Mapped[str] = mapped_column(Text)
    internet_message_id: Mapped[str | None] = mapped_column(Text)
    conversation_id: Mapped[str | None] = mapped_column(Text)
    thread_key: Mapped[str | None] = mapped_column(Text)
    normalized_content_hash: Mapped[str | None] = mapped_column(String(64))
    folder_id: Mapped[str] = mapped_column(Text)
    folder_alias: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(Text)
    sender_address: Mapped[str | None] = mapped_column(Text)
    recipients: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commitment_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str | None] = mapped_column(Text)
    web_link: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=True)
    importance: Mapped[str] = mapped_column(String(16), default="normal")
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    categories: Mapped[list[str] | None] = mapped_column(JSONB, default=list)


# Reference to the stored generated tsvector column created in migration 0001
# (``setweight(subject, 'A') || setweight(body_text, 'B')``, GIN-indexed as
# ``emails_tsv_idx``). Deliberately kept out of the ORM mapping: the column is
# database-generated and read-only, and mapping it would make SQLAlchemy emit
# PostgreSQL-only DDL that the SQLite-backed unit tests cannot execute.
EMAIL_SEARCH_VECTOR = literal_column(f"{Email.__tablename__}.search_tsv", type_=TSVECTOR)


class SearchSession(Base):
    __tablename__ = "search_sessions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_email_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    refinement_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SearchFeedback(Base):
    __tablename__ = "search_feedback"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[UUID] = mapped_column(ForeignKey("search_sessions.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[UUID] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    query: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()")
    )


class PinnedEmail(Base):
    __tablename__ = "pinned_emails"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[UUID] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()")
    )


class SearchResultEvent(Base):
    __tablename__ = "search_result_events"
    __table_args__ = (UniqueConstraint("search_id", "email_id", name="search_result_events_search_email_unique"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[UUID] = mapped_column(ForeignKey("search_sessions.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[UUID] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    signal: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ThreadSummaryCache(Base):
    __tablename__ = "thread_summary_cache"
    __table_args__ = (
        UniqueConstraint("user_id", "mailbox_connection_id", "group_key", name="thread_summary_owner_group_unique"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    group_key: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Commitment(Base):
    __tablename__ = "commitments"
    __table_args__ = (
        UniqueConstraint("email_id", "quote_hash", name="commitments_email_quote_hash_unique"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[UUID] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("CURRENT_TIMESTAMP")
    )
    text: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    quote_hash: Mapped[str] = mapped_column(String(64))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")


class ClaudeDailyUsage(Base):
    __tablename__ = "claude_daily_usage"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    usage_date: Mapped[date] = mapped_column(primary_key=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("CURRENT_TIMESTAMP"))


class AskHistory(Base):
    """Recent Ask bar submissions, so the bar can offer what was asked before.

    Per-user only, and read directly with an explicit ``user_id`` predicate as
    well as through the row-level-security policy, matching every other table.
    """

    __tablename__ = "ask_history"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(8), default="auto")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()")
    )


class Conversation(Base):
    """One chat. Titles are generated after the first exchange and user-editable.

    Carries ``mailbox_connection_id`` so that disconnecting or deleting a
    mailbox can take its derived history with it -- chat content is derived from
    private email and must not outlive it.
    """

    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()")
    )


class ChatMessage(Base):
    """One turn. ``text`` is what the reader sees and what history search matches.

    Email content is never stored here: a citation keeps an email id and the
    quoted passage, and the message body is re-read from ``emails`` when needed,
    so a deleted email simply stops resolving.
    """

    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    citations_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    tool_trace_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        # func.now(), not text("now()"): the `text` column declared above
        # shadows the module-level text() inside this class body.
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


# Same reasoning as EMAIL_SEARCH_VECTOR: ``messages.search_tsv`` is generated
# from the message text and GIN-indexed, so history search can match a word from
# any turn rather than only from the title. Declared here because it names the
# table of the class above.
MESSAGE_SEARCH_VECTOR = literal_column(f"{ChatMessage.__tablename__}.search_tsv", type_=TSVECTOR)


class ConversationContext(Base):
    """The rolling state of a chat: what is under discussion and what was folded away.

    ``working_set_json`` holds email ids only. Bodies are re-read on demand so
    the set cannot go stale and cannot leak mail into the history row.
    """

    __tablename__ = "conversation_context"
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    working_set_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_upto_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=text("now()")
    )


class MemoryNote(Base):
    """A stated preference the reader asked the assistant to remember.

    Only ever written from something the reader said outright. Nothing is
    inferred from how they browse or what they open, because an inferred note
    is one they cannot see coming and cannot argue with. Every note is listed in
    the settings the reader controls, and deleting one removes it permanently.
    """

    __tablename__ = "memory_notes"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), default="preference")
    note_text: Mapped[str] = mapped_column(String(400))
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (UniqueConstraint("email_id", "graph_attachment_id", name="attachments_email_graph_id_unique"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[UUID] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    graph_attachment_id: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size_bytes: Mapped[int] = mapped_column(Integer)
    file_type: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32))


class EmailChunk(Base):
    __tablename__ = "email_chunks"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[UUID] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="email", server_default="email")
    attachment_id: Mapped[UUID | None] = mapped_column(ForeignKey("attachments.id", ondelete="CASCADE"), nullable=True)
    embedding: Mapped[str | None] = mapped_column(VectorType(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndexingJob(Base):
    __tablename__ = "indexing_jobs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(index=True)
    mailbox_connection_id: Mapped[UUID] = mapped_column(ForeignKey("mailbox_connections.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db


def set_user_scope(db: Session, user_id: UUID) -> None:
    db.execute(text("SELECT set_config('app.user_id', :value, true)"), {"value": str(user_id)})
