"""Seed a demo mailbox so the mail client can be driven in a browser.

Loads the synthetic corpus through the real sync upsert -- the same code path
Graph ingestion uses -- so what the UI shows is what the sync job would have
produced. Prints a session token for the browser tests to present as a cookie.

Usage::

    DATABASE_URL=... SESSION_SECRET=... TOKEN_ENCRYPTION_KEY=... \\
    python eval/seed_demo_mailbox.py --size 5000
"""

import argparse
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("SESSION_SECRET", "seed-session-secret-0123456789abcdef")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "seed-token-key-0123456789abcdef")
os.environ.setdefault("MS_CLIENT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("MS_CLIENT_SECRET", "placeholder")

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import MailboxConnection, SessionRecord, User, engine, set_user_scope  # noqa: E402
from app.ingest.sync import upsert_message  # noqa: E402
from generate_synthetic_mailbox import main as generate_fixture  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=5000)
    parser.add_argument("--email", default="demo@example.test")
    parser.add_argument("--out", default="demo_session.json")
    parser.add_argument("--hostile-subject", default="Quarterly newsletter")
    args = parser.parse_args()

    with tempfile_out() as corpus_path:
        argv = sys.argv
        sys.argv = ["generate", "--size", str(args.size), "--out", str(corpus_path.name)]
        try:
            generate_fixture()
        finally:
            sys.argv = argv
        corpus = json.loads(Path(corpus_path.name).read_text(encoding="utf-8"))["value"]

    with Session(engine) as db:
        set_user_scope(db, uuid.UUID(int=0))

        existing = db.query(User).filter(User.email == args.email).one_or_none()
        if existing:
            user = existing
            mailbox = (
                db.query(MailboxConnection)
                .filter(MailboxConnection.user_id == user.id)
                .first()
            )
            assert mailbox is not None
            db.execute(text("DELETE FROM emails WHERE user_id = :uid"), {"uid": user.id})
        else:
            user = User(email=args.email)
            db.add(user)
            db.flush()
            mailbox = MailboxConnection(
                user_id=user.id,
                ms_tenant_id="organizations",
                ms_user_id=f"demo-{uuid.uuid4().hex[:8]}",
                account_email=args.email,
                granted_scopes="Mail.Read",
                status="active",
                index_scope={"folders": ["inbox", "sentitems", "archive"], "lookback_days": 365},
            )
            db.add(mailbox)
            db.flush()

        set_user_scope(db, user.id)

        indexed = 0
        for message in corpus:
            if upsert_message(
                db,
                user_id=user.id,
                mailbox_id=mailbox.id,
                folder_id=message.get("parentFolderId", "inbox"),
                message=message,
                target_tokens=400,
                overlap_tokens=40,
            ):
                indexed += 1

        # Give the hostile fixture a subject the UI test can find by name.
        db.execute(
            text(
                "UPDATE emails SET subject = :subject "
                "WHERE mailbox_connection_id = :mid AND graph_message_id = 'hostile-html-1'"
            ),
            {"subject": args.hostile_subject, "mid": mailbox.id},
        )

        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        db.add(
            SessionRecord(
                id=token,
                user_id=user.id,
                csrf_token=csrf,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        db.commit()
        db.execute(text("ANALYZE emails"))
        db.commit()

        # Capture plain values: the ORM instances are detached once the
        # `with` block closes.
        seeded = {
            "user_id": str(user.id),
            "mailbox_id": str(mailbox.id),
            "account_email": user.email,
        }

    payload = {
        "session_token": token,
        "csrf_token": csrf,
        **seeded,
        "messages_indexed": indexed,
        "corpus_size": args.size,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


class tempfile_out:
    def __enter__(self):
        import tempfile

        self._handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        return self._handle

    def __exit__(self, *exc):
        import os as _os

        self._handle.close()
        try:
            _os.unlink(self._handle.name)
        except OSError:
            pass


if __name__ == "__main__":
    main()
