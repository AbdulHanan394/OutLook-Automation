import asyncio
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from pypdf import PdfWriter
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db import Attachment, Base, Commitment, Email, EmailChunk
from app.ingest.sync import index_message_attachments, upsert_message
from app.retrieval.parse import parse_query
from app.retrieval.pipeline import keyword_search


class AttachmentGraph:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.downloaded: list[str] = []
        file_type = "#microsoft.graph.fileAttachment"
        self.attachments = [
            {
                "id": "text-id",
                "name": "forecast.pdf",
                "contentType": "application/pdf",
                "size": len(payloads["text-id"]),
                "isInline": False,
                "@odata.type": file_type,
            },
            {
                "id": "encrypted-id",
                "name": "locked.pdf",
                "contentType": "application/pdf",
                "size": len(payloads["encrypted-id"]),
                "isInline": False,
                "@odata.type": file_type,
            },
            {
                "id": "large-id",
                "name": "large.docx",
                "contentType": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "size": 8192,
                "isInline": False,
                "@odata.type": file_type,
            },
        ]

    async def get(self, _url: str, *, params: dict[str, str] | None = None) -> dict:
        return {"value": self.attachments}

    async def get_bytes(self, url: str, *, max_bytes: int) -> bytes:
        attachment_id = url.split("/")[-2]
        self.downloaded.append(attachment_id)
        return self.payloads[attachment_id]


def text_pdf(text: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode() + item + b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(offsets)}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010} 00000 n \n".encode())
    content.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(content)


def test_attachment_sync_indexes_text_and_skips_encrypted_and_oversized_files():
    forecast_pdf = text_pdf("Forecast for blue widget: 48 units.")
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("password")
    pdf_data = BytesIO()
    writer.write(pdf_data)

    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.create_function(
        "gen_random_uuid", 0, lambda: uuid4().hex,
    ))
    Base.metadata.create_all(
        engine,
        tables=[Email.__table__, Attachment.__table__, EmailChunk.__table__, Commitment.__table__],
    )
    user_id, mailbox_id = uuid4(), uuid4()
    email = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="message-id", folder_id="inbox", subject="Forecast",
        received_at=datetime(2025, 1, 1, tzinfo=UTC), has_attachments=True,
        body_text="See attached files.", body_preview="See attached files.",
        content_hash="message-hash", is_deleted=False,
        created_at=datetime(2025, 1, 1, tzinfo=UTC), updated_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    graph = AttachmentGraph({
        "text-id": forecast_pdf,
        "encrypted-id": pdf_data.getvalue(),
        "large-id": b"x" * 8192,
    })
    with Session(engine) as db:
        db.add(email)
        db.commit()
        asyncio.run(index_message_attachments(
            db, graph, user_id=user_id, mailbox_id=mailbox_id, email=email,
            graph_message_id="message-id", max_bytes=4096,
            target_tokens=400, overlap_tokens=40,
        ))
        db.commit()

        attachments = {item.filename: item for item in db.scalars(select(Attachment))}
        assert attachments["forecast.pdf"].status == "indexed"
        assert attachments["locked.pdf"].status == "skipped_encrypted"
        assert attachments["large.docx"].status == "skipped_oversized"
        assert graph.downloaded == ["text-id", "encrypted-id"]
        chunks = list(db.scalars(select(EmailChunk).where(EmailChunk.source == "attachment")))
        assert len(chunks) == 1
        assert "48 units" in chunks[0].chunk_text
        assert chunks[0].attachment_id == attachments["forecast.pdf"].id
        _, results, reasons = keyword_search(
            db, user_id, mailbox_id, parse_query('"blue widget"'), 10, 0.01, 0.015,
        )
        assert [result.id for result in results] == [email.id]
        assert any("forecast.pdf" in reason.detail for reason in reasons[email.id])

        graph.attachments = graph.attachments[1:]
        asyncio.run(index_message_attachments(
            db, graph, user_id=user_id, mailbox_id=mailbox_id, email=email,
            graph_message_id="message-id", max_bytes=4096,
            target_tokens=400, overlap_tokens=40,
        ))
        db.flush()
        assert db.scalar(select(Attachment).where(Attachment.filename == "forecast.pdf")) is None
        assert list(db.scalars(select(EmailChunk).where(EmailChunk.source == "attachment"))) == []

        email.has_attachments = False
        asyncio.run(index_message_attachments(
            db, graph, user_id=user_id, mailbox_id=mailbox_id, email=email,
            graph_message_id="message-id", max_bytes=4096,
            target_tokens=400, overlap_tokens=40,
        ))
        assert list(db.scalars(select(Attachment))) == []

        upsert_message(
            db, user_id=user_id, mailbox_id=mailbox_id, folder_id="inbox",
            message={"id": "message-id", "@removed": {"reason": "deleted"}},
            target_tokens=400, overlap_tokens=40,
        )
        assert list(db.scalars(select(Attachment))) == []
        assert list(db.scalars(select(EmailChunk))) == []
    engine.dispose()
