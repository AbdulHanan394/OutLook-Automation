"""Offline scoring for find/ask quality.

This replaces the previous five-line stub, which counted the queries file and
printed a note saying to connect the pipeline before scoring anything. There was
no recorded baseline anywhere -- ``docs/EVALUATION.md`` said so explicitly -- so
the first thing this does is establish one against the *existing*
``/api/search/email`` behaviour, then measure the Ask bar's find path on the same
corpus so the two numbers are comparable.

Reported:

* top-1 / top-3 / top-5 recall and MRR
* returned-nothing count
* confidence distribution, and a false-confident rate: how often the pipeline
  claimed high or low confidence while the right answer was not in the list. For
  a tool that answers "which email am I thinking of", that number matters more
  than recall.

Usage (repo root, migrated database)::

    DATABASE_URL=postgresql+psycopg://... \\
    python eval/run_eval.py --pipeline existing --out eval/baseline.json
    python eval/run_eval.py --pipeline ask-bar  --out eval/ask-bar.json
"""

import argparse
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

BACKEND = Path(__file__).resolve().parent.parent / "backend"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SESSION_SECRET", "eval-session-secret-0123456789abcdef")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "eval-token-key-0123456789abcdef")
os.environ.setdefault("MS_CLIENT_ID", "eval-client-id")
os.environ.setdefault("MS_CLIENT_SECRET", "eval-client-secret")
os.environ.setdefault("MS_TENANT", "organizations")

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Email, MailboxConnection, User, engine, set_user_scope  # noqa: E402
from app.retrieval.parse import parse_query  # noqa: E402
from app.retrieval.pipeline import keyword_search  # noqa: E402

DEFAULT_TOPK = (1, 3, 5)


@dataclass
class Report:
    queries: int = 0
    hits: dict[int, int] = field(default_factory=dict)
    reciprocal_rank_sum: float = 0.0
    no_result: int = 0
    confidence_counts: dict[str, int] = field(default_factory=dict)
    false_confident: int = 0
    judged_confidence: int = 0
    per_query: list[dict[str, Any]] = field(default_factory=list)

    def record(self, ranked: list[str], confidence: str, correct: set[str], query: str) -> None:
        self.queries += 1
        self.confidence_counts[confidence] = self.confidence_counts.get(confidence, 0) + 1

        position = next((i for i, item in enumerate(ranked) if item in correct), None)
        if not ranked:
            self.no_result += 1
        if position is not None:
            self.reciprocal_rank_sum += 1 / (position + 1)
            for k in DEFAULT_TOPK:
                if position < k:
                    self.hits[k] = self.hits.get(k, 0) + 1

        if confidence in {"high", "low"}:
            self.judged_confidence += 1
            if position is None or position >= 3:
                self.false_confident += 1

        self.per_query.append(
            {
                "query": query,
                "rank": position + 1 if position is not None else None,
                "confidence": confidence,
                "returned": len(ranked),
            }
        )

    def summary(self) -> dict[str, Any]:
        n = max(self.queries, 1)
        return {
            "queries": self.queries,
            **{f"top{k}_recall": round(self.hits.get(k, 0) / n, 4) for k in DEFAULT_TOPK},
            "mrr": round(self.reciprocal_rank_sum / n, 4),
            "returned_nothing": self.no_result,
            "confidence_counts": self.confidence_counts,
            "false_confident_rate": (
                round(self.false_confident / self.judged_confidence, 4)
                if self.judged_confidence
                else None
            ),
        }


def load_queries(path: Path, feature: str | None) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [row for row in rows if not feature or row.get("feature") == feature]


