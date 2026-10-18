"""Read-only browsing endpoints for the mail client.

Every handler here reads exclusively from the local database. Nothing in this
module calls Graph at request time: folders, message lists, message detail and
threads are all served from rows written by the sync job, which is what makes
the UI safe to open on a flaky connection and what keeps folder opening fast on
a 5,000-message mailbox.

The mailbox permission is ``Mail.Read``. There is deliberately no endpoint here
that mutates message state -- replying, forwarding, deleting, moving, flagging
and marking read all require permissions this application does not request.
"""

import base64
import binascii
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Attachment, Email, MailboxConnection, get_db, set_user_scope
from app.mail.folders import (
    FOLDER_DISPLAY_NAMES,
    FOLDER_ORDER,
    folder_display_name,
)
from app.mail.sanitize import (
    carries_inline_colour,
    declares_dark_background,
    sanitize_email_html,
    split_quoted,
)
from app.security.rate_limit import enforce_user_rate_limit
from app.security.sessions import require_user

router = APIRouter(prefix="/api/mail", tags=["mail"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50

# The reading pane's font, restated for a document that cannot inherit it. Kept
# in sync with --font-mono in frontend/src/app/tokens.css.
THEME_FONT_FAMILY = (
    "'IBM Plex Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', "
    "ui-monospace, monospace"
)
THEME_FONT = f"font-family:{THEME_FONT_FAMILY}"

# Mirrors tokens.css: --surface / --text / --text-3 / --border / --brand-1.
THEME_PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "surface": "#efeded",
        "ink": "#171516",
        "muted": "#706a6e",
        "rule": "#d4d1d2",
        "link": "#4338ca",
        "select_bg": "#4338ca",
        "select_ink": "#f8f7f7",
    },
    "dark": {
        "surface": "#1c1a1b",
        "ink": "#f3f0f1",
        "muted": "#a49da0",
        "rule": "#393536",
        "link": "#818cf8",
        "select_bg": "#818cf8",
        "select_ink": "#121011",
    },
}

# An email that brings its own colours is shown on paper in *both* themes. A
# white table with black text is the overwhelmingly common case, and repainting
# it into a dark theme means either overriding the sender's design or leaving
# the reader with black-on-black. Paper plus a label is the only honest option.
PAPER_PALETTES = {
    "surface": "#ffffff",
    "ink": "#171516",
    "muted": "#706a6e",
    "rule": "#d4d1d2",
    "link": "#2b3ec9",
    "select_bg": "#4338ca",
    "select_ink": "#f8f7f7",
}

# A sender-designed *dark* email is the mirror image of the paper case, and gets
# the mirror-image fix: a dark sheet with light ink, in *both* themes.
#
# The surface changes as well as the ink. Forcing light ink onto the light
# theme's background would make the parts of the message that sit outside the
# sender's own dark element invisible -- light on light, which is the same
# unreadable outcome by a different route.
DARK_BACKGROUND_PALETTE = {**THEME_PALETTES["dark"], "link": "#a5b4fc"}


def _render_palette(
    theme: str, *, paper: bool = False, dark_background: bool = False
) -> dict[str, str]:
    if dark_background:
        return DARK_BACKGROUND_PALETTE
    return PAPER_PALETTES if paper else THEME_PALETTES[theme]


