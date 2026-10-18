"""Mail-client browsing endpoints, against real Postgres.

Covers the OL1 acceptance criteria that can be asserted server-side: cursor
pagination over a 5,000-message folder, unread counts that agree with the
database, hostile HTML rendered inert with images blocked, and per-user
isolation between two accounts.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.integration.mailbox_helpers import (
    bulk_seed,
    make_email,
    make_mailbox,
    make_user,
    sign_in,
)

FOLDERS = "/api/mail/folders"
MESSAGES = "/api/mail/messages"

HOSTILE_HTML = (
    "<html><body>"
    "<p>Quarterly newsletter with hostile markup.</p>"
    "<script>window.__pwned = true;</script>"
    "<p onclick=\"window.__pwned = true\">Click bait.</p>"
    "<a href=\"javascript:window.__pwned = true\">Do not press</a>"
    "<img src=\"https://tracker.example.invalid/pixel.gif\" width=\"1\" height=\"1\">"
    "<iframe src=\"https://evil.example.invalid/\"></iframe>"
    "<form action=\"https://evil.example.invalid/\"><input name=\"x\"></form>"
    "</body></html>"
)


# --------------------------------------------------------------------------- #
# folders + unread counts
# --------------------------------------------------------------------------- #


def test_folders_report_unread_counts_matching_the_database(client: TestClient, db) -> None:
    user = make_user(db, "folders@example.test")
    mailbox = make_mailbox(db, user, folders=["inbox", "sentitems"])
    for index in range(6):
        make_email(db, user, mailbox, graph_id=f"m{index}", is_read=index >= 2)
    make_email(db, user, mailbox, graph_id="sent-1", folder="sentitems", is_read=False)

    sign_in(db, client, user)
    response = client.get(FOLDERS, params={"mailbox_id": str(mailbox.id)})
    assert response.status_code == 200
    payload = response.json()

    folders = {folder["id"]: folder for folder in payload["folders"]}
    assert folders["inbox"]["total"] == 6
    assert folders["inbox"]["unread"] == 2  # exactly the is_read=False rows
    assert folders["sentitems"]["unread"] == 1
    assert payload["unread_total"] == 3
    assert folders["inbox"]["name"] == "Inbox"
    assert folders["sentitems"]["name"] == "Sent Items"


def test_folders_lists_configured_folders_that_hold_no_messages(client: TestClient, db) -> None:
    user = make_user(db, "empty-folder@example.test")
    mailbox = make_mailbox(db, user, folders=["inbox", "drafts"])
    make_email(db, user, mailbox, graph_id="only-inbox")

    sign_in(db, client, user)
    response = client.get(FOLDERS, params={"mailbox_id": str(mailbox.id)})

    folders = {folder["id"]: folder for folder in response.json()["folders"]}
    assert folders["drafts"] == {"id": "drafts", "name": "Drafts", "total": 0, "unread": 0}


# --------------------------------------------------------------------------- #
# cursor pagination
# --------------------------------------------------------------------------- #


def test_cursor_pagination_walks_a_large_folder_without_gaps_or_repeats(client: TestClient, db) -> None:
    """The OL1 scroll target: 5,000 rows, newest first, no duplicates, no losses."""
    user = make_user(db, "big@example.test")
    mailbox = make_mailbox(db, user, folders=["inbox"])
    bulk_seed(db, user, mailbox, 5_000)

    sign_in(db, client, user)

    seen: list[str] = []
    cursor = None
    pages = 0
    while pages < 200:
        params: dict[str, object] = {
            "mailbox_id": str(mailbox.id),
            "folder": "inbox",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        response = client.get(MESSAGES, params=params)
        assert response.status_code == 200
        payload = response.json()
        seen.extend(message["id"] for message in payload["messages"])
        cursor = payload["next_cursor"]
        pages += 1
        if not cursor:
            break

    assert len(seen) == 5_000
    assert len(set(seen)) == 5_000, "cursor pagination returned a duplicate"
    assert pages == 50
    assert cursor is None


def test_message_list_is_newest_first(client: TestClient, db) -> None:
    from datetime import UTC, datetime, timedelta

    user = make_user(db, "order@example.test")
    mailbox = make_mailbox(db, user)
    now = datetime.now(UTC)
    for index in range(5):
        make_email(
            db, user, mailbox,
            graph_id=f"ordered-{index}",
            received_at=now - timedelta(hours=index),
        )

    sign_in(db, client, user)
    payload = client.get(MESSAGES, params={"mailbox_id": str(mailbox.id), "folder": "inbox"}).json()

    stamps = [datetime.fromisoformat(m["received_at"]) for m in payload["messages"]]
    assert stamps == sorted(stamps, reverse=True)


def test_unread_and_attachment_quick_filters(client: TestClient, db) -> None:
    user = make_user(db, "filters@example.test")
    mailbox = make_mailbox(db, user)
    make_email(db, user, mailbox, graph_id="read-1", is_read=True)
    make_email(db, user, mailbox, graph_id="read-2", is_read=True, has_attachments=True)
    make_email(db, user, mailbox, graph_id="unread-1", is_read=False)
    make_email(db, user, mailbox, graph_id="unread-2", is_read=False, has_attachments=True)

    sign_in(db, client, user)
    base = {"mailbox_id": str(mailbox.id), "folder": "inbox"}

    unread = client.get(MESSAGES, params={**base, "filter": "unread"}).json()
    assert len(unread["messages"]) == 2
    assert all(m["is_read"] is False for m in unread["messages"])

    attachments = client.get(MESSAGES, params={**base, "filter": "has_attachments"}).json()
    assert len(attachments["messages"]) == 2
    assert all(m["has_attachments"] is True for m in attachments["messages"])

    every = client.get(MESSAGES, params={**base, "filter": "all"}).json()
    assert len(every["messages"]) == 4


def test_invalid_cursor_is_rejected(client: TestClient, db) -> None:
    user = make_user(db, "cursor@example.test")
    mailbox = make_mailbox(db, user)
    sign_in(db, client, user)

    response = client.get(
        MESSAGES,
        params={"mailbox_id": str(mailbox.id), "folder": "inbox", "cursor": "not-a-cursor"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# message detail
# --------------------------------------------------------------------------- #


def test_message_detail_returns_headers_recipients_and_attachment_names(client: TestClient, db) -> None:
    from app.db import Attachment

    user = make_user(db, "detail@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(
        db, user, mailbox,
        graph_id="detail-1",
        subject="Invoice attached",
        sender_name="A. Sender",
        sender_address="a.sender@example.test",
        body_text="Please find the invoice.",
        is_read=False,
        importance="high",
        is_flagged=True,
        categories=["Finance"],
        web_link="https://outlook.example.invalid/message/1",
        to=[{"emailAddress": {"name": "B. Recipient", "address": "b.recipient@example.test"}}],
        cc=[{"emailAddress": {"name": "C. Cc", "address": "c.cc@example.test"}}],
    )
    db.add(Attachment(
        user_id=user.id,
        mailbox_connection_id=mailbox.id,
        email_id=email.id,
        graph_attachment_id="att-1",
        filename="invoice.pdf",
        file_type="pdf",
        size_bytes=1024,
        status="indexed",
    ))
    db.commit()

    sign_in(db, client, user)
    response = client.get(
        f"/api/mail/messages/{email.id}", params={"mailbox_id": str(mailbox.id)}
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["subject"] == "Invoice attached"
    assert payload["sender_address"] == "a.sender@example.test"
    assert payload["to"] == [{"name": "B. Recipient", "address": "b.recipient@example.test"}]
    assert payload["cc"] == [{"name": "C. Cc", "address": "c.cc@example.test"}]
    assert payload["attachment_names"] == ["invoice.pdf"]
    assert payload["web_link"] == "https://outlook.example.invalid/message/1"
    assert payload["is_read"] is False
    assert payload["importance"] == "high"
    assert payload["is_flagged"] is True
    assert payload["body_text"] == "Please find the invoice."


def test_thread_returns_conversation_messages_oldest_first(client: TestClient, db) -> None:
    from datetime import UTC, datetime, timedelta

    user = make_user(db, "thread@example.test")
    mailbox = make_mailbox(db, user)
    now = datetime.now(UTC)
    make_email(db, user, mailbox, graph_id="t2", conversation_id="conv-1",
               received_at=now - timedelta(days=1), subject="Re: Plan")
    make_email(db, user, mailbox, graph_id="t1", conversation_id="conv-1",
               received_at=now - timedelta(days=2), subject="Plan")
    make_email(db, user, mailbox, graph_id="other", conversation_id="conv-2")

    sign_in(db, client, user)
    response = client.get("/api/mail/threads/conv-1", params={"mailbox_id": str(mailbox.id)})

    assert response.status_code == 200
    subjects = [m["subject"] for m in response.json()["messages"]]
    assert subjects == ["Plan", "Re: Plan"]


# --------------------------------------------------------------------------- #
# safe rendering
# --------------------------------------------------------------------------- #


def test_hostile_email_renders_inert_with_images_blocked(client: TestClient, db) -> None:
    """The OL1 rendering gate: script tags, event handlers and trackers all inert."""
    user = make_user(db, "hostile@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(db, user, mailbox, graph_id="hostile-1", body_html=HOSTILE_HTML)

    sign_in(db, client, user)
    response = client.get(
        f"/api/mail/messages/{email.id}/render", params={"mailbox_id": str(mailbox.id)}
    )
    assert response.status_code == 200
    body = response.text.lower()

    assert "<script" not in body
    assert "onclick" not in body
    assert "javascript:" not in body
    assert "<iframe" not in body
    assert "<form" not in body
    assert "<input" not in body
    assert "tracker.example.invalid" not in body
    assert "data-blocked-image" in body  # the tracker is inert, not merely hidden
    assert "quarterly newsletter" in body  # readable content survives


def test_render_response_carries_its_own_strict_csp(client: TestClient, db) -> None:
    user = make_user(db, "csp@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(db, user, mailbox, graph_id="csp-1", body_html="<p>Hello</p>")

    sign_in(db, client, user)
    response = client.get(
        f"/api/mail/messages/{email.id}/render", params={"mailbox_id": str(mailbox.id)}
    )
    policy = response.headers["content-security-policy"]

    assert "default-src 'none'" in policy
    assert "img-src 'none'" in policy
    assert "form-action 'none'" in policy
    # 'self', not 'none': the reading pane frames this document on purpose, and
    # the iframe's empty sandbox supplies the isolation instead.
    assert "frame-ancestors" in policy
    assert "frame-ancestors 'none'" not in policy
    # The reader origin must be the one allowed to frame it -- not 'self', which
    # would name the API's own origin and forbid the reading pane entirely.
    from urllib.parse import urlsplit

    from app.config import get_settings

    configured = urlsplit(get_settings().app_base_url)
    assert configured.port and str(configured.port) in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize(
    ("theme", "ink", "surface"),
    [
        # tokens.css: --text and --surface.
        ("light", "#171516", "#efeded"),
        ("dark", "#f3f0f1", "#1c1a1b"),
    ],
)
def test_the_rendered_document_carries_the_chosen_theme(
    client: TestClient, db, theme: str, ink: str, surface: str
) -> None:
    """The iframe is an opaque origin: it inherits no colour scheme, cannot see
    data-theme, and cannot even inherit a font family. The theme has to be
    written into the document that actually reaches the browser."""
    user = make_user(db, f"theme-{theme}@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(db, user, mailbox, graph_id=f"theme-{theme}", body_html="<p>Hi</p>")

    sign_in(db, client, user)
    body = client.get(
        f"/api/mail/messages/{email.id}/render",
        params={"mailbox_id": str(mailbox.id), "theme": theme},
    ).text

    assert f'name="color-scheme" content="{theme}"' in body
    assert f"background:{surface}" in body
    assert f"color:{ink}" in body
    style = body.split("<style>")[1].split("</style>")[0]
    # The font is named, not inherited: "inherit" in an opaque-origin frame
    # resolves to the user-agent default, which is not this app's typeface.
    assert "IBM Plex Mono" in style
    assert "inherit" not in style.split("}")[0], (
        "the body font was left to inherit from a document that inherits nothing"
    )
    # No inline colours to fight, so the theme is free to pin the text colour.
    assert "color:inherit!important" in style


def test_a_plain_text_email_is_themed_and_not_labelled(client: TestClient, db) -> None:
    user = make_user(db, "theme-plain@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(db, user, mailbox, graph_id="theme-plain",
                       body_html=None, body_text="Just words.")

    sign_in(db, client, user)
    body = client.get(
        f"/api/mail/messages/{email.id}/render",
        params={"mailbox_id": str(mailbox.id), "theme": "dark"},
    ).text

    assert "Original formatting" not in body
    assert "color:inherit!important" in body


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_an_email_with_its_own_colours_is_shown_on_paper_in_both_themes(
    client: TestClient, db, theme: str
) -> None:
    """Forcing the theme onto a white table destroys the sender's design in one
    theme and is unreadable in the other, so such a message keeps its colours
    and is shown on paper instead -- with a label saying why."""
    user = make_user(db, f"paper-{theme}@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(
        db, user, mailbox, graph_id=f"paper-{theme}",
        body_html='<table bgcolor="#ffffff"><tr><td style="color:#000000">Hi</td></tr></table>',
    )

    sign_in(db, client, user)
    body = client.get(
        f"/api/mail/messages/{email.id}/render",
        params={"mailbox_id": str(mailbox.id), "theme": theme},
    ).text

    assert "background:#ffffff" in body
    assert "color:#171516" in body
    assert "Original formatting" in body
    # The sender's own declaration survives: that is the whole point of paper.
    assert "color:inherit!important" not in body


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_dark_email_never_renders_black_on_black(client: TestClient, db, theme: str) -> None:
    """The sender's background paints over ours whatever we do, so the ink is the
    only thing that can be fixed. Left alone it is the sender's black, on the
    sender's near-black: unreadable, and the reason paper mode was not enough."""
    user = make_user(db, f"darkmail-{theme}@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(
        db, user, mailbox, graph_id=f"darkmail-{theme}",
        body_html='<table bgcolor="#141419"><tr><td style="color:#000000">Status update</td></tr></table>',
    )

    sign_in(db, client, user)
    body = client.get(
        f"/api/mail/messages/{email.id}/render",
        params={"mailbox_id": str(mailbox.id), "theme": theme},
    ).text

    style = body.split("<style>")[1].split("</style>")[0]
    # Light ink on a dark sheet, in both themes, forced over the sender's
    # declaration. The surface has to move too, or the parts of the message
    # outside the sender's own dark element would be light on light.
    assert "color:#f3f0f1" in style
    assert "background:#1c1a1b" in style
    assert "color:inherit!important" in style
    # Not the paper sheet: that is what put white behind their dark table.
    assert "background:#ffffff" not in style
    assert "Original formatting" in body


