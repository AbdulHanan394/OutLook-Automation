import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.db import OAuthState


def create_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def create_state(db: Session, user_id: UUID | None, purpose: str) -> tuple[str, str, str]:
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    verifier, challenge = create_pkce()
    db.add(OAuthState(state=state, user_id=user_id, verifier=verifier, nonce=nonce, purpose=purpose, expires_at=datetime.now(UTC) + timedelta(minutes=10)))
    return state, challenge, nonce


def authorization_url(settings: Settings, state: str, challenge: str, nonce: str, scopes: str, redirect_uri: str) -> str:
    params = {"client_id": settings.ms_client_id, "response_type": "code", "redirect_uri": redirect_uri, "response_mode": "query", "scope": scopes, "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return f"https://login.microsoftonline.com/{settings.ms_tenant}/oauth2/v2.0/authorize?{urlencode(params)}"

def exchange_code(settings: Settings, code: str, verifier: str, scopes: list[str], redirect_uri: str) -> dict[str, Any]:
    """Exchange the authorization code with PKCE at Microsoft's token endpoint."""
    import httpx

    credential = settings.ms_client_secret.get_secret_value() if settings.ms_client_secret else None
    if not credential:
        raise ValueError("MS_CLIENT_SECRET is missing")
    data = {
        "client_id": settings.ms_client_id,
        "client_secret": credential,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    # Do not send `scope` when redeeming an authorization code.  The code is
    # already bound to the scopes the user approved; resending a different
    # (or resource-only) set can make Entra reject the redemption.
    response = httpx.post(
        f"https://login.microsoftonline.com/{settings.ms_tenant}/oauth2/v2.0/token",
        data=data,
        timeout=30,
    )
    result = response.json()
    if response.is_error or "error" in result:
        detail = result.get("error_description", result.get("error", "token exchange failed"))
        raise ValueError(detail)
    return cast(dict[str, Any], result)


def refresh_access_token(settings: Settings, refresh_token: str) -> dict[str, Any]:
    """Exchange an encrypted-at-rest delegated refresh token for Graph access."""
    import httpx

    credential = settings.ms_client_secret.get_secret_value() if settings.ms_client_secret else None
    if not credential:
        raise ValueError("MS_CLIENT_SECRET is missing")
    response = httpx.post(
        f"https://login.microsoftonline.com/{settings.ms_tenant}/oauth2/v2.0/token",
        data={
            "client_id": settings.ms_client_id,
            "client_secret": credential,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    result = response.json()
    if response.is_error or "error" in result:
        raise ValueError("Microsoft refresh-token exchange failed")
    if not result.get("access_token"):
        raise ValueError("Microsoft did not return an access token")
    return cast(dict[str, Any], result)


def microsoft_profile(access_token: str) -> dict[str, Any]:
    """Return the minimal Graph profile fields used to identify an account."""
    import httpx

    response = httpx.get(
        "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if response.is_error:
        raise ValueError("Microsoft Graph could not return the signed-in account")
    return cast(dict[str, Any], response.json())


def profile_email(claims: dict[str, Any], profile: dict[str, Any] | None = None) -> str | None:
    """Find a usable address, tolerating optional OIDC email claims."""
    candidates = [claims.get("preferred_username"), claims.get("email")]
    if profile:
        candidates.extend([profile.get("mail"), profile.get("userPrincipalName")])
    for candidate in candidates:
        if isinstance(candidate, str) and "@" in candidate and "#" not in candidate:
            return candidate.lower()
    return None