def _render_style(palette: dict[str, str], *, force_inherit: bool) -> str:
    """The base stylesheet for the sandboxed email document.

    ``force_inherit`` is used only for messages with no colour of their own. For
    those, pinning every descendant to the body's colour is what guarantees the
    message is readable in whichever theme is active -- a body rule alone loses
    to any inline style, and dark mode turns unstyled HTML emails black-on-black
    on some clients that supply default dark colours. For an email that *does*
    carry its own colours this is switched off, because overriding them is
    exactly the destruction the "Original formatting" path exists to avoid.

    The selector is ``body *`` and not ``*``. A universal selector matches the
    body element itself, so ``*`` would make the body inherit from <html> --
    which has no colour of its own -- and every message would render in the
    user-agent's default text colour on our dark surface: black on black.
    """
    base = (
        f"body{{margin:0;padding:14px;{THEME_FONT};font-size:13px;line-height:1.6;"
        f"background:{palette['surface']};color:{palette['ink']}}}"
    )
    inherit = (
        f"body *{{color:inherit!important;font-family:inherit!important;{THEME_FONT_FAMILY}}}"
        if force_inherit
        else ""
    )
    return (
        base
        + inherit
        + f"a{{color:{palette['link']}}}"
        f"a:visited{{color:{palette['link']}}}"
        f"blockquote{{margin:0;padding-left:12px;border-left:3px solid {palette['rule']}}}"
        "img{max-width:100%;height:auto}"
        "pre{white-space:pre-wrap;word-break:break-word;font-family:inherit}"
        "table{max-width:100%}"
        # Selection has to be visible in both themes, and the default selection
        # colour is chosen by the UA against a background it cannot see. Only a
        # flat <color> works here: ::selection silently drops a gradient, which
        # is why an earlier rule pairing --brand-gradient with white text left
        # light mode showing white on white.
        f"::selection{{background:{palette['select_bg']};"
        f"color:{palette['select_ink']}}}"
        # Firefox honours only the prefixed form.
        f"::-moz-selection{{background:{palette['select_bg']};"
        f"color:{palette['select_ink']}}}"
        f"[data-blocked-image]{{display:inline-block;min-width:24px;min-height:24px;"
        f"border:1px dashed {palette['muted']};vertical-align:middle}}"
        f".original-formatting{{margin:0 0 10px;padding:2px 8px;border-radius:999px;"
        f"background:{palette['muted']};color:{palette['surface']};"
        f"font:{THEME_FONT};font-size:10px;font-weight:700;"
        "letter-spacing:0.06em;text-transform:uppercase;display:inline-block}"
    )

__all__ = [
    "DARK_BACKGROUND_PALETTE",
    "FOLDER_DISPLAY_NAMES",
    "FOLDER_ORDER",
    "PAPER_PALETTES",
    "THEME_PALETTES",
    "folder_display_name",
    "get_message",
    "get_thread",
    "list_folders",
    "list_messages",
    "render_message",
    "router",
]


def owned_mailbox(mailbox_id: UUID, user_id: UUID, db: Session) -> MailboxConnection:
    mailbox = db.scalar(
        select(MailboxConnection).where(
            MailboxConnection.id == mailbox_id,
            MailboxConnection.user_id == user_id,
        )
    )
    if not mailbox:
        raise HTTPException(404, "Mailbox not found")
    return mailbox


def encode_cursor(received_at: datetime, email_id: UUID) -> str:
    raw = f"{received_at.isoformat()}|{email_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, _, identifier = raw.partition("|")
        return datetime.fromisoformat(stamp), UUID(identifier)
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise HTTPException(400, "Invalid cursor") from error


def _folder_key(alias: str | None, folder_id: str | None) -> str:
    return (alias or folder_id or "")


def serialize_row(email: Email, attachments: dict[UUID, list[str]]) -> dict[str, object]:
    return {
        "id": str(email.id),
        "subject": email.subject,
        "sender_name": email.sender_name,
        "sender_address": email.sender_address,
        "received_at": email.received_at.isoformat(),
        "sent_at": email.sent_at.isoformat() if email.sent_at else None,
        "folder": _folder_key(email.folder_alias, email.folder_id),
        "preview": (email.body_preview or email.body_text or "")[:300],
        "is_read": bool(email.is_read),
        "importance": email.importance or "normal",
        "is_flagged": bool(email.is_flagged),
        "has_attachments": bool(email.has_attachments),
        "attachment_names": attachments.get(email.id, []),
        "thread_count": 0,  # filled in by the caller when the list is small
    }


