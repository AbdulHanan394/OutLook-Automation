from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Column, MetaData, String, Table, Text, create_engine, text
from sqlalchemy.orm import Session

from app.db import Base, Email
from app.retrieval.confidence import confidence
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.parse import parse_query, required_query_terms
from app.retrieval.pipeline import feedback_tie_break, token_similarity


def test_parse_preserves_phrases_without_ai():
    criteria = parse_query('"revised budget" deadline', sender="manager")
    assert criteria.phrases == ["revised budget"]
    assert criteria.terms == ["deadline"]
    assert criteria.is_sufficient()

def test_required_terms_ignore_search_instructions_but_keep_topic_terms():
    assert required_query_terms("Show me emails about the team budget") == (["team", "budget"], [])

def test_rrf_rewards_agreement():
    ranked = reciprocal_rank_fusion([["target", "other"], ["target"]])
    assert ranked[0][0] == "target"

def test_confidence_never_claims_exact():
    assert confidence([], min_score=.01, margin=.01, agreeing_retrievers=0) == "none"
    assert confidence([.1, .095], min_score=.01, margin=.01, agreeing_retrievers=2) == "low"

def test_token_similarity_identifies_related_rejected_messages():
    assert token_similarity("Budget review and forecast", "Budget forecast review") == 0.75
    assert token_similarity("Different topic", "Unrelated message") == 0

def test_feedback_only_breaks_ties_and_never_changes_scores():
    ranked = [("negative", 0.1), ("positive", 0.1), ("higher-score", 0.101)]
    result = feedback_tie_break(
        ranked,
        {"negative": -500, "positive": 500, "higher-score": 500},
        0.005,
    )
    assert result == [("higher-score", 0.101), ("positive", 0.1), ("negative", 0.1)]

def test_refinement_excludes_rejected_email_and_older_hint_uses_its_date():
    from app.retrieval.pipeline import keyword_search

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Email.__table__])
    attachment_metadata = MetaData()
    Table(
        "attachments",
        attachment_metadata,
        Column("id", String, primary_key=True),
        Column("user_id", String),
        Column("mailbox_connection_id", String),
        Column("email_id", String),
        Column("filename", Text),
        Column("file_type", String),
    )
    Table(
        "email_chunks",
        attachment_metadata,
        Column("user_id", String),
        Column("mailbox_connection_id", String),
        Column("email_id", String),
        Column("attachment_id", String),
        Column("source", String),
        Column("chunk_text", Text),
    )
    attachment_metadata.create_all(engine)
    user_id, mailbox_id = uuid4(), uuid4()
    rejected = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="rejected", folder_id="inbox", subject="Launch planning notes",
        body_text="The launch planning meeting notes.", body_preview="Launch notes",
        received_at=datetime(2024, 5, 2, tzinfo=UTC), content_hash="rejected",
        created_at=datetime(2024, 5, 2, tzinfo=UTC),
        updated_at=datetime(2024, 5, 2, tzinfo=UTC),
    )
    older = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="older", folder_id="inbox", subject="Launch planning notes",
        body_text="Earlier launch notes.", body_preview="Earlier notes",
        received_at=datetime(2024, 5, 1, tzinfo=UTC), content_hash="older",
        created_at=datetime(2024, 5, 1, tzinfo=UTC),
        updated_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    newer = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="newer", folder_id="inbox", subject="Launch planning notes",
        body_text="Later launch notes.", body_preview="Later notes",
        received_at=datetime(2024, 5, 3, tzinfo=UTC), content_hash="newer",
        created_at=datetime(2024, 5, 3, tzinfo=UTC),
        updated_at=datetime(2024, 5, 3, tzinfo=UTC),
    )
    criteria = parse_query("launch notes")

    with Session(engine) as db:
        db.add_all([rejected, older, newer])
        db.commit()
        _, results, _ = keyword_search(
            db, user_id, mailbox_id, criteria, 10, 0.01, 0.015,
            excluded_ids={rejected.id},
            received_before=rejected.received_at,
        )

    assert [email.id for email in results] == [older.id]
    engine.dispose()