def test_load_images_is_an_explicit_opt_in(client: TestClient, db) -> None:
    user = make_user(db, "images@example.test")
    mailbox = make_mailbox(db, user)
    html = '<p>Report</p><img src="https://cdn.example.invalid/chart.png" alt="chart">'
    email = make_email(db, user, mailbox, graph_id="img-1", body_html=html)

    sign_in(db, client, user)
    blocked = client.get(
        f"/api/mail/messages/{email.id}/render", params={"mailbox_id": str(mailbox.id)}
    )
    loaded = client.get(
        f"/api/mail/messages/{email.id}/render",
        params={"mailbox_id": str(mailbox.id), "images": True},
    )

    assert "cdn.example.invalid" not in blocked.text
    assert "img-src 'none'" in blocked.headers["content-security-policy"]
    assert "cdn.example.invalid" in loaded.text
    assert "img-src https:" in loaded.headers["content-security-policy"]


def test_plain_text_body_falls_back_to_preserved_line_breaks(client: TestClient, db) -> None:
    user = make_user(db, "plain@example.test")
    mailbox = make_mailbox(db, user)
    email = make_email(
        db, user, mailbox, graph_id="plain-1", body_html=None,
        body_text="Line one\nLine two\n\nLine four",
    )

    sign_in(db, client, user)
    response = client.get(
        f"/api/mail/messages/{email.id}/render", params={"mailbox_id": str(mailbox.id)}
    )

    assert "<pre>" in response.text
    assert "Line one\nLine two" in response.text


