from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth.microsoft import (
    authorization_url,
    create_state,
    exchange_code,
    microsoft_profile,
    profile_email,
)
from app.config import Settings, get_settings
from app.db import OAuthState, SessionRecord, User, get_db
from app.security.csrf import require_csrf
from app.security.sessions import COOKIE_NAME, new_session

router = APIRouter(prefix="/api/auth", tags=["auth"])
LOGIN_SCOPES = "openid profile email https://graph.microsoft.com/User.Read"


@router.get("/login")
def login(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, str]:
    state, challenge, nonce = create_state(db, None, "login")
    db.commit()
    return {"authorization_url": authorization_url(settings, state, challenge, nonce, LOGIN_SCOPES, settings.ms_redirect_uri)}


@router.get("/callback")
def callback(state: str, code: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Response:
    oauth = db.get(OAuthState, state)
    if not oauth or oauth.purpose != "login" or oauth.expires_at < datetime.now(UTC):
        raise HTTPException(400, "Invalid or expired OAuth state")
    result = exchange_code(settings, code, oauth.verifier, LOGIN_SCOPES.split(), f"{settings.api_base_url}/api/auth/callback")
    claims = result.get("id_token_claims", {})
    access_token = result.get("access_token")
    profile = microsoft_profile(access_token) if access_token else None
    email = profile_email(claims, profile)
    object_id = claims.get("oid") or (profile or {}).get("id")
    if not email:
        raise HTTPException(400, "Microsoft did not return an email address for this account")
    db.delete(oauth)
    user = db.query(User).filter(User.email == email).one_or_none()
    if not user:
        user = User(email=email, ms_object_id=object_id)
        db.add(user); db.flush()
    session = new_session(db, user.id)
    db.commit()
    response = Response(status_code=302, headers={"Location": f"{settings.app_base_url}/app/find"})
    response.set_cookie(COOKIE_NAME, session.id, httponly=True, secure=settings.app_env == "production", samesite="lax", max_age=604800)
    response.set_cookie("csrf_token", session.csrf_token, httponly=False, secure=settings.app_env == "production", samesite="lax", max_age=604800)
    return response


@router.post("/logout")
def logout(response: Response, session: SessionRecord = Depends(require_csrf), db: Session = Depends(get_db)) -> dict[str, bool]:
    db.delete(session); db.commit(); response.delete_cookie(COOKIE_NAME); response.delete_cookie("csrf_token")
    return {"ok": True}
