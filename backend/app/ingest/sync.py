"""Idempotent Graph delta ingestion. URLs returned by Graph are opaque and never logged."""
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import Attachment, Commitment, Email, EmailChunk
from app.graph.client import GraphClient, GraphError, GraphPayloadTooLarge
from app.ingest.attachments import attachment_file_type, extract_attachment_text
from app.ingest.chunk import chunk_email
from app.ingest.extract import clean_body, content_hash, normalized_content_hash
from app.mail.sanitize import sanitize_email_html

MESSAGE_SELECT = "id,internetMessageId,conversationId,parentFolderId,subject,sender,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,hasAttachments,isRead,importance,flag,categories,body,bodyPreview,webLink"
ATTACHMENT_SELECT = "id,name,contentType,size,isInline,lastModifiedDateTime,@odata.type"
logger = logging.getLogger(__name__)

def _address(value: dict[str, Any] | None) -> tuple[str | None, str | None]:
    address = (value or {}).get("emailAddress", {})
    return address.get("name"), address.get("address")

# These three are display-only state mirrored from Graph. The app is read-only:
# nothing here is ever written back to the mailbox, it is only ever observed.
IMPORTANCE_VALUES = {"low", "normal", "high"}


def _importance(message: dict[str, Any]) -> str:
    value = message.get("importance")
    return value if isinstance(value, str) and value in IMPORTANCE_VALUES else "normal"


def _is_flagged(message: dict[str, Any]) -> bool:
    flag = message.get("flag")
    return isinstance(flag, dict) and flag.get("flagStatus") == "flagged"


def _categories(message: dict[str, Any]) -> list[str]:
    raw = message.get("categories")
    if not isinstance(raw, list):
        return []
    return [str(item)[:64] for item in raw if isinstance(item, str)][:10]

def upsert_message(db: Session, *, user_id: UUID, mailbox_id: UUID, folder_id: str, message: dict[str, Any], target_tokens: int, overlap_tokens: int) -> bool:
    graph_id = message["id"]
    existing = db.scalar(select(Email).where(
        Email.user_id == user_id,
        Email.mailbox_connection_id == mailbox_id,
        Email.graph_message_id == graph_id,
    ))
    if "@removed" in message:
        if existing:
            existing.is_deleted = True
            db.execute(delete(Commitment).where(
                Commitment.user_id == user_id,
                Commitment.email_id == existing.id,
            ))
            db.execute(delete(Attachment).where(
                Attachment.user_id == user_id,
                Attachment.email_id == existing.id,
            ))
            db.execute(delete(EmailChunk).where(
                EmailChunk.user_id == user_id,
                EmailChunk.email_id == existing.id,
            ))
        return False
    body = message.get("body", {})
    body_text = clean_body(body.get("content", ""), body.get("contentType", "html"))
    # Sanitised at ingest, with remote images already stripped, so the database
    # only ever holds inert markup. The reading pane re-sanitises on render.
    body_html = sanitize_email_html(body.get("content", "")) if body.get("contentType") == "html" else None
    sender_name, sender_address = _address(message.get("sender"))
    received = datetime.fromisoformat(message["receivedDateTime"])
    sent_at = datetime.fromisoformat(message["sentDateTime"]) if message.get("sentDateTime") else None
    digest = content_hash(message.get("subject"), sender_address, message.get("receivedDateTime"), message.get("sentDateTime"), body_text)
    if existing and existing.content_hash == digest:
        existing.is_deleted = False
        existing.folder_alias = folder_id
        existing.sent_at = sent_at
        existing.has_attachments = bool(message.get("hasAttachments"))
        # Message state is refreshed even when the body is unchanged. This is the
        # backfill path for rows written before 0010 added these columns: the
        # content hash ignores read state, so without this the first sync after
        # the migration would leave every pre-existing message at its default.
        existing.is_read = bool(message.get("isRead", True))
        existing.importance = _importance(message)
        existing.is_flagged = _is_flagged(message)
        existing.categories = _categories(message)
        return False
    subject = message.get("subject")
    conversation_id = message.get("conversationId")
    values = {
        "user_id": user_id,
        "mailbox_connection_id": mailbox_id,
        "graph_message_id": graph_id,
        "internet_message_id": message.get("internetMessageId"),
        "conversation_id": conversation_id,
        "thread_key": conversation_id,
        "normalized_content_hash": normalized_content_hash(subject, body_text),
        "folder_id": message.get("parentFolderId") or folder_id,
        "folder_alias": folder_id,
        "subject": subject,
        "sender_name": sender_name,
        "sender_address": sender_address,
        "recipients": {
            "to": message.get("toRecipients", []),
            "cc": message.get("ccRecipients", []),
            "bcc": message.get("bccRecipients", []),
        },
        "received_at": received,
        "sent_at": sent_at,
        "has_attachments": bool(message.get("hasAttachments")),
        "body_text": body_text,
        "body_html": body_html,
        "body_preview": message.get("bodyPreview"),
        "web_link": message.get("webLink"),
        "content_hash": digest,
        "is_deleted": False,
        "is_read": bool(message.get("isRead", True)),
        "importance": _importance(message),
        "is_flagged": _is_flagged(message),
        "categories": _categories(message),
    }
    if existing:
        db.execute(delete(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.email_id == existing.id,
        ))
        db.execute(delete(Attachment).where(
            Attachment.user_id == user_id,
            Attachment.email_id == existing.id,
        ))
        for key, value in values.items():
            setattr(existing, key, value)
        email = existing
        db.execute(delete(EmailChunk).where(EmailChunk.email_id == email.id))
        db.flush()
    else:
        email = Email(**values)
        db.add(email)
        db.flush()
    for index, (text, chunk_hash) in enumerate(chunk_email(email.subject, email.sender_name, body_text, target_tokens, overlap_tokens)):
        db.add(EmailChunk(user_id=user_id, mailbox_connection_id=mailbox_id, email_id=email.id, chunk_index=index, chunk_text=text, content_hash=chunk_hash, source="email"))
    return True

