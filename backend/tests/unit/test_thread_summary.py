from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.routes_search import EmailSummaryRequest, RefineRequest
from app.llm.answer import Excerpt, verify_answer
from app.llm.thread_summary import summary_group_key, summary_source_hash


def test_thread_summary_cache_keys_are_order_independent_and_invalidate_on_content_change():
    first = SimpleNamespace(id=uuid4(), content_hash="source-one")
    second = SimpleNamespace(id=uuid4(), content_hash="source-two")
    assert summary_group_key([first, second]) == summary_group_key([second, first])
    assert summary_source_hash([first, second]) == summary_source_hash([second, first])

    updated_second = SimpleNamespace(id=second.id, content_hash="source-updated")
    assert summary_group_key([first, second]) == summary_group_key([first, updated_second])
    assert summary_source_hash([first, second]) != summary_source_hash([first, updated_second])


def test_thread_summary_accepts_only_verified_citations():
    excerpts = [
        Excerpt(
            label="T1",
            email_id="email-1",
            subject="Project update",
            sender="Alex",
            date="2024-06-01",
            text="The review is scheduled for Thursday.",
        )
    ]
    valid = verify_answer({
        "sufficient_evidence": True,
        "answer": "The review is scheduled for Thursday.",
        "claims": [{
            "text": "Alex scheduled the review for Thursday.",
            "type": "explicit",
            "sources": ["T1"],
            "quote": "scheduled for Thursday",
        }],
    }, excerpts)
    invalid = verify_answer({
        "sufficient_evidence": True,
        "answer": "The review is scheduled for Friday.",
        "claims": [{
            "text": "The review is Friday.",
            "type": "explicit",
            "sources": ["T1"],
            "quote": "scheduled for Friday",
        }],
    }, excerpts)

    assert valid["sufficient_evidence"] is True
    assert valid["claims"][0]["email_ids"] == ["email-1"]
    assert invalid["sufficient_evidence"] is False


def test_email_summary_scope_and_refinement_request_validation():
    assert EmailSummaryRequest.model_validate({"scope": "email"}).scope == "email"
    assert EmailSummaryRequest.model_validate({"scope": "thread"}).scope == "thread"
    assert RefineRequest.model_validate({
        "hint": "from Alex",
        "reference_email_id": uuid4(),
    }).hint == "from Alex"

    with pytest.raises(ValueError):
        RefineRequest.model_validate({"hint": "from Alex"})
