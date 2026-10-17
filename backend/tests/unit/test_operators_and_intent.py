"""Operator parsing, intent classification, and the safety barrier around both.

These are pure-function tests: no database, no model. They are the fastest
possible guard on the rule that makes the Ask bar trustworthy -- a typed operator
must never become a search term, and a model must never be able to invent a hard
filter.
"""

import pytest

from app.llm.intent import (
    classify_by_rule,
    clean_clues,
    extract_intent,
    interpretation_to_json,
    keyword_only,
)
from app.retrieval.clues import extract_search_clues
from app.retrieval.operators import (
    date_window,
    describe,
    parse_date,
    parse_operators,
    sender_matches,
)
from app.retrieval.parse import parse_query

# --------------------------------------------------------------------------- #
# operators
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected_free_text"),
    [
        ("from:sara@example.com", ""),
        ("has:attachment", ""),
        ("is:unread", ""),
        ("from:sara@example.com has:attachment", ""),
        ("is:unread folder:sentitems", ""),
        ('subject:"quarterly newsletter" before:2026-04-01', ""),
        ("budget approval from:sara", "budget approval"),
        ('from:"Sara Ahmed" march budget', "march budget"),
    ],
)
def test_operators_are_removed_from_the_text_that_gets_searched(raw, expected_free_text):
    """The core rule: operators are lifted out before tokenization.

    Without this the tokenizer emits "from", "has" and "attachment" as mandatory
    lexical terms, because its character class excludes a colon. Every result is
    then ranked against words the reader never meant as content.
    """
    assert parse_operators(raw).free_text == expected_free_text


def test_operator_words_never_leak_into_search_terms():
    raw = "from:sara@example.com has:attachment"
    assert "from" not in parse_query(parse_operators(raw).free_text).terms
    assert parse_query(raw).terms  # the naive path really would have included them
    assert "from" in parse_query(raw).terms


def test_multiple_operators_all_apply():
    ops = parse_operators("from:sara is:unread has:attachment folder:inbox after:2026-03-01")
    assert ops.senders == ["sara"]
    assert ops.is_unread is True
    assert ops.has_attachment is True
    assert ops.folders == ["inbox"]
    assert ops.after == parse_date("2026-03-01")
    assert ops.free_text == ""


def test_quoted_sender_loses_its_quotes():
    assert parse_operators('from:"Sara Ahmed"').senders == ["Sara Ahmed"]


def test_unsupported_operator_values_are_reported_not_guessed():
    """An unparseable date must not be turned into a silent guess."""
    ops = parse_operators("after:tomorrow has:sparkles")
    assert ops.unknown == ["after:tomorrow", "has:sparkles"]
    assert ops.after is None


def test_is_read_is_the_negation_of_is_unread():
    assert parse_operators("is:read").is_unread is False
    assert parse_operators("is:unread").is_unread is True


def test_plain_keywords_have_no_operators():
    ops = parse_operators("the email about the march budget")
    assert ops.any is False
    assert ops.free_text == "the email about the march budget"


def test_before_is_a_half_open_window():
    """``before:D`` means up to the start of D, which is what a reader expects."""
    start, end = date_window(parse_operators("before:2026-04-01"))
    assert start is None
    assert end is not None and end.day == 1


def test_chips_describe_every_applied_operator():
    chips = describe(parse_operators("from:sara has:attachment is:unread"))
    kinds = {chip["kind"] for chip in chips}
    assert {"sender", "attachment", "unread"} <= kinds
    assert all(chip["guessed"] is False for chip in chips), (
        "typed operators are hard filters and must never be labelled guessed"
    )


def test_sender_matches_name_or_address():
    assert sender_matches("sara@example.test", "sara")
    assert sender_matches("Sara Ahmed", "sara ahmed")
    assert sender_matches("Sara Ahmed <sara@example.test>", "sara@")
    assert not sender_matches(None, "sara")
    assert not sender_matches("bob@example.test", "sara@example.test")


# --------------------------------------------------------------------------- #
# intent classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("what did the client say about the deadline?", "ask"),
        ("who approved the budget?", "ask"),
        ("summarize emails about the marketing campaign", "ask"),
        ("how many emails did Sara send?", "ask"),
        ("find the email from Sara about the March budget", "find"),
        ("show me emails about hiring", "find"),
        ("the email from Sara about the March budget", "find"),
    ],
)
def test_intent_is_classified_without_a_model(query, expected):
    ops = parse_operators(query)
    assert classify_by_rule(query, ops, "auto") == expected


