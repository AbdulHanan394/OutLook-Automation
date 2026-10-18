"""Bounded, read-only tools the assistant may call while answering.

This is the one place the assistant gets to choose what to read, instead of the
server guessing from one query string. Three properties make that safe enough
to allow, and all three are enforced here rather than trusted to the prompt:

**Read-only.** There is no write tool, no Outlook credential and no Graph call.
Every tool reads the local index. The assistant cannot send, move, delete, flag
or draft anything, and no tool can be added that does, because the dispatcher
below has no branch that could.

**User-scoped.** Every query filters on ``user_id`` explicitly *and* runs under
the forced row-level policy. There is no way to pass another user's id: no tool
accepts one, and the mailbox is fixed per conversation by the caller.

**Bounded.** Steps, rows per call, result size and the total emails a single
turn may read are all capped. A model that loops, or that calls a tool a
hundred times, stops and is answered from what it had.

Tool results are *untrusted data*, not instructions: email bodies go back as
clearly delimited text, and the dispatcher never lets a tool response become
part of the system prompt.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Email
from app.retrieval.parse import drop_conversational_filler, parse_query
from app.retrieval.pipeline import keyword_search

logger = logging.getLogger(__name__)

# Hard ceilings. A turn is a few calls, not an unbounded crawl of the mailbox.
MAX_STEPS = 4
MAX_ROWS_PER_CALL = 5
MAX_RESULT_CHARS = 6000
MAX_EMAILS_PER_TURN = 25
# One row's body is truncated before it is ever returned to the model.
MAX_BODY_CHARS = 1200

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_emails",
        "description": (
            "Search the user's synced email by keywords and optional filters. "
            "Returns matching messages with subject, sender, date and an excerpt. "
            "Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or a short phrase. Conversational filler is ignored.",
                },
                "from_date": {"type": "string", "description": "ISO date, e.g. 2026-03-01."},
                "to_date": {"type": "string", "description": "ISO date, e.g. 2026-03-31."},
                "folder": {"type": "string", "description": "Optional folder name to restrict to."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_email",
        "description": (
            "Read one message in full by the email_id returned from search_emails. "
            "Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"email_id": {"type": "string", "description": "The email id."}},
            "required": ["email_id"],
        },
    },
    {
        "name": "list_recent",
        "description": "List the most recent messages, optionally for one folder. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Optional folder name."},
                "days": {"type": "integer", "description": "How far back to look, default 7."},
            },
        },
    },
    {
        "name": "count_emails",
        "description": "Count messages, optionally restricted to a folder or a date range. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
            },
        },
    },
]


@dataclass
class ToolResult:
    """What one tool returned, and what it cost against the turn's budget."""

    name: str
    payload: Any
    truncated: bool = False


@dataclass
class ToolBudget:
    """The per-turn ceilings, enforced rather than trusted."""

    steps: int = 0
    emails_read: int = 0

    def allow_step(self) -> bool:
        if self.steps >= MAX_STEPS:
            return False
        self.steps += 1
        return True

    def allow_email(self) -> bool:
        if self.emails_read >= MAX_EMAILS_PER_TURN:
            return False
        self.emails_read += 1
        return True

    def exhausted(self) -> bool:
        return self.steps >= MAX_STEPS or self.emails_read >= MAX_EMAILS_PER_TURN