def test_quoted_reply_is_separated_behind_a_toggle(client: TestClient, db) -> None:
    user = make_user(db, "quoted@example.test")
    mailbox = make_mailbox(db, user)
    html = '<p>My reply.</p><blockquote type="cite">The original message.</blockquote>'
    email = make_email(db, user, mailbox, graph_id="quoted-1", body_html=html)

    sign_in(db, client, user)
    hidden = client.get(
        f"/api/mail/messages/{email.id}/render", params={"mailbox_id": str(mailbox.id)}
    )
    shown = client.get(
        f"/api/mail/messages/{email.id}/render",
        params={"mailbox_id": str(mailbox.id), "quoted": True},
    )

    assert "original message" not in hidden.text
    assert "My reply." in hidden.text
    assert "original message" in shown.text


def test_stored_html_is_already_sanitised_at_ingest(db) -> None:
    """Defence in depth: the database must never hold raw hostile markup."""
    from app.db import Email
    from app.ingest.sync import upsert_message

    user = make_user(db, "ingest@example.test")
    mailbox = make_mailbox(db, user)

    upsert_message(
        db,
        user_id=user.id,
        mailbox_id=mailbox.id,
        folder_id="inbox",
        message={
            "id": "raw-hostile",
            "subject": "Raw",
            "receivedDateTime": "2026-03-20T09:00:00+00:00",
            "isRead": True,
            "sender": {"emailAddress": {"name": "N", "address": "n@example.test"}},
            "body": {"contentType": "html", "content": HOSTILE_HTML},
        },
        target_tokens=400,
        overlap_tokens=40,
    )

    stored = db.query(Email).all()
    assert stored
    for row in stored:
        lowered = (row.body_html or "").lower()
        assert "<script" not in lowered
        assert "onclick" not in lowered
        assert "tracker.example.invalid" not in lowered
        # The plain-text projection used for search and retrieval is clean too.
        assert "<img" not in (row.body_text or "").lower()


