"""The shared relevance rule: ordering, duplicates and the cutoff.

Every result list in the product -- operator results, keyword finds and the
emails listed beside an answer -- is shaped by :func:`rank_rows`. These tests
pin the three decisions it makes, because the bugs they prevent were all of the
same kind: a list that *looked* like results and contained things that had
nothing to do with the question.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.retrieval.relevance import (
    RELEVANCE_CUTOFF,
    apply_relevance_cutoff,
    collapse_duplicate_rows,
    duplicate_key,
    lexical_score,
    rank_rows,
)


def message(
    subject: str = "",
    body: str = "",
    *,
    content_hash: str | None = None,
    identifier: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier or str(uuid4()),
        subject=subject,
        body_text=body,
        body_preview=body,
        normalized_content_hash=content_hash,
    )


# --------------------------------------------------------------------------- #
# the cutoff
# --------------------------------------------------------------------------- #


def test_cutoff_keeps_the_top_score_whatever_the_units():
    """Relative, not absolute: 0.19 of a top score of 0.4 fails the same test
    that 19.0 of a top score of 40.0 fails."""
    assert RELEVANCE_CUTOFF == 0.5
    assert apply_relevance_cutoff([("a", 0.4), ("b", 0.19)], lambda item: item[1]) == [("a", 0.4)]
    assert apply_relevance_cutoff([("a", 40.0), ("b", 19.0)], lambda item: item[1]) == [("a", 40.0)]
    # Exactly half is a pass: the cutoff is inclusive, so a tie is never lost.
    assert apply_relevance_cutoff([("a", 4.0), ("b", 2.0)], lambda item: item[1]) == [
        ("a", 4.0),
        ("b", 2.0),
    ]


def test_cutoff_preserves_the_order_it_was_given():
    rows = [("a", 3.0), ("b", 1.6), ("c", 0.1)]
    assert apply_relevance_cutoff(rows, lambda item: item[1]) == [("a", 3.0), ("b", 1.6)]


def test_cutoff_never_leaves_an_empty_list_or_singletons_lonely():
    assert apply_relevance_cutoff([("a", 5.0)], lambda item: item[1]) == [("a", 5.0)]
    assert apply_relevance_cutoff([], lambda item: item[1]) == []


def test_cutoff_keeps_everything_when_nothing_matched():
    """No score to compare against means no basis for dropping anything."""
    assert apply_relevance_cutoff([("a", 0.0), ("b", 0.0)], lambda item: item[1]) == [
        ("a", 0.0),
        ("b", 0.0),
    ]


def test_rank_rows_drops_weak_rows_below_half_the_top_score():
    strong = message("Revised budget", "Approved by the finance team.")
    medium = message("Budget", "Mentioned once.")
    weak = message("Lunch", "Shall we get lunch?")

    ranked = rank_rows([weak, medium, strong], terms=["budget", "approved"], phrases=[])

    # Strong scores 3 + 1 = 4; medium scores 3; weak scores 0 and is not within
    # half of 4.
    assert ranked == [strong, medium]


def test_rank_rows_keeps_a_weak_row_when_it_is_all_there_is():
    only = message("Lunch", "Shall we get lunch?")

    assert rank_rows([only], terms=["lunch"], phrases=[]) == [only]


def test_rank_rows_returns_nothing_when_the_query_words_match_nothing():
    """The query was answered with words; if no message contains them, saying so
    is the result. Listing every message in the mailbox at score zero is the bug
    this closes."""
    rows = [message("Lunch"), message("Standup notes")]

    assert rank_rows(rows, terms=["budget", "deadline"], phrases=[]) == []


def test_rank_rows_keeps_every_operator_hit_when_there_are_no_words_to_compare():
    """``from:sara`` is already fully answered by SQL; nothing is more relevant
    than anything else, so the cutoff must not quietly shrink the list."""
    rows = [message("Lunch"), message("Standup"), message("Budget")]

    assert rank_rows(rows, terms=[], phrases=[]) == rows


# --------------------------------------------------------------------------- #
# ordering: cited first
# --------------------------------------------------------------------------- #


def test_cited_emails_lead_regardless_of_their_score():
    """A quoted message is evidence; the rest are suggestions."""
    strong = message("Revised budget", "Approved by finance.")
    cited = message("Notes", "One line mentioning the deadline.")
    assert lexical_score("Revised budget", "Approved by finance.", ["budget"], []) > lexical_score(
        "Notes", "One line mentioning the deadline.", ["deadline"], []
    )

    ranked = rank_rows([strong, cited], terms=["budget", "deadline"], phrases=[], cited={cited.id})

    assert ranked == [cited, strong]


def test_a_weak_citation_is_never_cut_off_by_the_cutoff():
    """The cutoff applies to the suggestions. Cutting a citation would hide the
    very passage the answer is resting on."""
    strong = message("Revised budget", "Approved by finance.")
    cited = message("Notes", "Unrelated looking body.")
    assert lexical_score("Revised budget", "Approved by finance.", ["budget"], []) == 3.0

    ranked = rank_rows([strong, cited], terms=["budget"], phrases=[], cited={cited.id})

    assert cited in ranked and strong in ranked


def test_cited_emails_are_ordered_by_score_among_themselves():
    low = message("Notes", "the deadline slipped")
    high = message("Revised budget deadline", "the deadline is 30 April")

    ranked = rank_rows([low, high], terms=["deadline"], phrases=[], cited={low.id, high.id})

    assert ranked == [high, low]


def test_nothing_is_cited_so_the_badge_stays_off_every_row():
    rows = [message("Revised budget", "Approved.")]

    ranked = rank_rows(rows, terms=["budget"], phrases=[], cited=set())

    assert ranked == rows


# --------------------------------------------------------------------------- #
# duplicates
# --------------------------------------------------------------------------- #


def test_same_subject_and_body_hash_collapse_to_one_row():
    first = message("Budget approval", "Approved on 12 March.", content_hash="h1")
    copy = message("Budget approval", "Approved on 12 March.", content_hash="h1")

    assert collapse_duplicate_rows([first, copy]) == [first]


def test_a_reply_that_reuses_the_subject_is_not_a_duplicate():
    original = message("Budget approval", "Approved on 12 March.", content_hash="h1")
    reply = message("Budget approval", "Thanks, one question about the split.", content_hash="h2")

    assert collapse_duplicate_rows([original, reply]) == [original, reply]


def test_the_same_body_under_a_different_subject_is_not_a_duplicate():
    """A forwarded copy is often re-titled. Only subject *and* body together
    mean the reader is looking at the same message twice."""
    original = message("Budget approval", "Approved on 12 March.", content_hash="h1")
    forwarded = message("Fwd: budget approval", "Approved on 12 March.", content_hash="h1")

    assert collapse_duplicate_rows([original, forwarded]) == [original, forwarded]


def test_duplicates_are_collapsed_without_a_stored_content_hash():
    """Plain-text rows that never went through ingest have no stored hash, so the
    key is computed from the body instead."""
    first = message("Budget approval", "Approved on 12 March.")
    copy = message("  BUDGET   approval ", "Approved   on 12 March.")

    assert duplicate_key(first) == duplicate_key(copy)
    assert collapse_duplicate_rows([first, copy]) == [first]


def test_rank_rows_keeps_the_cited_copy_of_a_duplicated_message():
    strong = message("Budget approval", "Approved on 12 March.", content_hash="h1")
    duplicate = message("Budget approval", "Approved on 12 March.", content_hash="h1")

    ranked = rank_rows([strong, duplicate], terms=["budget"], phrases=[], cited={duplicate.id})

    assert len(ranked) == 1
    assert ranked[0] is duplicate


def test_rank_rows_honours_the_limit_after_shaping():
    rows = [message(f"Subject {index}", "budget") for index in range(5)]

    assert len(rank_rows(rows, terms=["budget"], phrases=[], limit=3)) == 3


@pytest.mark.parametrize("rows", [[], [message("only")]])
def test_rank_rows_handles_degenerate_input(rows):
    assert isinstance(rank_rows(rows, terms=["budget"], phrases=[]), list)