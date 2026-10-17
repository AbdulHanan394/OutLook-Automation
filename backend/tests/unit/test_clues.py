import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.retrieval.clues import SearchClues, add_soft_clues, extract_search_clues
from app.retrieval.parse import parse_query

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures" / "search_clues.json").read_text())


@pytest.mark.parametrize("fixture", FIXTURES)
def test_soft_clue_eval_fixtures_preserve_explicit_filters(fixture):
    from_date = datetime(2024, 1, 1, tzinfo=UTC)
    criteria = parse_query(fixture["question"], sender="alex@example.test", from_dt=from_date, folder="inbox")
    clues = extract_search_clues(fixture["payload"])
    augmented = add_soft_clues(criteria, clues)

    assert set(fixture["expected_terms"]).issubset(set(augmented.terms))
    assert augmented.sender == criteria.sender
    assert augmented.from_dt == from_date
    assert augmented.folder == "inbox"


def test_search_clue_output_cannot_set_hard_filters():
    with pytest.raises(ValueError, match="unexpected search clue fields"):
        extract_search_clues({"rewrites": ["budget"], "sender": "someone@example.test"})


def test_soft_clues_are_bounded_deduplicated_and_optional():
    clues = extract_search_clues({
        "rewrites": ["  budget forecast ", "budget forecast", " ".join(["long"] * 60)],
        "soft_clues": [],
    })
    assert clues == SearchClues(
        rewrites=["budget forecast", " ".join(["long"] * 60)[:160]],
        soft_clues=[],
    )