def test_sync_backfills_read_state_onto_unchanged_rows(db) -> None:
    """Rows written before 0010 must pick up isRead/importance/flag on re-sync.

    The content hash deliberately ignores read state, so without the early-return
    path in upsert_message carrying these columns the first sync after the
    migration would silently leave every pre-existing message at its default.
    """
    from app.ingest.sync import upsert_message

    user = make_user(db, "backfill@example.test")
    mailbox = make_mailbox(db, user)

    message = {
        "id": "backfill-1",
        "subject": "Existing",
        "receivedDateTime": "2026-03-20T09:00:00+00:00",
        "sender": {"emailAddress": {"name": "N", "address": "n@example.test"}},
        "body": {"contentType": "text", "content": "Same body"},
    }
    upsert_message(
        db, user_id=user.id, mailbox_id=mailbox.id, folder_id="inbox",
        message=dict(message), target_tokens=400, overlap_tokens=40,
    )

    from app.db import Email

    before = db.query(Email).one()
    assert before.is_read is True
    assert before.importance == "normal"

    # Re-sync the identical message, now with state changed upstream.
    upsert_message(
        db, user_id=user.id, mailbox_id=mailbox.id, folder_id="inbox",
        message={**message, "isRead": False, "importance": "high",
                 "flag": {"flagStatus": "flagged"}, "categories": ["Follow up"]},
        target_tokens=400, overlap_tokens=40,
    )

    after = db.query(Email).one()
    assert after.is_read is False
    assert after.importance == "high"
    assert after.is_flagged is True
    assert after.categories == ["Follow up"]


