from fastapi import Depends, Header, HTTPException

from app.db import SessionRecord
from app.security.sessions import require_session


def require_csrf(
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    session: SessionRecord = Depends(require_session),
) -> SessionRecord:
    if not csrf_token or csrf_token != session.csrf_token:
        raise HTTPException(403, "CSRF validation failed")
    return session