@dataclass
class ToolRegistry:
    """The dispatch table.

    Only read tools exist. Adding a write tool would mean adding a branch here
    that performs one, which is the point: the absence is visible.
    """

    db: Session
    user_id: UUID
    mailbox_id: UUID
    settings: Settings
    budget: ToolBudget = field(default_factory=ToolBudget)

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handler: Callable[[dict[str, Any]], ToolResult] | None = {
            "search_emails": self._search_emails,
            "read_email": self._read_email,
            "list_recent": self._list_recent,
            "count_emails": self._count_emails,
        }.get(name)
        if handler is None:
            # An unknown tool is a dead end, not an error the model can exploit.
            return ToolResult(name, {"error": f"No such tool: {name}"})
        try:
            return handler(arguments)
        except (ValueError, TypeError) as error:
            return ToolResult(name, {"error": str(error)})

    # ------------------------------------------------------------- the tools

    def _search_emails(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("search_emails needs a query")
        # Same rule as the ask path: a question is not a bag of required tokens.
        criteria = drop_conversational_filler(parse_query(query))
        status, emails, _ = keyword_search(
            self.db,
            user_id=self.user_id,
            mailbox_id=self.mailbox_id,
            criteria=criteria,
            limit=MAX_ROWS_PER_CALL,
            min_score=self.settings.conf_min_score,
            margin=self.settings.conf_margin,
            received_before=_parse_date(arguments.get("to_date")),
        )
        folder = _clean(arguments.get("folder"))
        rows = [_row(email) for email in emails]
        if folder:
            rows = [row for row in rows if row["folder"] == folder]
        return ToolResult("search_emails", {"status": status, "results": rows})

    def _read_email(self, arguments: dict[str, Any]) -> ToolResult:
        email_id = str(arguments.get("email_id", "")).strip()
        if not email_id:
            raise ValueError("read_email needs an email_id")
        if not self.budget.allow_email():
            return ToolResult(
                "read_email", {"error": "This turn has already read as many messages as it may."}
            )
        # Scoped to this user and this conversation's mailbox. There is no path
        # from a tool argument to another user's row.
        email = self.db.scalar(
            select(Email).where(
                Email.id == _as_uuid(email_id),
                Email.user_id == self.user_id,
                Email.mailbox_connection_id == self.mailbox_id,
                ~Email.is_deleted,
            )
        )
        if email is None:
            return ToolResult("read_email", {"error": "No such message in this mailbox."})
        return ToolResult("read_email", {"email": _row(email, with_body=True)})

    def _list_recent(self, arguments: dict[str, Any]) -> ToolResult:
        days = max(1, min(int(arguments.get("days", 7) or 7), 365))
        statement = select(Email).where(
            Email.user_id == self.user_id,
            Email.mailbox_connection_id == self.mailbox_id,
            ~Email.is_deleted,
        )
        folder = _clean(arguments.get("folder"))
        if folder:
            statement = statement.where(Email.folder_alias == folder)
        rows = [
            _row(email)
            for email in self.db.scalars(
                statement.order_by(Email.received_at.desc()).limit(MAX_ROWS_PER_CALL)
            )
        ]
        return ToolResult("list_recent", {"days": days, "results": rows})

    def _count_emails(self, arguments: dict[str, Any]) -> ToolResult:
        statement = select(func.count()).select_from(Email).where(
            Email.user_id == self.user_id,
            Email.mailbox_connection_id == self.mailbox_id,
            ~Email.is_deleted,
        )
        folder = _clean(arguments.get("folder"))
        if folder:
            statement = statement.where(Email.folder_alias == folder)
        total = self.db.scalar(statement)
        return ToolResult("count_emails", {"count": int(total or 0)})


# ------------------------------------------------------------- formatting


def _row(email: Email, *, with_body: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "email_id": str(email.id),
        "subject": email.subject or "(no subject)",
        "sender": " ".join(filter(None, [email.sender_name, email.sender_address])) or "(unknown)",
        "date": email.received_at.date().isoformat(),
        "folder": email.folder_alias or email.folder_id,
    }
    if with_body:
        body = email.body_text or email.body_preview or email.subject or ""
        row["body"] = body[:MAX_BODY_CHARS]
        row["truncated"] = len(body) > MAX_BODY_CHARS
    else:
        row["preview"] = (email.body_preview or email.subject or "")[:280]
    return row


def render(result: ToolResult) -> str:
    """Tool output as untrusted data the model can quote but not obey."""
    from app.llm.answer import escape_email_content

    try:
        body = json.dumps(result.payload, ensure_ascii=False)
    except (TypeError, ValueError):
        body = str(result.payload)
    if len(body) > MAX_RESULT_CHARS:
        body = body[:MAX_RESULT_CHARS] + "\n[truncated]"
        result.truncated = True
    # Angle brackets are folded as well as the excerpt delimiter: a message body
    # is attacker-controlled far more often than a transcript is.
    safe = escape_email_content(body).replace("<", "&lt;").replace(">", "&gt;")
    return f"<tool_result name=\"{result.name}\">\n{safe}\n</tool_result>"


# ------------------------------------------------------------------ helpers


def _clean(value: Any) -> str:
    """A folder name, trimmed and length-capped. Empty means "no filter"."""
    text = str(value or "").strip()
    return text[:120]


def _as_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _parse_date(value: Any) -> datetime | None:
    """An ISO date the model supplied, or ``None`` if it is not one.

    A malformed date is ignored rather than raising: the model inventing
    "2026-13-45" should cost it a filter, not the whole turn.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.combine(date.fromisoformat(text), time.min, tzinfo=UTC)
    except ValueError:
        return None