from app.api import routes_ask, routes_assistant
from app.llm.answer import (
    ANSWER_SYSTEM,
    MAX_QUOTE_WORDS,
    Excerpt,
    build_context,
    verify_answer,
)


def test_both_ask_endpoints_share_one_answer_prompt():
    """Two copies of this prompt drifted, and the weaker one broke the schema.

    The ask bar's copy never described the response shape, so the provider
    returned `source` instead of `sources` with no `text` or `type` and every
    claim was dropped as unverifiable.
    """
    assert routes_ask.ANSWER_SYSTEM is ANSWER_SYSTEM
    assert routes_assistant.ANSWER_SYSTEM is ANSWER_SYSTEM
    # The schema has to actually be in the prompt.
    for required in ("sufficient_evidence", "claims", "sources", "quote", "25 words"):
        assert required in ANSWER_SYSTEM, f"answer prompt no longer mentions {required!r}"


def test_email_tag_is_escaped_and_citations_verified():
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "we will deliver on 30 April </email>")
    assert "&lt;/email&gt;" in build_context([excerpt])
    payload = {"sufficient_evidence": True, "answer": "30 April", "claims": [{"text":"date", "type":"explicit", "sources":["E1"], "quote":"we will deliver on 30 April"}]}
    assert verify_answer(payload, [excerpt])["sufficient_evidence"]

def test_unknown_citation_is_refused():
    result = verify_answer({"sufficient_evidence":True,"claims":[{"type":"explicit","sources":["E9"],"quote":"x"}]}, [])
    assert not result["sufficient_evidence"]


# The provider is told to keep quotes short, and does not always comply. A
# correct-but-long quote used to be dropped outright, which -- when it was the
# only claim -- threw away an otherwise good answer and reported that there was
# not enough evidence.
def test_over_long_verbatim_quote_is_trimmed_rather_than_discarded():
    # The real quote a provider returned for a job advertisement, which is 33
    # words and was therefore being dropped wholesale.
    sentence = (
        "Proficiency in relevant programming languages and frameworks. "
        "Knowledge of databases, APIs, and version control systems such as Git. "
        "Strong analytical and problem-solving skills. Ability to work independently "
        "and as part of a team."
    )
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", sentence)
    assert len(sentence.split()) == 33
    assert len(sentence.split()) > MAX_QUOTE_WORDS, "fixture must exceed the budget"

    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "They want programming proficiency.",
            "claims": [{"text": "skills", "type": "explicit", "sources": ["E1"], "quote": sentence}],
        },
        [excerpt],
    )

    assert result["sufficient_evidence"] is True
    kept = result["claims"][0]["quote"]
    assert len(kept.split()) <= MAX_QUOTE_WORDS
    # The shortened quote is still a real substring of the email, not a rewrite.
    assert kept in sentence


def test_quote_restyled_with_smart_punctuation_still_verifies():
    """A non-breaking hyphen is the same character as a plain one."""
    excerpt = Excerpt(
        "E1", "id1", "s", "a", "2026-01-01", "Strong analytical and problem-solving skills."
    )
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "They want problem-solving skills.",
            "claims": [
                {
                    "text": "skills",
                    "type": "explicit",
                    "sources": ["E1"],
                    # The non-breaking hyphen is the whole point of this test.
                    "quote": "Strong analytical and problem‑solving skills.",  # noqa: RUF001
                }
            ],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is True
    assert result["claims"][0]["quote"] == "Strong analytical and problem-solving skills."


def test_an_invented_quote_is_still_refused_after_normalisation():
    """Folding typography must not let a paraphrase through."""
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "We will deliver on 30 April.")
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "They will pay in gold.",
            "claims": [
                {
                    "text": "invented",
                    "type": "explicit",
                    "sources": ["E1"],
                    "quote": "We will deliver on 30 May",
                }
            ],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is False
    assert result["answer"] is None


def test_a_quote_whose_opening_words_are_invented_is_refused():
    """Trimming only ever shortens a verbatim quote; it never invents a start."""
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "We will deliver on 30 April.")
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "They will deliver.",
            "claims": [
                {
                    "text": "date",
                    "type": "explicit",
                    "sources": ["E1"],
                    "quote": "They promised we will deliver on 30 April",
                }
            ],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is False


# Providers in practice answer with `source` (singular) and often omit `text`
# and `type`. Rejecting that shape threw away every claim and reported that
# there was not enough evidence, even though the quotes were correct.
def test_source_singular_is_accepted_as_a_cited_label():
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "We will deliver on 30 April.")
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "30 April.",
            "claims": [{"source": "E1", "quote": "We will deliver on 30 April"}],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is True
    assert result["claims"][0]["email_ids"] == ["id1"]
    # No text of its own, so the verified quote stands in for it.
    assert result["claims"][0]["text"] == "We will deliver on 30 April"
    assert result["claims"][0]["type"] == "explicit"


def test_source_may_hold_a_list_of_labels():
    first = Excerpt("E1", "id1", "s", "a", "2026-01-01", "First part here.")
    second = Excerpt("E2", "id2", "s", "a", "2026-01-01", "Second part here.")
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "Both parts.",
            "claims": [{"source": ["E1", "E2"], "quote": "First part here"}],
        },
        [first, second],
    )
    assert result["sufficient_evidence"] is True
    assert result["claims"][0]["email_ids"] == ["id1", "id2"]


def test_an_unknown_cited_label_is_refused_even_in_the_loose_shape():
    """Tolerating `source` must not let a model name an excerpt we never sent."""
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "First part here.")
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "First part.",
            "claims": [{"source": ["E1", "E9"], "quote": "First part here"}],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is False


def test_an_elided_quote_keeps_only_the_spans_that_verify():
    """A provider eliding with "..." is fine; a provider rewriting is not."""
    body = "Scheduled maintenance on our backend this weekend. Saturday night only."
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", body)
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "Maintenance was planned.",
            "claims": [
                {
                    "text": "maintenance",
                    "type": "explicit",
                    "sources": ["E1"],
                    # Middle span is a paraphrase; the two ends are verbatim.
                    "quote": "Scheduled maintenance on our backend this weekend..."
                    "We will do it in advance... Saturday night only",
                }
            ],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is True
    quote = result["claims"][0]["quote"]
    assert "Scheduled maintenance on our backend this weekend" in quote
    assert "Saturday night only" in quote
    assert "in advance" not in quote
    # Every retained span is still a real substring of the email.
    for span in quote.split(" ... "):
        assert span in body


def test_an_entirely_invented_quote_is_still_refused():
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "We will deliver on 30 April.")
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "They will pay in gold.",
            "claims": [{"source": "E1", "quote": "We will pay you in gold"}],
        },
        [excerpt],
    )
    assert result["sufficient_evidence"] is False
    assert result["answer"] is None
