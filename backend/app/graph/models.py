from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GraphBody(BaseModel):
    contentType: str = "html"
    content: str = ""


class GraphMessage(BaseModel):
    id: str
    subject: str | None = None
    receivedDateTime: datetime | None = None
    sender: dict[str, Any] | None = None
    body: GraphBody | None = None
    bodyPreview: str | None = None
    internetMessageId: str | None = None
    conversationId: str | None = None
    parentFolderId: str | None = None
    webLink: str | None = None
    hasAttachments: bool = False
    removed: dict[str, Any] | None = Field(default=None, alias="@removed")