# --------------------------------------------------------------------------- #
# per-user isolation
# --------------------------------------------------------------------------- #


def test_user_b_cannot_fetch_user_a_messages(client: TestClient, db) -> None:
    """The OL1 isolation gate, asserted at the API boundary."""
    alice = make_user(db, "alice@example.test")
    bob = make_user(db, "bob@example.test")
    alice_mailbox = make_mailbox(db, alice, folders=["inbox"])
    bob_mailbox = make_mailbox(db, bob, folders=["inbox"])
    secret = make_email(db, alice, alice_mailbox, graph_id="alice-1", subject="Alice private")

    sign_in(db, client, bob)

    # By id, naming Alice's mailbox.
    direct = client.get(
        f"/api/mail/messages/{secret.id}", params={"mailbox_id": str(alice_mailbox.id)}
    )
    assert direct.status_code == 404

    # By id, naming Bob's own mailbox -- the combination an attacker controls.
    crossed = client.get(
        f"/api/mail/messages/{secret.id}", params={"mailbox_id": str(bob_mailbox.id)}
    )
    assert crossed.status_code == 404

    # Bob's own list must not contain it either.
    listed = client.get(MESSAGES, params={"mailbox_id": str(bob_mailbox.id), "folder": "inbox"})
    assert listed.status_code == 200
    assert all(message["id"] != str(secret.id) for message in listed.json()["messages"])

    # Nor can he render it.
    rendered = client.get(
        f"/api/mail/messages/{secret.id}/render", params={"mailbox_id": str(bob_mailbox.id)}
    )
    assert rendered.status_code == 404

    # Nor can he read Alice's folders.
    folders = client.get(FOLDERS, params={"mailbox_id": str(alice_mailbox.id)})
    assert folders.status_code == 404


