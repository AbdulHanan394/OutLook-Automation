from uuid import UUID
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.embeddings.voyage import VoyageClient

async def vector_search(db: Session, client: VoyageClient, *, user_id: UUID, mailbox_id: UUID, query: str, limit: int) -> list[str]:
    vector = (await client.embed([query], "query")).vectors[0]
    encoded = "[" + ",".join(str(x) for x in vector) + "]"
    # Model guard is mandatory: rows from another model are invisible to this search.
    db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    result = db.execute(text("""
        SELECT email_id FROM email_chunks
        WHERE user_id = :user_id AND mailbox_connection_id = :mailbox_id
          AND embedding IS NOT NULL AND embedding_model = :model AND embedding_dim = :dimension
        ORDER BY embedding <=> CAST(:query_vector AS vector) LIMIT :limit
    """), {"user_id": str(user_id), "mailbox_id": str(mailbox_id), "model": client.model, "dimension": client.dimension, "query_vector": encoded, "limit": limit})
    return [str(row[0]) for row in result]
