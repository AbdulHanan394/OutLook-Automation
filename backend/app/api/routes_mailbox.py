from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.microsoft import (
    authorization_url,
    create_state,
    exchange_code,
    microsoft_profile,
    profile_email,
)
from app.config import Settings, get_settings
from app.db import Email, IndexingJob, MailboxConnection, OAuthState, User, get_db
from app.security.crypto import TokenCipher
from app.security.csrf import require_csrf
from app.security.sessions import COOKIE_NAME, new_session, require_user

router = APIRouter(prefix="/api", tags=["mailbox"])
LOGIN_SCOPES = "openid profile email https://graph.microsoft.com/User.Read"
IDENTITY_SCOPE = "https://graph.microsoft.com/User.Read"


def owned_mailbox(mailbox_id: UUID, user_id: UUID, db: Session) -> MailboxConnection:
    mailbox = db.scalar(select(MailboxConnection).where(MailboxConnection.id == mailbox_id, MailboxConnection.user_id == user_id))
    if not mailbox:
        raise HTTPException(404, "Mailbox not found")
    return mailbox


@router.post("/outlook/connect")
def connect(user_id: UUID = Depends(require_user), _=Depends(require_csrf), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, str]:
    state, challenge, nonce = create_state(db, user_id, "connect_mailbox"); db.commit()
    scopes = settings.ms_scopes.split()
    if IDENTITY_SCOPE not in scopes:
        scopes.append(IDENTITY_SCOPE)
    return {"authorization_url": authorization_url(settings, state, challenge, nonce, " ".join(scopes), settings.ms_redirect_uri)}


