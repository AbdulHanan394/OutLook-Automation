"""One relevance rule for every list of emails the app shows.

Three surfaces return results -- the operator path, the keyword find path and the
AI answer path -- and each used to decide on its own what to keep. That produced
lists that disagreed with each other about the same question: the answer path
listed whatever retrieval returned, the find path listed up to five rows, and the
operator path listed up to twenty-five, with duplicate copies of the same message
sitting in more than one of them.

So the three decisions live here and every path calls them:

* **order** -- verified citations first, best-scoring first inside each group;
* **duplicates** -- one row per subject + body hash;
* **the cutoff** -- a row is listed only if it scores at least
  :data:`RELEVANCE_CUTOFF` of the best score in the same list.

The cutoff is relative rather than absolute on purpose. A fixed floor cannot
work for both "what did they say about the budget" (where a weak hit is still
worth seeing) and "from:sara" (where every row is equally wanted), but a
*fraction of the best row* always means the same thing: "keep what is nearly as
good as the best thing we found, drop what is not".
"""

import hashlib
import re
from collections.abc import Callable, Sequence
from typing import Any

# A row has to score at least this fraction of the top row's score to be listed.
# Half is deliberately permissive: it removes the rows that only matched a
# stray common word while keeping the genuine runners-up a reader may want.
RELEVANCE_CUTOFF = 0.5

# A term in the subject is a far stronger signal than the same term in the body,
# which is why the weights differ. These are the weights the Find pipeline has
# always sorted by; moving them here keeps the cutoff consistent with the order.
SUBJECT_TERM_WEIGHT = 3.0
BODY_TERM_WEIGHT = 1.0
ATTACHMENT_TERM_WEIGHT = 0.75
SUBJECT_PHRASE_WEIGHT = 5.0
BODY_PHRASE_WEIGHT = 2.0
ATTACHMENT_PHRASE_WEIGHT = 1.0


def contains_term(text: str, term: str) -> bool:
    """Whole-word match, so 'steam' does not match 'estimate'."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def contains_phrase(text: str, phrase: str) -> bool:
    """Word-boundary match, allowing any run of whitespace between the words."""
    words = re.findall(r"\w+", phrase)
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def lexical_score(
    subject: str,
    body: str,
    terms: Sequence[str],
    phrases: Sequence[str],
    *,
    attachment_text: str = "",
) -> float:
    """How much of the query this message actually answers.

    Weighted, not a count: matching in the subject beats matching three times in
    the body, and an exact phrase beats a loose term. Attachment text counts for
    less than the message body because an extracted PDF mentioning a word is
    weaker evidence than the author writing it.
    """
    if not terms and not phrases:
        return 0.0
    return (
        SUBJECT_TERM_WEIGHT * sum(contains_term(subject, term) for term in terms)
        + BODY_TERM_WEIGHT * sum(contains_term(body, term) for term in terms)
        + ATTACHMENT_TERM_WEIGHT * sum(contains_term(attachment_text, term) for term in terms)
        + SUBJECT_PHRASE_WEIGHT * sum(contains_phrase(subject, phrase) for phrase in phrases)
        + BODY_PHRASE_WEIGHT * sum(contains_phrase(body, phrase) for phrase in phrases)
        + ATTACHMENT_PHRASE_WEIGHT * sum(contains_phrase(attachment_text, phrase) for phrase in phrases)
    )


def apply_relevance_cutoff[T](
    items: Sequence[T],
    score_of: Callable[[T], float],
    *,
    ratio: float = RELEVANCE_CUTOFF,
) -> list[T]:
    """Keep the items scoring at least ``ratio`` of the best score in ``items``.

    Order is preserved. A best score of zero means nothing in the list matched
    anything, so there is no reference to compare against and nothing is cut;
    callers that *do* have query words pass them differently (see
    :func:`rank_rows`) because there the honest answer is "none of these".
    """
    if len(items) < 2:
        return list(items)
    top = max(score_of(item) for item in items)
    if top <= 0:
        return list(items)
    threshold = top * ratio
    return [item for item in items if score_of(item) >= threshold]


def _normalized(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def duplicate_key(row: Any) -> tuple[str, str]:
    """Identity of a message for duplicate purposes: its subject and its body.

    Same subject *and* same body is what makes a row a duplicate. A forwarded
    copy, a re-sent attachment and a second copy of the same notice in another
    folder all collapse to one row, while a genuine reply that reuses the subject
    ("Re: budget") keeps its own place because its body differs.
    """
    subject = _normalized(getattr(row, "subject", None))
    stored = getattr(row, "normalized_content_hash", None)
    if stored:
        return (subject, str(stored))
    body = _normalized(
        getattr(row, "body_text", None) or getattr(row, "body_preview", None)
    )
    return (subject, hashlib.sha256(f"{subject}\x1f{body}".encode()).hexdigest())


def collapse_duplicate_rows[T](rows: Sequence[T]) -> list[T]:
    """One row per subject + body hash, keeping the first (best ranked) copy."""
    seen: set[tuple[str, str]] = set()
    kept: list[T] = []
    for row in rows:
        key = duplicate_key(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def rank_rows[T](
    rows: Sequence[T],
    *,
    terms: Sequence[str],
    phrases: Sequence[str],
    cited: set[str] | None = None,
    limit: int | None = None,
    id_of: Callable[[T], str] | None = None,
) -> list[T]:
    """Order, de-duplicate and cut one result list.

    Cited emails lead, best-scoring first, because a message the answer actually
    quoted is evidence and the rest are suggestions. Inside that split the
    cutoff applies to the suggestions only: a citation is relevant by
    construction, so scoring it against the best score could drop the very
    passage the reader is trying to check.

    When the caller supplied query words and *nothing* matched them, the honest
    result is an empty list rather than every row at score zero. When no words
    were supplied at all -- a pure ``from:sara`` query, where every row already
    satisfies the filter the reader typed -- there is nothing to compare and
    every row is kept.
    """
    if not rows:
        return []
    cited_ids = cited or set()
    identity = id_of or (lambda row: str(getattr(row, "id", "")))
    scored: list[tuple[T, float, bool, int]] = [
        (
            row,
            lexical_score(
                getattr(row, "subject", None) or "",
                (getattr(row, "body_text", None) or getattr(row, "body_preview", None) or ""),
                terms,
                phrases,
            ),
            identity(row) in cited_ids,
            index,
        )
        for index, row in enumerate(rows)
    ]
    top = max(score for _, score, _, _ in scored)

    if not terms and not phrases:
        kept = scored
    elif top <= 0:
        kept = []
    else:
        kept = [
            entry
            for entry in sorted(scored, key=lambda entry: (not entry[2], -entry[1], entry[3]))
            if entry[2] or entry[1] >= top * RELEVANCE_CUTOFF
        ]

    ordered = collapse_duplicate_rows([row for row, _, _, _ in kept])
    return ordered[:limit] if limit is not None else ordered