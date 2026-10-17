from collections.abc import Generator
from datetime import UTC, date, datetime
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
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import UserDefinedType

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class VectorType(UserDefinedType):
    """PostgreSQL pgvector column type without requiring a separate ORM package."""

    cache_ok = True

    def get_col_spec(self, **kwargs) -> str:
        return "vector"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.utcnow)


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
    index_scope: Mapped[dict] = mapped_column(JSONB)
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
    recipients: Mapped[dict | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commitment_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str | None] = mapped_column(Text)
    web_link: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


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
    query_json: Mapped[dict] = mapped_column(JSONB)
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
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
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
    summary_json: Mapped[dict] = mapped_column(JSONB)
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
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
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
