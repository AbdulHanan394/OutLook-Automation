import hashlib
import hmac
from fastapi import APIRouter, Request, Response

from app.config import get_settings

router = APIRouter(prefix="/api/webhooks", tags=["webhook"])

@router.post("/graph")
async def graph_webhook(request: Request, validationToken: str | None = None) -> Response:
    if validationToken is not None:
        return Response(content=validationToken, media_type="text/plain")
    payload = await request.json()
    secret = get_settings().graph_webhook_client_state_secret.get_secret_value()
    for event in payload.get("value", []):
        candidate = event.get("clientState", "")
        if not hmac.compare_digest(candidate, secret):
            return Response(status_code=202)
    return Response(status_code=202)
