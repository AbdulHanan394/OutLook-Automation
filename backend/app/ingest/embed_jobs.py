from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import EmailChunk
from app.embeddings.voyage import VoyageClient

async def embed_pending(db: Session, client: VoyageClient, *, user_id: UUID, mailbox_id: UUID, batch_size: int = 128) -> int:
    rows = list(db.scalars(select(EmailChunk).where(EmailChunk.user_id == user_id, EmailChunk.mailbox_connection_id == mailbox_id, EmailChunk.embedding == None).order_by(EmailChunk.id).limit(batch_size))) # noqa: E711
    if not rows: return 0
    result = await client.embed([row.chunk_text for row in rows], "document")
    for row, vector in zip(rows, result.vectors, strict=True):
        row.embedding = "[" + ",".join(str(x) for x in vector) + "]"; row.embedding_model = client.model; row.embedding_dim = client.dimension; row.embedded_at = datetime.now(UTC)
    db.commit()
    return len(rows)