async def index_message_attachments(
    db: Session,
    graph: GraphClient,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    email: Email,
    graph_message_id: str,
    max_bytes: int,
    target_tokens: int,
    overlap_tokens: int,
) -> None:
    if not email.has_attachments:
        attachment_ids = list(db.scalars(select(Attachment.id).where(
            Attachment.user_id == user_id,
            Attachment.mailbox_connection_id == mailbox_id,
            Attachment.email_id == email.id,
        )))
        if attachment_ids:
            db.execute(delete(EmailChunk).where(
                EmailChunk.user_id == user_id,
                EmailChunk.attachment_id.in_(attachment_ids),
            ))
            db.execute(delete(Attachment).where(
                Attachment.user_id == user_id,
                Attachment.id.in_(attachment_ids),
            ))
        return
    url: str | None = f"/me/messages/{graph_message_id}/attachments"
    params: dict[str, str] | None = {"$select": ATTACHMENT_SELECT}
    chunk_index = max(
        db.scalars(select(EmailChunk.chunk_index).where(
            EmailChunk.user_id == user_id,
            EmailChunk.email_id == email.id,
        )).all(),
        default=-1,
    ) + 1
    current_attachment_ids: set[str] = set()
    while url:
        page = await graph.get(url, params=params)
        for metadata in page.get("value", []):
            if (
                metadata.get("@odata.type") != "#microsoft.graph.fileAttachment"
                or metadata.get("isInline")
            ):
                continue
            filename = metadata.get("name") or ""
            file_type = attachment_file_type(filename)
            if not file_type:
                continue
            attachment_id = metadata.get("id")
            size_bytes = metadata.get("size")
            if not attachment_id or not isinstance(size_bytes, int) or size_bytes < 0:
                continue
            modified_value = metadata.get("lastModifiedDateTime")
            last_modified_at = (
                datetime.fromisoformat(modified_value.replace("Z", "+00:00"))
                if isinstance(modified_value, str)
                else None
            )
            current_attachment_ids.add(attachment_id)
            existing = db.scalar(select(Attachment).where(
                Attachment.user_id == user_id,
                Attachment.mailbox_connection_id == mailbox_id,
                Attachment.email_id == email.id,
                Attachment.graph_attachment_id == attachment_id,
            ))
            if (
                existing
                and existing.filename == filename
                and existing.size_bytes == size_bytes
                and existing.last_modified_at == last_modified_at
            ):
                if size_bytes > max_bytes:
                    db.execute(delete(EmailChunk).where(
                        EmailChunk.user_id == user_id,
                        EmailChunk.attachment_id == existing.id,
                    ))
                    existing.status = "skipped_oversized"
                    continue
                if existing.status in {"indexed", "empty", "skipped_encrypted", "failed_parse"}:
                    continue
            if existing:
                db.execute(delete(EmailChunk).where(
                    EmailChunk.user_id == user_id,
                    EmailChunk.attachment_id == existing.id,
                ))
                db.delete(existing)
                db.flush()
            record = Attachment(
                user_id=user_id,
                mailbox_connection_id=mailbox_id,
                email_id=email.id,
                graph_attachment_id=attachment_id,
                filename=filename,
                content_type=metadata.get("contentType"),
                last_modified_at=last_modified_at,
                size_bytes=size_bytes,
                file_type=file_type,
                status="pending",
            )
            db.add(record)
            db.flush()
            if size_bytes > max_bytes:
                record.status = "skipped_oversized"
                continue
            try:
                content = await graph.get_bytes(
                    f"/me/messages/{graph_message_id}/attachments/{attachment_id}/$value",
                    max_bytes=max_bytes,
                )
            except GraphPayloadTooLarge:
                record.status = "skipped_oversized"
                continue
            except GraphError as error:
                record.status = "failed_download"
                logger.warning("Outlook attachment download failed (%s)", type(error).__name__)
                continue
            text, status = extract_attachment_text(file_type, content)
            record.status = status
            if status != "indexed":
                continue
            chunks = chunk_email(
                f"Attachment: {filename}",
                email.sender_name,
                text,
                target_tokens,
                overlap_tokens,
            )
            for chunk_text, chunk_hash in chunks:
                db.add(EmailChunk(
                    user_id=user_id,
                    mailbox_connection_id=mailbox_id,
                    email_id=email.id,
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    content_hash=chunk_hash,
                    source="attachment",
                    attachment_id=record.id,
                ))
                chunk_index += 1
        params = None
        url = page.get("@odata.nextLink")
    stale_query = select(Attachment.id).where(
        Attachment.user_id == user_id,
        Attachment.mailbox_connection_id == mailbox_id,
        Attachment.email_id == email.id,
    )
    if current_attachment_ids:
        stale_query = stale_query.where(
            Attachment.graph_attachment_id.not_in(current_attachment_ids)
        )
    stale_ids = list(db.scalars(stale_query))
    if stale_ids:
        db.execute(delete(EmailChunk).where(
            EmailChunk.user_id == user_id,
            EmailChunk.attachment_id.in_(stale_ids),
        ))
        db.execute(delete(Attachment).where(
            Attachment.user_id == user_id,
            Attachment.id.in_(stale_ids),
        ))


