from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.turn import answer_question
from app.config import Settings, get_settings
from app.db import MailboxConnection, SessionRecord, get_db
from app.llm.client import JsonMessageClient
from app.llm.factory import create_json_client
from app.security.csrf import require_csrf
from app.security.sessions import require_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AskRequest(BaseModel):
    mailbox_id: UUID
    question: str = Field(min_length=3, max_length=1000)


def create_answer_client(settings: Settings) -> JsonMessageClient:
    return create_json_client(settings, "answer")


@router.post("/ask")
def ask(
    request: AskRequest,
    user_id: UUID = Depends(require_user),
    csrf_session: SessionRecord = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    mailbox = db.scalar(select(MailboxConnection).where(MailboxConnection.id == request.mailbox_id, MailboxConnection.user_id == user_id))
    if not mailbox: raise HTTPException(404, "Mailbox not found")
    if not mailbox.external_ai_consent_at:
        raise HTTPException(409, "AI processing consent is required")
    # Retrieval, the model call and quote verification live in app.assistant.turn
    # so the conversational endpoint answers a turn the same way.
    return answer_question(
        db,
        user_id=user_id,
        mailbox_id=request.mailbox_id,
        question=request.question,
        settings=settings,
        client=create_answer_client(settings),
    )