def _attachment_names(db: Session, user_id: UUID, email_ids: list[UUID]) -> dict[UUID, list[str]]:
    if not email_ids:
        return {}
    rows = db.execute(
        select(Attachment.email_id, Attachment.filename)
        .where(Attachment.user_id == user_id, Attachment.email_id.in_(email_ids))
        .order_by(Attachment.filename)
    ).all()
    names: dict[UUID, list[str]] = {}
    for email_id, filename in rows:
        names.setdefault(email_id, []).append(filename)
    return names


@router.get("/folders")
def list_folders(
    mailbox_id: UUID = Query(),
    user_id: UUID = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Synced folders with unread counts. No Graph call: counts come from emails."""
    owned_mailbox(mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "mail", settings.rate_limit_mail_per_min)

    key = func.coalesce(Email.folder_alias, Email.folder_id)
    unread: ColumnElement[int] = func.coalesce(
        func.sum(case((Email.is_read.is_(False), 1), else_=0)), 0
    )
    rows = db.execute(
        select(
            key.label("folder"),
            func.count(Email.id),
            unread,
        )
        .where(
            Email.user_id == user_id,
            Email.mailbox_connection_id == mailbox_id,
            Email.is_deleted.is_(False),
        )
        .group_by(key)
    ).all()

    counts: dict[str, dict[str, int]] = {
        str(alias): {"total": int(total), "unread": int(unread)}
        for alias, total, unread in rows
        if alias
    }

    # Folders the mailbox was configured to sync but which hold no messages yet
    # still belong in the sidebar: an empty Drafts is information.
    for alias in mailbox_folders(mailbox_id, user_id, db, settings):
        counts.setdefault(alias, {"total": 0, "unread": 0})

    folders: list[dict[str, object]] = [
        {
            "id": alias,
            "name": folder_display_name(alias),
            "total": value["total"],
            "unread": value["unread"],
        }
        for alias, value in counts.items()
    ]
    folders.sort(
        key=lambda folder: (
            FOLDER_ORDER.index(str(folder["id"]))
            if str(folder["id"]) in FOLDER_ORDER
            else len(FOLDER_ORDER),
            str(folder["name"]),
        )
    )
    unread_total = sum(value["unread"] for value in counts.values())
    return {"folders": folders, "unread_total": unread_total}


def mailbox_folders(mailbox_id: UUID, user_id: UUID, db: Session, settings: Settings) -> list[str]:
    mailbox = owned_mailbox(mailbox_id, user_id, db)
    configured = [str(name) for name in (mailbox.index_scope or {}).get("folders", [])]
    return configured or settings.folders


@router.get("/messages")
def list_messages(
    mailbox_id: UUID = Query(),
    folder: str = Query(min_length=1, max_length=200),
    filter: Literal["all", "unread", "has_attachments"] = "all",
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user_id: UUID = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """One page of message rows, newest first, by opaque cursor.

    Ordering is ``(received_at DESC, id DESC)`` so the composite index built in
    migration 0010 can serve both the sort and the keyset predicate without a
    sort step.
    """
    owned_mailbox(mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "mail", settings.rate_limit_mail_per_min)

    key = func.coalesce(Email.folder_alias, Email.folder_id)
    predicates = [
        Email.user_id == user_id,
        Email.mailbox_connection_id == mailbox_id,
        Email.is_deleted.is_(False),
        key == folder,
    ]
    if filter == "unread":
        predicates.append(Email.is_read.is_(False))
    elif filter == "has_attachments":
        predicates.append(Email.has_attachments.is_(True))

    if cursor:
        stamp, identifier = decode_cursor(cursor)
        predicates.append(
            or_(
                Email.received_at < stamp,
                and_(Email.received_at == stamp, Email.id < identifier),
            )
        )

    rows = list(
        db.scalars(
            select(Email)
            .where(*predicates)
            .order_by(Email.received_at.desc(), Email.id.desc())
            .limit(limit + 1)  # one extra row tells us whether more pages exist
        )
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    attachments = _attachment_names(db, user_id, [row.id for row in rows])
    messages = [serialize_row(row, attachments) for row in rows]

    if messages:
        last = rows[-1]
        messages[-1]["cursor"] = encode_cursor(last.received_at, last.id)

    return {
        "messages": messages,
        "next_cursor": messages[-1]["cursor"] if messages and has_more else None,
        "folder": folder,
        "filter": filter,
    }


@router.get("/messages/{email_id}")
def get_message(
    email_id: UUID,
    mailbox_id: UUID = Query(),
    user_id: UUID = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Headers, recipient lists, attachment names and the plain-text body.

    The sanitised HTML is *not* inlined here. It is served separately by the
    render endpoint so that it can carry its own Content-Security-Policy.
    """
    owned_mailbox(mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "mail", settings.rate_limit_mail_per_min)

    email = db.scalar(
        select(Email).where(
            Email.id == email_id,
            Email.user_id == user_id,
            Email.mailbox_connection_id == mailbox_id,
            Email.is_deleted.is_(False),
        )
    )
    if not email:
        raise HTTPException(404, "Message not found")

    recipients = email.recipients or {}
    names = _attachment_names(db, user_id, [email.id])
    return {
        "id": str(email.id),
        "subject": email.subject,
        "sender_name": email.sender_name,
        "sender_address": email.sender_address,
        "to": [_address_entry(item) for item in recipients.get("to", []) if item],
        "cc": [_address_entry(item) for item in recipients.get("cc", []) if item],
        "received_at": email.received_at.isoformat(),
        "sent_at": email.sent_at.isoformat() if email.sent_at else None,
        "folder": _folder_key(email.folder_alias, email.folder_id),
        "body_text": email.body_text or email.body_preview or "",
        "has_html": bool(email.body_html),
        "has_quoted": email.body_html is not None and bool(split_quoted(email.body_html)[1]),
        "web_link": email.web_link,
        "is_read": bool(email.is_read),
        "importance": email.importance or "normal",
        "is_flagged": bool(email.is_flagged),
        "has_attachments": bool(email.has_attachments),
        "attachment_names": names.get(email.id, []),
        "conversation_id": email.conversation_id,
        "thread_key": email.thread_key,
    }


def _frame_ancestor(app_base_url: str) -> str:
    """CSP frame-ancestors value naming only the reader application."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(app_base_url)
    except ValueError:
        return "'none'"
    if not parts.scheme or not parts.netloc:
        return "'none'"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"http://{host} https://{host}"


def _address_entry(value: object) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return {"name": None, "address": None}
    address = value.get("emailAddress")
    address = address if isinstance(address, dict) else {}
    return {
        "name": address.get("name"),
        "address": address.get("address"),
    }


@router.get("/messages/{email_id}/render")
def render_message(
    email_id: UUID,
    mailbox_id: UUID = Query(),
    images: bool = Query(default=False),
    quoted: bool = Query(default=False),
    theme: Literal["light", "dark"] = Query(default="light"),
    user_id: UUID = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Sanitised HTML for the reading pane's sandboxed iframe.

    This response carries its own Content-Security-Policy. The parent document's
    policy has no ``script-src`` and no ``unsafe-inline``; keeping it that way is
    the whole point of putting email markup in a separate document with an empty
    ``sandbox`` attribute. Because the iframe is an opaque origin, allowing
    inline *style* here costs nothing: no script can run in this document at all,
    and inline styles are what email layout is made of.

    ``images`` is the reader's explicit "Load images" action. It stays False by
    default because a remote image in an email is a tracking pixel until proven
    otherwise.
    """
    owned_mailbox(mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "mail", settings.rate_limit_mail_per_min)

    email = db.scalar(
        select(Email).where(
            Email.id == email_id,
            Email.user_id == user_id,
            Email.mailbox_connection_id == mailbox_id,
            Email.is_deleted.is_(False),
        )
    )
    if not email:
        raise HTTPException(404, "Message not found")

    if email.body_html:
        body, quoted_html = split_quoted(email.body_html)
        if not quoted:
            quoted_html = ""
        content = body + quoted_html
    else:
        # Plain-text fallback. Line breaks are preserved verbatim because
        # whitespace is the only formatting a text/plain body has.
        from html import escape as html_escape

        content = f"<pre>{html_escape(email.body_text or email.body_preview or '')}</pre>"

    # Re-sanitised here even though ingest already sanitised: this is the
    # document that actually reaches a browser, and defence in depth is cheap.
    safe = sanitize_email_html(content, allow_images=images)

    # frame-ancestors names the app origin, not 'self': the reader runs on a
    # different origin than this API, so 'self' would forbid the very framing the
    # reading pane performs. Naming APP_BASE_URL precisely still blocks every
    # other site from framing someone's mail, and the iframe's empty sandbox
    # means the app holds an opaque origin even when it does frame it. The
    # session cookie is SameSite=Lax, so a cross-site frame carries nothing.
    ancestors = _frame_ancestor(settings.app_base_url)
    policy = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src https: http: data:; "
        f"base-uri 'none'; form-action 'none'; frame-ancestors {ancestors}"
        if images
        else (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; "
            f"base-uri 'none'; form-action 'none'; frame-ancestors {ancestors}"
        )
    )
    # The iframe is an opaque origin: it inherits no colour scheme and cannot
    # see the app's data-theme attribute. The reader's chosen theme is passed
    # explicitly rather than guessed from prefers-color-scheme, which would
    # disagree whenever the app theme differs from the OS setting. The whole
    # sheet below is re-sent on every theme change, which is why the reader
    # reloads the frame when data-theme flips rather than editing it in place.
    #
    # The font stack mirrors the app's --font-mono. It is named rather than
    # inherited: an opaque-origin frame inherits *nothing* from its embedder, so
    # `font-family: inherit` silently resolved to the UA default and every email
    # rendered in a serif face the rest of the app does not use.
    dark_background = declares_dark_background(safe)
    paper = carries_inline_colour(safe) and not dark_background
    palette = _render_palette(theme, paper=paper, dark_background=dark_background)
    # A dark-background email is the one case where the sender's ink is
    # overridden: their background paints over ours whatever we do, so replacing
    # the ink is the only way to keep the text readable. Everything else is
    # either left alone (paper) or has nothing to conflict with (themed).
    style = _render_style(palette, force_inherit=not paper)
    label = (
        '<p class="original-formatting">Original formatting</p>'
        if paper or dark_background
        else ""
    )
    document = (
        '<!doctype html><html><head><meta charset="utf-8">'
        # Second layer: a document policy intersects with the header policy, so
        # both have to agree for anything to load.
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
        f'<meta name="color-scheme" content="{theme}">'
        f"<style>{style}</style></head><body>"
        f"{label}{safe}</body></html>"
    )
    return Response(
        content=document,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": policy,
            "X-Content-Type-Options": "nosniff",
            # The document is a one-shot render of stored content; never let a
            # browser or proxy keep a stale copy of somebody's mail.
            "Cache-Control": "no-store, private",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/threads/{thread_id}")
def get_thread(
    thread_id: str,
    mailbox_id: UUID = Query(),
    user_id: UUID = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Messages in the same conversation, headers only, oldest first."""
    owned_mailbox(mailbox_id, user_id, db)
    set_user_scope(db, user_id)
    enforce_user_rate_limit(user_id, "mail", settings.rate_limit_mail_per_min)

    identifier = thread_id.strip()
    if len(identifier) > 200:
        raise HTTPException(400, "Invalid thread")

    rows = list(
        db.scalars(
            select(Email)
            .where(
                Email.user_id == user_id,
                Email.mailbox_connection_id == mailbox_id,
                Email.is_deleted.is_(False),
                or_(
                    Email.conversation_id == identifier,
                    Email.thread_key == identifier,
                    Email.id == _as_uuid(identifier),
                ),
            )
            .order_by(Email.received_at.asc())
            .limit(100)
        )
    )
    attachments = _attachment_names(db, user_id, [row.id for row in rows])
    return {
        "thread_id": identifier,
        "messages": [
            {
                **serialize_row(row, attachments),
                "is_current": False,
            }
            for row in rows
        ],
    }


def _as_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None
