import logging
from typing import Annotated, Any, Literal
from uuid import UUID

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Commitment, Email, MailboxConnection, SessionRecord, get_db, set_user_scope
from app.llm.commitments import extract_for_folder
from app.security.csrf import require_csrf
from app.security.rate_limit import enforce_user_rate_limit
from app.security.sessions import require_user

router = APIRouter(prefix="/api/commitments", tags=["commitments"])
logger = logging.getLogger(__name__)


class ExtractRequest(BaseModel):
    mailbox_id: UUID
    folder: str | None = Field(default=None, min_length=1, max_length=200)


class StatusRequest(BaseModel):
    status: Literal["done", "dismissed", "open"]


@router.get("")
def list_commitments(
    mailbox_id: UUID,
    user_id: Annotated[UUID, Depends(require_user)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    status: Annotated[Literal["open", "done", "dismissed", "all"], Query()] = "open",
) -> list[dict[str, Any]]:
    enforce_user_rate_limit(user_id, "commitments", settings.rate_limit_commitments_per_min)
    set_user_scope(db, user_id)
    mailbox = db.scalar(select(MailboxConnection).where(
        MailboxConnection.id == mailbox_id,
        MailboxConnection.user_id == user_id,
    ))
    if not mailbox:
        raise HTTPException(404, "Mailbox not found")
    statement = (
        select(Commitment, Email)
        .join(Email, Email.id == Commitment.email_id)
        .where(
            Commitment.user_id == user_id,
            Commitment.email_id == Email.id,
            Email.user_id == user_id,
            Email.mailbox_connection_id == mailbox_id,
            ~Email.is_deleted,
        )
    )
    if status != "all":
        statement = statement.where(Commitment.status == status)
    rows = db.execute(statement.order_by(Commitment.created_at.desc())).all()
    return [
        {
            "id": str(item.id),
            "email_id": str(email.id),
            "thread_id": item.thread_id,
            "text": item.text,
            "quote": item.quote,
            "due_date": item.due_date.isoformat() if item.due_date else None,
            "due_text": item.due_text,
            "status": item.status,
            "subject": email.subject or "(no subject)",
            "sender": " ".join(filter(None, [email.sender_name, email.sender_address])) or "(unknown)",
            "sent_at": (email.sent_at or email.received_at).isoformat(),
            "outlook_url": email.web_link,
        }
        for item, email in rows
    ]


@router.post("/extract")
def extract_commitments(
    request: ExtractRequest,
    user_id: Annotated[UUID, Depends(require_user)],
    _csrf: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    enforce_user_rate_limit(user_id, "commitments", settings.rate_limit_commitments_per_min)
    set_user_scope(db, user_id)
    mailbox = db.scalar(select(MailboxConnection).where(
        MailboxConnection.id == request.mailbox_id,
        MailboxConnection.user_id == user_id,
    ))
    if not mailbox:
        raise HTTPException(404, "Mailbox not found")
    if not mailbox.external_ai_consent_at:
        raise HTTPException(409, "AI processing consent is required")
    folder = request.folder or (settings.commitment_folder_names[0] if settings.commitment_folder_names else "sentitems")
    allowed_folders = set(mailbox.index_scope.get("folders", []))
    if folder not in allowed_folders:
        raise HTTPException(400, "Choose a folder that is enabled for this mailbox.")
    try:
        result = extract_for_folder(
            db,
            user_id=user_id,
            mailbox_id=mailbox.id,
            folder=folder,
            settings=settings,
        )
    except HTTPException:
        raise
    except (anthropic.APIError, httpx.HTTPError):
        logger.warning("Commitment extraction request failed")
        raise HTTPException(503, "Commitments could not be extracted right now. Please try again later.") from None
    return result


@router.post("/{commitment_id}/status")
def update_commitment_status(
    commitment_id: UUID,
    request: StatusRequest,
    mailbox_id: UUID,
    user_id: Annotated[UUID, Depends(require_user)],
    _csrf: Annotated[SessionRecord, Depends(require_csrf)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    enforce_user_rate_limit(user_id, "commitments", settings.rate_limit_commitments_per_min)
    set_user_scope(db, user_id)
    item = db.scalar(
        select(Commitment)
        .join(Email, Email.id == Commitment.email_id)
        .where(
            Commitment.id == commitment_id,
            Commitment.user_id == user_id,
            Email.user_id == user_id,
            Email.mailbox_connection_id == mailbox_id,
            ~Email.is_deleted,
        )
    )
    if not item:
        raise HTTPException(404, "Commitment not found")
    item.status = request.status
    db.commit()
    return {"id": str(item.id), "status": item.status}
