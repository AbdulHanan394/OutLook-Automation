from app.llm.answer import Excerpt
from app.llm.timeline import build_timeline, is_timeline_question


def test_timeline_question_detection():
    assert is_timeline_question("Can you show me the timeline for the launch?")
    assert is_timeline_question("What happened with the launch?")
    assert not is_timeline_question("What did we decide about the launch?")


def test_timeline_uses_verified_quotes_and_sorts_by_email_date():
    excerpts = [
        Excerpt("E1", "later", "Later update", "Alex", "2026-03-12", "The team approved the design."),
        Excerpt("E2", "earlier", "Earlier update", "Sam", "2026-03-03", "The team proposed a new design."),
    ]
    result = {
        "sufficient_evidence": True,
        "claims": [
            {
                "text": "The team approved the design.",
                "type": "explicit",
                "email_ids": ["later"],
                "quote": "The team approved the design.",
            },
            {
                "text": "The team proposed a new design.",
                "type": "explicit",
                "email_ids": ["earlier"],
                "quote": "The team proposed a new design.",
            },
            {
                "text": "An unsupported event.",
                "type": "explicit",
                "email_ids": ["earlier"],
                "quote": "A quote that is not in the email.",
            },
        ],
    }

    timeline = build_timeline(result, excerpts)

    assert [event["email_id"] for event in timeline] == ["earlier", "later"]
    assert [event["date"] for event in timeline] == ["2026-03-03", "2026-03-12"]
    assert len(timeline) == 2
