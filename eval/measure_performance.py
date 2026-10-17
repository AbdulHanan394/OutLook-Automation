"""Measure the mail-client browsing endpoints against a real fixture mailbox.

Reports wall-clock timings for the OL1 targets -- folder open, first message page,
opening a message, and rendering its body -- using the same synthetic corpus the
evaluation harness scores against. Nothing here is a promise: the numbers are
only as good as the machine and the corpus that produced them, so every run
records the corpus size and the Postgres version alongside the timings.

Usage (from the repo root, with a migrated database)::

    TEST_DATABASE_URL=postgresql+psycopg://... \\
    DATABASE_URL=postgresql+psycopg://... \\
    python eval/measure_performance.py --size 5000
"""

import argparse
import json
import os
import statistics
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SESSION_SECRET", "measure-session-secret-0123456789abcdef")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "measure-token-key-0123456789abcdef")
os.environ.setdefault("MS_CLIENT_ID", "measure-client-id")
os.environ.setdefault("MS_CLIENT_SECRET", "measure-client-secret")
os.environ.setdefault("MS_TENANT", "organizations")

import sys  # noqa: E402

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import Email, MailboxConnection, SessionRecord, User, engine  # noqa: E402
from app.main import app  # noqa: E402
from generate_synthetic_mailbox import main as generate_fixture  # noqa: E402


def seed(db: Session, size: int) -> tuple[User, MailboxConnection, int]:
    """Load the synthetic Graph corpus through the real sync upsert."""
    from app.ingest.sync import upsert_message

    user = User(email=f"perf-{uuid.uuid4().hex[:8]}@example.test")
    db.add(user)
    db.flush()
    mailbox = MailboxConnection(
        user_id=user.id,
        ms_tenant_id="organizations",
        ms_user_id=f"ms-{uuid.uuid4().hex[:8]}",
        account_email=user.email,
        granted_scopes="Mail.Read",
        status="active",
        index_scope={"folders": ["inbox", "sentitems"], "lookback_days": 365},
    )
    db.add(mailbox)
    db.flush()

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        out = handle.name
    argv = sys.argv
    sys.argv = ["generate", "--size", str(size), "--out", out]
    try:
        generate_fixture()
    finally:
        sys.argv = argv
    corpus = json.loads(Path(out).read_text(encoding="utf-8"))["value"]

    indexed = 0
    for message in corpus:
        if upsert_message(
            db,
            user_id=user.id,
            mailbox_id=mailbox.id,
            folder_id="inbox",
            message=message,
            target_tokens=400,
            overlap_tokens=40,
        ):
            indexed += 1
    db.commit()
    Path(out).unlink(missing_ok=True)
    # Without fresh statistics the planner guesses that folder_alias='inbox'
    # matches about one row, and will happily pick the wrong index. Measuring a
    # plan the planner has not been given the facts to choose is meaningless.
    db.execute(text("ANALYZE emails"))
    db.commit()
    return user, mailbox, indexed


def time_calls(client, path: str, params: dict, repeats: int = 10) -> dict:
    timings: list[float] = []
    status = None
    for _ in range(repeats):
        started = time.perf_counter()
        response = client.get(path, params=params)
        timings.append((time.perf_counter() - started) * 1000)
        status = response.status_code
    if status != 200:
        return {"error": f"HTTP {status}"}
    timings.sort()
    return {
        "median_ms": round(statistics.median(timings), 1),
        "p95_ms": round(timings[int(len(timings) * 0.95) - 1], 1),
        "min_ms": round(timings[0], 1),
        "max_ms": round(timings[-1], 1),
        "runs": repeats,
    }


def explain(db: Session, sql: str, params: dict) -> str:
    rows = db.execute(text(sql), params).fetchall()
    # EXPLAIN returns one row per plan line; the whole tree is needed to tell
    # whether the 0010 index was actually chosen.
    return "\n".join(str(row[0]) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    with Session(engine) as db:
        db.execute(text("SET app.user_id = '00000000-0000-0000-0000-000000000000'"))
        user, mailbox, indexed = seed(db, args.size)
        db.execute(text(f"SET app.user_id = '{user.id}'"))

        token = f"perf-{uuid.uuid4().hex}"
        db.add(SessionRecord(
            id=token,
            user_id=user.id,
            csrf_token="perf",
            expires_at=datetime.now(UTC).replace(year=datetime.now(UTC).year + 1),
        ))
        db.commit()

        with TestClient(app) as client:
            client.cookies.set("email_finder_session", token)
            base = {"mailbox_id": str(mailbox.id)}

            folders = client.get("/api/mail/folders", params=base)
            first_page = client.get(
                "/api/mail/messages", params={**base, "folder": "inbox", "limit": 50}
            )
            sample_id = first_page.json()["messages"][0]["id"] if first_page.status_code == 200 else ""

            results = {
                "corpus": {
                    "requested_size": args.size,
                    "messages_indexed_this_run": indexed,
                    "folders": folders.json() if folders.status_code == 200 else None,
                    "generated_at": datetime.now(UTC).isoformat(),
                },
                "postgres": db.execute(text("SHOW server_version")).scalar(),
                "timings": {
                    "folder_open": time_calls(client, "/api/mail/folders", base, args.repeats),
                    "first_page_50": time_calls(
                        client, "/api/mail/messages",
                        {**base, "folder": "inbox", "limit": 50}, args.repeats,
                    ),
                    "page_100": time_calls(
                        client, "/api/mail/messages",
                        {**base, "folder": "inbox", "limit": 100}, args.repeats,
                    ),
                    "message_detail": time_calls(
                        client, f"/api/mail/messages/{sample_id}", base, args.repeats
                    ),
                    "message_render": time_calls(
                        client, f"/api/mail/messages/{sample_id}/render", base, args.repeats
                    ),
                },
            }

            # Prove the list query is served by the 0010 index and not a sort.
            plan = explain(
                db,
                """
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT * FROM emails
                WHERE user_id = :uid AND mailbox_connection_id = :mid
                  AND folder_alias = 'inbox' AND is_deleted = false
                ORDER BY received_at DESC, id DESC
                LIMIT 50
                """,
                {"uid": user.id, "mid": mailbox.id},
            )
            results["list_query_plan"] = {
                "uses_folder_list_index": "emails_folder_list_idx" in plan,
                "sequential_scan": "Seq Scan" in plan,
                "plan": [line.strip() for line in plan.splitlines() if line.strip()][:8],
            }

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()