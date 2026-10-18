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
        # Both terms are in the body, so they are one fact, not two chips.
        ("", "budget launch", "budget launch", None, None, ["phrase"]),
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


def test_subject_matches_collapse_into_one_chip():
    """Three subject words are one reason. Three near-identical sentences used
    to push the sender and date chips -- the ones that actually explained the
    result -- off the end of the five-reason cap."""
    email = SimpleNamespace(
        subject="Revised budget and launch timeline",
        body_text="Attached.",
        sender_name="Morgan Lee",
        sender_address="morgan@example.com",
    )

    reasons = match_reasons(email, parse_query("revised budget timeline", sender="morgan"))

    assert [(reason.type, reason.detail) for reason in reasons] == [
        ("subject", 'The subject mentions "revised", "budget", "timeline".'),
        ("sender", 'Sender matches "morgan".'),
    ]


def test_body_matches_collapse_into_one_chip_and_skip_subject_words():
    email = SimpleNamespace(
        subject="Budget review",
        body_text="The timeline slipped and the budget was re-checked.",
        sender_name="Morgan Lee",
        sender_address="morgan@example.com",
    )

    reasons = match_reasons(email, parse_query("budget timeline"))

    assert [(reason.type, reason.detail) for reason in reasons] == [
        # "budget" is reported against the subject only, not repeated here.
        ("subject", 'The subject mentions "budget".'),
        ("phrase", 'The message mentions "timeline".'),
    ]


@pytest.mark.parametrize(
    ("query", "offending"),
    [
        ("the budget", "the"),
        ("to find the budget", "find"),
        ("budget from sara", "from"),
        ("budget in the inbox", "in"),
        ("ask about the budget", "ask"),
        ("what did they say about the budget", "what"),
    ],
)
def test_reason_chips_never_cite_a_stop_word(query, offending):
    """A chip reading 'The message mentions "the"' is true of every email ever
    sent. A reason that cannot discriminate between rows is not a reason."""
    email = SimpleNamespace(
        subject="The budget",
        body_text="Ask us to find this in your inbox, from anyone.",
        sender_name="Morgan Lee",
        sender_address="morgan@example.com",
    )

    details = " ".join(reason.detail.lower() for reason in match_reasons(email, parse_query(query)))

    assert f'"{offending}"' not in details
    # The one word that carries the query is still reported.
    assert "budget" in details


def test_a_query_of_nothing_but_stop_words_produces_no_chips():
    email = SimpleNamespace(
        subject="The budget",
        body_text="Approved.",
        sender_name="Morgan Lee",
        sender_address="morgan@example.com",
    )

    assert match_reasons(email, parse_query("the a an of it is")) == []
