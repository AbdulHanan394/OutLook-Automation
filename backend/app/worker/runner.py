import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import anthropic
import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.microsoft import refresh_access_token
from app.config import get_settings
from app.db import Email, EmailChunk, IndexingJob, MailboxConnection, SessionLocal, set_user_scope
from app.graph.client import GraphClient
from app.ingest.sync import sync_folder
from app.llm.commitments import extract_for_folder
from app.security.crypto import TokenCipher

logger = logging.getLogger(__name__)


def claim_one() -> IndexingJob | None:
    with SessionLocal.begin() as db:
        job = db.scalar(select(IndexingJob).where(IndexingJob.status == "queued", IndexingJob.run_after <= datetime.now(UTC)).with_for_update(skip_locked=True).limit(1))
        if job:
            set_user_scope(db, job.user_id)
            job.status, job.attempts = "running", job.attempts + 1
        return job


def run_sync_job(db, job: IndexingJob) -> int:
    settings = get_settings()
    mailbox = db.get(MailboxConnection, job.mailbox_connection_id)
    if not mailbox or not mailbox.encrypted_refresh_token:
        raise ValueError("Mailbox connection has no refresh token")
    key = settings.token_encryption_key.get_secret_value()
    refresh_token = TokenCipher("v1", [key]).decrypt(mailbox.encrypted_refresh_token)
    tokens = refresh_access_token(settings, refresh_token)
    if rotated_token := tokens.get("refresh_token"):
        mailbox.encrypted_refresh_token = TokenCipher("v1", [key]).encrypt(rotated_token)
    graph = GraphClient(tokens["access_token"])
    lookback = (datetime.now(UTC) - timedelta(days=settings.index_lookback_days)).isoformat()
    total = 0
    folders = job.payload.get("folders", settings.folders)
    job.progress_total = None
    db.commit()
    for folder in folders:
        _, indexed = asyncio.run(sync_folder(
            db, graph, user_id=job.user_id, mailbox_id=job.mailbox_connection_id,
            folder_id=folder, lookback_iso=lookback,
            target_tokens=settings.chunk_target_tokens, overlap_tokens=settings.chunk_overlap_tokens,
            attachment_max_bytes=settings.attachment_max_mb * 1024 * 1024,
        ))
        total += indexed
        job.progress_done = total
        db.commit()
    if mailbox.external_ai_consent_at:
        for folder in settings.commitment_folder_names:
            if folder not in folders:
                continue
            try:
                result = extract_for_folder(
                    db,
                    user_id=job.user_id,
                    mailbox_id=job.mailbox_connection_id,
                    folder=folder,
                    settings=settings,
                )
                if result["stopped_reason"]:
                    logger.warning("Automatic commitment extraction stopped: %s", result["stopped_reason"])
            except (HTTPException, anthropic.APIError, httpx.HTTPError, SQLAlchemyError) as error:
                logger.warning("Automatic commitment extraction failed: %s", type(error).__name__)
    return total


def mark_failed(job_id, error: Exception) -> None:
    with SessionLocal.begin() as db:
        job = db.get(IndexingJob, job_id)
        if job:
            job.status = "failed"
            job.error_code = type(error).__name__
            job.finished_at = datetime.now(UTC)


def enqueue_due_syncs() -> None:
    """Queue periodic mailbox syncs without overlapping work for a mailbox."""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.sync_poll_interval_minutes)
    with SessionLocal.begin() as db:
        mailboxes = list(db.scalars(select(MailboxConnection).where(
            MailboxConnection.status == "active",
            MailboxConnection.encrypted_refresh_token.is_not(None),
        )))
        for mailbox in mailboxes:
            latest = db.scalar(select(IndexingJob).where(
                IndexingJob.mailbox_connection_id == mailbox.id,
            ).order_by(IndexingJob.created_at.desc()).limit(1))
            if latest and latest.status in {"queued", "running"}:
                continue
            if latest and latest.created_at >= cutoff:
                continue
            db.add(IndexingJob(
                user_id=mailbox.user_id,
                mailbox_connection_id=mailbox.id,
                type="sync_folder",
                payload={"folders": mailbox.index_scope.get("folders", settings.folders)},
            ))


def run_forever() -> None:
    next_poll_at = 0.0
    while True:
        if time.monotonic() >= next_poll_at:
            enqueue_due_syncs()
            next_poll_at = time.monotonic() + get_settings().sync_poll_interval_minutes * 60
        job = claim_one()
        if not job:
            time.sleep(2)
            continue
        try:
            with SessionLocal() as db:
                current = db.get(IndexingJob, job.id)
                if current:
                    set_user_scope(db, current.user_id)
                    if current.type == "delete_data":
                        db.execute(delete(EmailChunk).where(EmailChunk.mailbox_connection_id == current.mailbox_connection_id))
                        db.execute(delete(Email).where(Email.mailbox_connection_id == current.mailbox_connection_id))
                    elif current.type == "disconnect":
                        mailbox = db.get(MailboxConnection, current.mailbox_connection_id)
                        if mailbox:
                            mailbox.encrypted_refresh_token = None
                            mailbox.status = "disconnected"
                        if current.payload.get("delete_data"):
                            db.execute(delete(EmailChunk).where(EmailChunk.mailbox_connection_id == current.mailbox_connection_id))
                            db.execute(delete(Email).where(Email.mailbox_connection_id == current.mailbox_connection_id))
                    elif current.type == "sync_folder":
                        run_sync_job(db, current)
                    current.status, current.finished_at = "succeeded", datetime.now(UTC)
                    current.error_code = None
                    db.commit()
        except Exception as error:
            mark_failed(job.id, error)


if __name__ == "__main__":
    run_forever()
