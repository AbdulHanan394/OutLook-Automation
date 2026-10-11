import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import SessionRecord, get_db

COOKIE_NAME = "email_finder_session"


def new_session(db: Session, user_id: UUID) -> SessionRecord:
    record = SessionRecord(id=secrets.token_urlsafe(32), user_id=user_id, csrf_token=secrets.token_urlsafe(32), expires_at=datetime.now(UTC) + timedelta(days=7))
    db.add(record)
    return record


def require_session(request: Request, db: Session = Depends(get_db)) -> SessionRecord:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Authentication required")
    record = db.get(SessionRecord, token)
    if not record or record.expires_at < datetime.now(UTC):
        raise HTTPException(401, "Authentication required")
    return record


def require_user(session: SessionRecord = Depends(require_session)) -> UUID:
    return session.user_id