def test_attachment_only_match_returns_parent_and_honors_file_type_and_user():
    from app.retrieval.pipeline import keyword_search

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Email.__table__])
    attachment_metadata = MetaData()
    Table(
        "attachments",
        attachment_metadata,
        Column("id", String, primary_key=True),
        Column("user_id", String),
        Column("mailbox_connection_id", String),
        Column("email_id", String),
        Column("filename", Text),
        Column("file_type", String),
    )
    Table(
        "email_chunks",
        attachment_metadata,
        Column("user_id", String),
        Column("mailbox_connection_id", String),
        Column("email_id", String),
        Column("attachment_id", String),
        Column("source", String),
        Column("chunk_text", Text),
    )
    attachment_metadata.create_all(engine)
    user_id, mailbox_id = uuid4(), uuid4()
    email = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="attachment-parent", folder_id="inbox", subject="Quarterly report",
        body_text="Please review the attached file.", body_preview="Review attached file",
        received_at=datetime(2024, 5, 2, tzinfo=UTC), content_hash="attachment-parent",
        created_at=datetime(2024, 5, 2, tzinfo=UTC),
        updated_at=datetime(2024, 5, 2, tzinfo=UTC),
    )
    partial_email = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="partial", folder_id="inbox", subject="Blue product update",
        body_text="The blue version is ready.", body_preview="Blue version ready",
        received_at=datetime(2024, 5, 3, tzinfo=UTC), content_hash="partial",
        created_at=datetime(2024, 5, 3, tzinfo=UTC),
        updated_at=datetime(2024, 5, 3, tzinfo=UTC),
    )
    team_email = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="team", folder_id="inbox", subject="Team project update",
        body_text="The team reviewed the project.", body_preview="Team reviewed project",
        received_at=datetime(2024, 5, 4, tzinfo=UTC), content_hash="team",
        created_at=datetime(2024, 5, 4, tzinfo=UTC),
        updated_at=datetime(2024, 5, 4, tzinfo=UTC),
    )
    substring_email = Email(
        id=uuid4(), user_id=user_id, mailbox_connection_id=mailbox_id,
        graph_message_id="steam", folder_id="inbox", subject="Steam engine update",
        body_text="The steam engine is ready.", body_preview="Steam engine ready",
        received_at=datetime(2024, 5, 5, tzinfo=UTC), content_hash="steam",
        created_at=datetime(2024, 5, 5, tzinfo=UTC),
        updated_at=datetime(2024, 5, 5, tzinfo=UTC),
    )
    attachment_id, other_user_id = uuid4(), uuid4()
    with Session(engine) as db:
        db.add_all([email, partial_email, team_email, substring_email])
        db.flush()
        db.execute(text(
            "INSERT INTO attachments (id, user_id, mailbox_connection_id, email_id, filename, file_type) "
            "VALUES (:id, :user_id, :mailbox_id, :email_id, :filename, :file_type)"
        ), {
            "id": attachment_id.hex, "user_id": user_id.hex, "mailbox_id": mailbox_id.hex,
            "email_id": email.id.hex, "filename": "budget.pdf", "file_type": "pdf",
        })
        db.execute(text(
            "INSERT INTO email_chunks (user_id, mailbox_connection_id, email_id, attachment_id, source, chunk_text) "
            "VALUES (:user_id, :mailbox_id, :email_id, :attachment_id, 'attachment', :chunk_text)"
        ), {
            "user_id": user_id.hex, "mailbox_id": mailbox_id.hex, "email_id": email.id.hex,
            "attachment_id": attachment_id.hex, "chunk_text": "Attachment: budget.pdf\n\nForecast for blue widget is 48 units.",
        })
        db.commit()
        criteria = parse_query('"blue widget"')
        _, results, reasons = keyword_search(db, user_id, mailbox_id, criteria, 10, 0.01, 0.015)
        assert [result.id for result in results] == [email.id]
        assert any("budget.pdf" in reason.detail for reason in reasons[email.id])

        _, team_results, _ = keyword_search(
            db, user_id, mailbox_id, parse_query("team"), 10, 0.01, 0.015,
        )
        assert [result.id for result in team_results] == [team_email.id]

        _, strict_results, _ = keyword_search(
            db, user_id, mailbox_id, parse_query("blue widget"), 10, 0.01, 0.015,
        )
        assert [result.id for result in strict_results] == [email.id]

        _, pdf_filtered, _ = keyword_search(
            db, user_id, mailbox_id, criteria, 10, 0.01, 0.015, file_types=["pdf"],
        )
        assert [result.id for result in pdf_filtered] == [email.id]

        _, filtered, _ = keyword_search(
            db, user_id, mailbox_id, criteria, 10, 0.01, 0.015, file_types=["docx"],
        )
        assert filtered == []

        _, isolated, _ = keyword_search(
            db, other_user_id, mailbox_id, criteria, 10, 0.01, 0.015,
        )
        assert isolated == []
    engine.dispose()
