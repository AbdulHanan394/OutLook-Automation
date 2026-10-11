from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.routes_search import serialize
from app.retrieval.parse import parse_query
from app.retrieval.pipeline import match_reasons


def test_match_reasons_identify_only_matching_search_lanes():
    email = SimpleNamespace(
        subject="Budget launch update",
        body_text="The launch date moved to Friday.",
        sender_name="Morgan Lee",
        sender_address="morgan@example.com",
    )
    criteria = parse_query(
        '"launch date" budget friday',
        sender="morgan@example.com",
        from_dt=datetime(2026, 1, 1, tzinfo=UTC),
    )

    reasons = match_reasons(email, criteria)

    assert [(reason.type, reason.detail) for reason in reasons] == [
        ("phrase", 'Includes the phrase "launch date".'),
        ("subject", 'The subject mentions "budget".'),
        ("phrase", 'The message mentions "friday".'),
        ("sender", 'Sender matches "morgan@example.com".'),
        ("date", "Matched the selected received-date filter."),
    ]


def test_match_reasons_do_not_invent_nonmatching_lanes():
    email = SimpleNamespace(
        subject="Travel plans",
        body_text="The hotel is booked.",
        sender_name="Taylor",
        sender_address="taylor@example.com",
    )

    assert match_reasons(email, parse_query("budget launch", sender="morgan")) == []


def test_search_result_serialization_includes_full_email_body():
    email_body = "Opening paragraph.\n\nA longer section of the full email."
    email = SimpleNamespace(
        id=uuid4(),
        subject="Project update",
        sender_name="Morgan",
        sender_address="morgan@example.test",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        body_preview="Opening paragraph.",
        body_text=email_body,
        web_link="https://outlook.example.test/message",
    )

    result = serialize(email, [])

    assert result["body"] == email_body


@pytest.mark.parametrize(
    ("subject", "body", "query", "sender", "from_dt", "expected"),
    [
        ("budget overview", "", "budget", None, None, ["subject"]),
        ("", "budget approved", "budget", None, None, ["phrase"]),
        ("launch date", "", '"launch date"', None, None, ["phrase"]),
        ("", "", "", "morgan", None, ["sender"]),
        ("", "", "", None, datetime(2026, 1, 1, tzinfo=UTC), ["date"]),
        ("launch plan", "friday confirmed", "launch friday", None, None, ["subject", "phrase"]),
        ("", "budget launch", "budget launch", None, None, ["phrase", "phrase"]),
        ("quarterly report", "", "quarterly", "morgan", None, ["subject", "sender"]),
        ("", "approved", "missing", "nobody", None, []),
        ("", "delivery next week", '"delivery next week"', None, None, ["phrase"]),
    ],
)
def test_ten_search_fixtures_report_only_matching_reasons(
    subject, body, query, sender, from_dt, expected
):
    email = SimpleNamespace(
        subject=subject,
        body_text=body,
        sender_name="Morgan Lee",
        sender_address="morgan@example.com",
    )

    criteria = parse_query(query, sender=sender, from_dt=from_dt)

    assert [reason.type for reason in match_reasons(email, criteria)] == expected