def test_explicit_mode_overrides_the_rules():
    ops = parse_operators("what is this from: sara")
    assert classify_by_rule("what is this from: sara", ops, "find") == "find"
    assert classify_by_rule("find the thing", ops, "ask") == "ask"


def test_ambiguous_prose_defers_to_the_model():
    """Only genuinely ambiguous text may cost a model call."""
    assert classify_by_rule("sara budget", parse_operators("sara budget"), "auto") is None


def test_operators_alone_never_need_a_model():
    ops = parse_operators("from:sara has:attachment")
    assert classify_by_rule("from:sara has:attachment", ops, "auto") == "find"
    assert keyword_only("from:sara has:attachment", ops).source == "operator"


# --------------------------------------------------------------------------- #
# the injection barrier
# --------------------------------------------------------------------------- #


def test_interpreter_cannot_invent_a_hard_filter():
    """Same barrier as the Find pipeline: unknown keys are a hard error."""
    with pytest.raises(ValueError, match="unexpected interpretation fields"):
        extract_intent({"intent": "find", "sender": "attacker@example.test"})

    with pytest.raises(ValueError, match="unexpected interpretation fields"):
        extract_intent({"intent": "find", "folder": "inbox"})


def test_the_existing_find_barrier_is_untouched():
    """Guards against someone widening extract_search_clues to satisfy intent."""
    with pytest.raises(ValueError, match="unexpected search clue fields"):
        extract_search_clues({"rewrites": ["budget"], "sender": "someone@example.test"})


def test_unusable_intent_value_falls_back_to_find():
    intent, _, _ = extract_intent({"intent": "delete_everything"})
    assert intent == "find"


def test_clues_are_length_bounded_and_deduplicated():
    clues = clean_clues(["  budget  ", "budget", "x" * 400, 42])
    assert clues[0] == "budget"
    assert clues.count("budget") == 1
    assert all(len(value) <= 160 for value in clues)


def test_interpretation_payload_carries_only_typed_filters():
    ops = parse_operators("from:sara has:attachment")
    payload = interpretation_to_json(keyword_only("from:sara has:attachment", ops))
    assert payload["intent"] == "find"
    assert payload["used_model"] is False
    assert payload["filters"]["sender"] == "sara"
    assert payload["filters"]["has_attachment"] is True
    assert payload["filters"]["unread"] is None
    assert {chip["kind"] for chip in payload["chips"]} == {"sender", "attachment"}


def test_provider_failure_degrades_to_keyword_search_without_raising():
    """No consent, no client, or a provider error must not fail the request."""
    from app.llm.intent import interpret

    class Broken:
        def json_message(self, *args, **kwargs):
            raise RuntimeError("provider down")

    # Deliberately ambiguous, so the model really is attempted.
    query = "sara budget approval"
    result = interpret(query, operators=parse_operators(query), mode="auto", client=Broken())
    assert result.used_model is False
    assert result.fallback_notice
    assert "keyword" in result.fallback_notice.lower()

    # No client at all (no consent / cap reached) takes the same route.
    without = interpret(query, operators=parse_operators(query), mode="auto", client=None)
    assert without.used_model is False
    assert without.fallback_notice


def test_interpret_uses_the_model_only_when_ambiguous():
    from app.llm.intent import interpret

    calls: list[str] = []

    class Counting:
        def json_message(self, system: str, prompt: str, tokens: int):
            calls.append(prompt)
            return {"intent": "ask", "rewrites": ["deadline"], "soft_clues": ["client"]}, 1, 1

    ops = parse_operators("sara budget")
    interpret("sara budget", operators=ops, mode="auto", client=Counting())
    assert len(calls) == 1
    
    calls.clear()
    # "what is this" is settled by the rules as a question; the ask path does
    # its own keyword extraction, so interpreting here would waste a call.
    interpret("what is this", operators=parse_operators("what is this"), mode="auto", client=Counting())
    assert calls == [], "an unambiguous question must not spend a model call"