def build_corpus(db: Session, size: int) -> tuple[UUID, UUID, dict[str, str]]:
    """Load the synthetic corpus through the real sync upsert.

    Returns the fixture ``graph_message_id`` -> database UUID map, which is what
    lets a query file's ``correct_email_ids`` be resolved.
    """
    from app.ingest.sync import upsert_message
    from generate_synthetic_mailbox import main as generate_fixture

    user = User(email=f"eval-{uuid.uuid4().hex[:8]}@example.test")
    db.add(user)
    db.flush()
    mailbox = MailboxConnection(
        user_id=user.id,
        ms_tenant_id="organizations",
        ms_user_id="eval",
        account_email=user.email,
        granted_scopes="Mail.Read",
        status="active",
        index_scope={"folders": ["inbox", "sentitems", "archive"]},
    )
    db.add(mailbox)
    db.flush()
    set_user_scope(db, user.id)

    # Closed before use: on Windows an open handle cannot be deleted or rewritten
    # by the generator.
    handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    corpus_path = handle.name
    handle.close()
    argv = sys.argv
    sys.argv = ["generate", "--size", str(size), "--out", corpus_path]
    try:
        generate_fixture()
    finally:
        sys.argv = argv
    corpus = json.loads(Path(corpus_path).read_text(encoding="utf-8"))["value"]
    Path(corpus_path).unlink(missing_ok=True)

    for message in corpus:
        upsert_message(
            db,
            user_id=user.id,
            mailbox_id=mailbox.id,
            folder_id=message.get("parentFolderId", "inbox"),
            message=message,
            target_tokens=400,
            overlap_tokens=40,
        )
    db.commit()

    mapping = {
        email.graph_message_id: str(email.id)
        for email in db.query(Email).filter(Email.user_id == user.id).all()
    }
    # Without fresh statistics the planner guesses folder selectivity wrong.
    db.execute(text("ANALYZE emails"))
    db.commit()
    return user.id, mailbox.id, mapping


def score(
    db: Session,
    queries: list[dict],
    mapping: dict[str, str],
    *,
    user_id: UUID,
    mailbox_id: UUID,
    pipeline: str,
) -> Report:
    from app.retrieval.intent_search import find_with_intent

    settings = get_settings()
    report = Report()
    set_user_scope(db, user_id)

    for query in queries:
        correct = {
            mapping[item]
            for item in [*query.get("correct_email_ids", []), *query.get("acceptable_ids", [])]
            if item in mapping
        }
        if not correct:
            # A query whose expected email is not in the corpus cannot be scored.
            continue

        text_query = query["query"]
        if pipeline == "existing":
            criteria = parse_query(text_query)
            status, results, _ = keyword_search(
                db,
                user_id=user_id,
                mailbox_id=mailbox_id,
                criteria=criteria,
                limit=settings.max_alternatives,
                min_score=settings.conf_min_score,
                margin=settings.conf_margin,
            )
            # Mirror what POST /api/search/email actually returns to the reader:
            # high -> the single best match, low -> alternatives, none -> nothing
            # at all (best_match and alternatives are both null). Scoring the raw
            # internal result list instead would credit the baseline with hits a
            # user never sees.
            if status == "high":
                ranked = [str(results[0].id)] if results else []
            elif status == "low":
                ranked = [str(email.id) for email in results]
            else:
                ranked = []
            confidence = status
        else:
            # client=None: the Ask bar's behaviour with no AI consent, which is
            # the mode this harness can measure deterministically.
            rows, confidence, _ = find_with_intent(
                db,
                user_id=user_id,
                mailbox_id=mailbox_id,
                raw_query=text_query,
                settings=settings,
                client=None,
            )
            ranked = [row["email_id"] for row in rows]

        report.record(ranked, confidence, correct, text_query)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Score find quality against a fixture corpus.")
    parser.add_argument("--queries", default=str(Path(__file__).parent / "queries.jsonl"))
    parser.add_argument("--size", type=int, default=200, help="corpus size")
    parser.add_argument("--feature", default="find", choices=["find", "ask"])
    parser.add_argument("--pipeline", default="ask-bar", choices=["existing", "ask-bar"])
    parser.add_argument("--limit", type=int, default=0, help="0 = all")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    queries = load_queries(Path(args.queries), args.feature)
    if args.limit:
        queries = queries[: args.limit]
    if not queries:
        raise SystemExit(f"no {args.feature} queries found in {args.queries}")

    with Session(engine) as db:
        set_user_scope(db, uuid.UUID(int=0))
        user_id, mailbox_id, mapping = build_corpus(db, args.size)
        report = score(
            db, queries, mapping, user_id=user_id, mailbox_id=mailbox_id, pipeline=args.pipeline
        )

    settings = get_settings()
    payload = {
        "pipeline": args.pipeline,
        "feature": args.feature,
        "corpus_size": args.size,
        "settings": {
            "max_alternatives": settings.max_alternatives,
            "conf_margin": settings.conf_margin,
            "conf_min_score": settings.conf_min_score,
        },
        **report.summary(),
        "per_query": report.per_query,
    }
    rendered = json.dumps(payload, indent=2)
    print(rendered)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()