@router.get("/outlook/callback", response_model=None)
def outlook_callback(state: str, code: str | None = None, error: str | None = None, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Response | dict[str, str]:
    oauth = db.get(OAuthState, state)
    if not oauth or oauth.purpose not in {"connect_mailbox", "login"} or oauth.expires_at < datetime.now(UTC):
        raise HTTPException(400, "Invalid or expired OAuth state")
    if error == "access_denied":
        db.delete(oauth); db.commit()
        return {"status": "consent_denied"}
    if not code: raise HTTPException(400, "Authorization code missing")
    requested_scopes = LOGIN_SCOPES.split() if oauth.purpose == "login" else settings.ms_scopes.split()
    result = exchange_code(settings, code, oauth.verifier, requested_scopes, settings.ms_redirect_uri)
    if oauth.purpose == "login":
        claims = result.get("id_token_claims", {})
        access_token = result.get("access_token")
        profile = microsoft_profile(access_token) if access_token else None
        email = profile_email(claims, profile)
        if not email:
            raise HTTPException(400, "Microsoft did not return an email address for this account")
        user = db.query(User).filter(User.email == email).one_or_none()
        if not user:
            user = User(email=email, ms_object_id=claims.get("oid") or (profile or {}).get("id"))
            db.add(user); db.flush()
        session = new_session(db, user.id)
        db.delete(oauth); db.commit()
        response = Response(status_code=302, headers={"Location": f"{settings.app_base_url}/app/find"})
        response.set_cookie(COOKIE_NAME, session.id, httponly=True, secure=settings.app_env == "production", samesite="lax", max_age=604800)
        response.set_cookie("csrf_token", session.csrf_token, httponly=False, secure=settings.app_env == "production", samesite="lax", max_age=604800)
        return response
    scopes = result.get("scope", "").split()
    if "https://graph.microsoft.com/Mail.Read" not in scopes and "Mail.Read" not in scopes:
        raise HTTPException(400, "Mail.Read was not granted")
    refresh = result.get("refresh_token")
    claims = result.get("id_token_claims", {})
    access_token = result.get("access_token")
    profile = microsoft_profile(access_token) if access_token else None
    ms_user_id = claims.get("oid") or (profile or {}).get("id")
    account_email = profile_email(claims, profile)
    if not refresh or not ms_user_id or not account_email or not oauth.user_id:
        raise HTTPException(502, "Mailbox connection response was incomplete")
    key = settings.token_encryption_key.get_secret_value()
    if not TokenCipher.valid_key(key): raise HTTPException(500, "Token encryption key is invalid")
    tenant_id = claims.get("tid", settings.ms_tenant)
    mailbox = db.scalar(select(MailboxConnection).where(MailboxConnection.user_id == oauth.user_id, MailboxConnection.ms_tenant_id == tenant_id, MailboxConnection.ms_user_id == ms_user_id))
    if mailbox:
        mailbox.account_email = account_email
        mailbox.granted_scopes = " ".join(scopes)
        mailbox.encrypted_refresh_token = TokenCipher("v1", [key]).encrypt(refresh)
        mailbox.status = "active"
        mailbox.index_scope = {"folders": settings.folders, "lookback_days": settings.index_lookback_days}
    else:
        mailbox = MailboxConnection(user_id=oauth.user_id, ms_tenant_id=tenant_id, ms_user_id=ms_user_id, account_email=account_email, granted_scopes=" ".join(scopes), encrypted_refresh_token=TokenCipher("v1", [key]).encrypt(refresh), status="active", index_scope={"folders": settings.folders, "lookback_days": settings.index_lookback_days})
        db.add(mailbox); db.flush()
    db.add(IndexingJob(user_id=oauth.user_id, mailbox_connection_id=mailbox.id, type="sync_folder", payload={"folders": settings.folders}))
    db.delete(oauth); db.commit()
    return Response(status_code=302, headers={"Location": f"{settings.app_base_url}/app/settings"})


@router.get("/mailboxes/{mailbox_id}/sync")
def sync_status(mailbox_id: UUID, user_id: UUID = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    mailbox = owned_mailbox(mailbox_id, user_id, db)
    jobs = list(db.scalars(select(IndexingJob).where(IndexingJob.mailbox_connection_id == mailbox.id)))
    email_count = db.scalar(select(func.count()).select_from(Email).where(Email.mailbox_connection_id == mailbox.id, ~Email.is_deleted)) or 0
    latest = max(jobs, key=lambda job: job.created_at) if jobs else None
    return {"state": "indexing" if any(job.status in {"queued", "running"} for job in jobs) else "complete", "folders": mailbox.index_scope.get("folders", []), "email_count": email_count, "job_status": latest.status if latest else None, "job_error": latest.error_code if latest else None, "embedding": {"done": sum(job.progress_done for job in jobs), "total": None}, "searchable": {"keyword": email_count > 0, "semantic": "ready" if mailbox.external_ai_consent_at else "disabled_no_consent"}, "last_success_at": latest.finished_at.isoformat() if latest and latest.status == "succeeded" and latest.finished_at else None}


@router.get("/mailboxes")
def list_mailboxes(user_id: UUID = Depends(require_user), db: Session = Depends(get_db)) -> list[dict[str, str | bool]]:
    mailboxes = list(db.scalars(select(MailboxConnection).where(MailboxConnection.user_id == user_id).order_by(MailboxConnection.created_at.desc())))
    return [
        {
            "id": str(mailbox.id),
            "email": mailbox.account_email,
            "status": mailbox.status,
            "ai_consent": mailbox.external_ai_consent_at is not None,
        }
        for mailbox in mailboxes
    ]


@router.post("/mailboxes/{mailbox_id}/sync", status_code=202)
def start_sync(mailbox_id: UUID, user_id: UUID = Depends(require_user), _=Depends(require_csrf), db: Session = Depends(get_db)) -> dict[str, str]:
    mailbox = owned_mailbox(mailbox_id, user_id, db)
    job = IndexingJob(user_id=user_id, mailbox_connection_id=mailbox.id, type="sync_folder", payload={"folders": mailbox.index_scope.get("folders", [])})
    db.add(job); db.commit()
    return {"job_id": str(job.id), "state": "queued"}


@router.post("/mailboxes/{mailbox_id}/consent/ai")
def ai_consent(mailbox_id: UUID, user_id: UUID = Depends(require_user), _=Depends(require_csrf), db: Session = Depends(get_db)) -> dict[str, bool]:
    mailbox = owned_mailbox(mailbox_id, user_id, db); mailbox.external_ai_consent_at = datetime.now(UTC); db.commit()
    return {"accepted": True}


@router.delete("/mailboxes/{mailbox_id}/data", status_code=202)
def delete_data(mailbox_id: UUID, user_id: UUID = Depends(require_user), _=Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    mailbox = owned_mailbox(mailbox_id, user_id, db); job = IndexingJob(user_id=user_id, mailbox_connection_id=mailbox.id, type="delete_data", payload={}); db.add(job); db.commit()
    return {"job_id": str(job.id), "state": "queued"}

@router.delete("/mailboxes/{mailbox_id}", status_code=202)
def disconnect(mailbox_id: UUID, delete_data: bool = False, user_id: UUID = Depends(require_user), _=Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    mailbox = owned_mailbox(mailbox_id, user_id, db); mailbox.status = "disconnecting"
    job = IndexingJob(user_id=user_id, mailbox_connection_id=mailbox.id, type="disconnect", payload={"delete_data": delete_data}); db.add(job); db.commit()
    return {"job_id": str(job.id), "state": "queued"}