def test_user_b_cannot_read_user_a_thread(client: TestClient, db) -> None:
    alice = make_user(db, "alice2@example.test")
    bob = make_user(db, "bob2@example.test")
    alice_mailbox = make_mailbox(db, alice)
    make_email(db, alice, alice_mailbox, graph_id="a1", conversation_id="shared-conv-id")

    sign_in(db, client, bob)
    response = client.get(
        "/api/mail/threads/shared-conv-id", params={"mailbox_id": str(alice_mailbox.id)}
    )
    assert response.status_code == 404


def test_anonymous_request_is_rejected(client: TestClient, db) -> None:
    user = make_user(db, "anon-check@example.test")
    mailbox = make_mailbox(db, user)
    client.cookies.clear()

    for path, params in [
        (FOLDERS, {"mailbox_id": str(mailbox.id)}),
        (MESSAGES, {"mailbox_id": str(mailbox.id), "folder": "inbox"}),
    ]:
        assert client.get(path, params=params).status_code == 401


# --------------------------------------------------------------------------- #
# read-only guarantee
# --------------------------------------------------------------------------- #


def test_no_endpoint_can_mutate_message_state() -> None:
    """Read-only Mail.Read: nothing may change a message.

    The ask endpoints take POST because they carry a request body, which does
    not make them mutations. The browsing endpoints must be GET-only, and no
    endpoint anywhere may expose the verbs that would need Mail.ReadWrite or
    Mail.Send.
    """
    from app.main import app

    schema = app.openapi()
    mail_paths = [path for path in schema["paths"] if path.startswith("/api/mail/")]
    assert mail_paths, "expected the mail endpoints"

    read_only_paths = [
        path for path in mail_paths
        if not path.startswith("/api/mail/ask")
    ]
    for path in read_only_paths:
        assert set(schema["paths"][path]) == {"get"}, (
            f"{path} exposes a non-GET method; the mailbox grant is Mail.Read"
        )

    # The only non-GET verbs anywhere under /api/mail are the ask body endpoint,
    # recording a history row, and clearing that history -- none of which touch a
    # message. The verbs below are exactly what a write grant would enable.
    write_verbs = {"put", "patch"}
    for path in mail_paths:
        methods = set(schema["paths"][path])
        assert not (methods & write_verbs), f"{path} exposes a message-rewriting verb"
        if "delete" in methods:
            # Only the ask history may be deleted, and only for its owner.
            assert path == "/api/mail/ask/history", (
                f"{path} exposes DELETE; deleting a message needs Mail.ReadWrite"
            )


def test_ask_endpoint_writes_only_to_ask_history() -> None:
    """The ask stream must not be able to modify a message row."""
    from app.main import app

    spec = app.openapi()["paths"]["/api/mail/ask"]["post"]
    # Nothing in the request body addresses a message id, so there is no way to
    # name a message to write to.
    schema_ref = spec.get("requestBody", {})
    assert schema_ref, "ask should carry a request body"
    body = str(schema_ref)
    assert "email_id" not in body
    assert "message_id" not in body


def test_scopes_stay_read_only() -> None:
    from app.config import Settings

    settings = Settings(
        session_secret="x" * 32,
        token_encryption_key="y" * 32,
        ms_scopes="openid profile offline_access https://graph.microsoft.com/Mail.Read",
    )
    assert "Mail.ReadWrite" not in settings.ms_scopes
    assert "Mail.Send" not in settings.ms_scopes

    with pytest.raises(ValidationError):
        Settings(
            session_secret="x" * 32,
            token_encryption_key="y" * 32,
            ms_scopes="https://graph.microsoft.com/Mail.ReadWrite",
        )