async def sync_folder(
    db: Session,
    graph: GraphClient,
    *,
    user_id: UUID,
    mailbox_id: UUID,
    folder_id: str,
    lookback_iso: str,
    target_tokens: int,
    overlap_tokens: int,
    attachment_max_bytes: int = 10 * 1024 * 1024,
    resume_url: str | None = None,
) -> tuple[str | None, int]:
    url = resume_url or f"/me/mailFolders/{folder_id}/messages/delta"
    params = None if resume_url else {"$select": MESSAGE_SELECT, "$filter": f"receivedDateTime ge {lookback_iso}", "$orderby": "receivedDateTime desc"}
    indexed = 0
    while True:
        page = await graph.get(url, params=params)
        for message in page.get("value", []):
            indexed += int(upsert_message(db, user_id=user_id, mailbox_id=mailbox_id, folder_id=folder_id, message=message, target_tokens=target_tokens, overlap_tokens=overlap_tokens))
            if "@removed" not in message:
                email = db.scalar(select(Email).where(
                    Email.user_id == user_id,
                    Email.mailbox_connection_id == mailbox_id,
                    Email.graph_message_id == message["id"],
                ))
                if email:
                    await index_message_attachments(
                        db,
                        graph,
                        user_id=user_id,
                        mailbox_id=mailbox_id,
                        email=email,
                        graph_message_id=message["id"],
                        max_bytes=attachment_max_bytes,
                        target_tokens=target_tokens,
                        overlap_tokens=overlap_tokens,
                    )
        db.commit()
        if page.get("@odata.nextLink"):
            url, params = page["@odata.nextLink"], None
            continue
        return page.get("@odata.deltaLink"), indexed
