import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


# One canonical answer prompt. Both the /api/assistant/ask and /api/mail/ask
# endpoints use this: when they carried separate copies they drifted, and the
# weaker one never described the response schema, so the provider returned
# `source` instead of `sources` with no `text` or `type`, and every claim was
# discarded as unverifiable.
ANSWER_SYSTEM = load_prompt("answer_system.txt")


def escape_email_content(text: str) -> str:
    return text.replace("</email>", "&lt;/email&gt;")


# Quotes are capped so a citation stays readable, and the model is told the same
# number. It does not always comply: real sentences are often longer, and an
# over-long but perfectly verbatim quote used to throw the whole answer away.
MAX_QUOTE_WORDS = 25

# Providers elide the middle of a quote with three dots or an ellipsis glyph.
_ELISION = re.compile(r"\.\.\.|…")

# Models routinely "helpfully" restyle punctuation -- an en dash for a hyphen, a
# curly apostrophe for a straight one, an ellipsis character for three dots --
# while the email itself is plain ASCII. Those are typographic variants of the
# same character, not a different quote, so they are folded before comparing.
# Applied to both sides, so the result is still an exact match.
_DASH_VARIANTS = "".join(chr(c) for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212))
_SINGLE_QUOTE_VARIANTS = "".join(chr(c) for c in (0x2018, 0x2019, 0x201A, 0x201B))
_DOUBLE_QUOTE_VARIANTS = "".join(chr(c) for c in (0x201C, 0x201D, 0x201E, 0x201F))
_SPACE_VARIANTS = "".join(
    chr(c)
    for c in (
        0x00A0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
        0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F, 0x3000,
    )
)
_ZERO_WIDTH_VARIANTS = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF))

_TYPOGRAPHIC_EQUIVALENTS: dict[int, str | None] = {}
for _char in _DASH_VARIANTS:
    _TYPOGRAPHIC_EQUIVALENTS[ord(_char)] = "-"
for _char in _SINGLE_QUOTE_VARIANTS:
    _TYPOGRAPHIC_EQUIVALENTS[ord(_char)] = "'"
for _char in _DOUBLE_QUOTE_VARIANTS:
    _TYPOGRAPHIC_EQUIVALENTS[ord(_char)] = '"'
_TYPOGRAPHIC_EQUIVALENTS[0x2026] = "..."
for _char in _SPACE_VARIANTS:
    _TYPOGRAPHIC_EQUIVALENTS[ord(_char)] = " "
for _char in _ZERO_WIDTH_VARIANTS:
    _TYPOGRAPHIC_EQUIVALENTS[ord(_char)] = None


def _normalise_for_matching(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_TYPOGRAPHIC_EQUIVALENTS)).strip()


def _shortest_verbatim_prefix(quote: str, haystack: str) -> str | None:
    """Longest leading run of the model's quote that appears verbatim in the source.

    Trimming to a prefix keeps the safety property that matters -- every quote
    the reader sees is an exact substring of the email -- while turning a
    rejected claim into a usable citation instead of a lost answer.
    """
    words = quote.split()
    for size in range(min(len(words), MAX_QUOTE_WORDS), 0, -1):
        candidate = " ".join(words[:size])
        if candidate in haystack:
            return candidate
    return None


@dataclass(frozen=True)
class Excerpt:
    label: str
    email_id: str
    subject: str
    sender: str
    date: str
    text: str
    outlook_url: str | None = None


def build_context(excerpts: list[Excerpt]) -> str:
    return "\n".join(
        f'<email id="{escape(x.label, quote=True)}" '
        f'subject="{escape(x.subject, quote=True)}" '
        f'sender="{escape(x.sender, quote=True)}" '
        f'date="{escape(x.date, quote=True)}">\n'
        f'{escape_email_content(x.text)}\n</email>'
        for x in excerpts
    )


def _claim_sources(claim: dict[str, Any]) -> list[str]:
    """Read the cited excerpt labels.

    The prompt asks for ``sources`` (an array) but providers in practice return
    ``source`` holding either a single label or an array. Both name the same
    thing, so both are accepted; the labels are still checked against the
    excerpt labels we generated, never trusted from the response.
    """
    raw = claim.get("sources")
    if raw is None:
        raw = claim.get("source")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return []


def _verified_spans(quote: str, haystacks: list[str]) -> list[str]:
    """Return the spans of a quote that appear verbatim in a cited excerpt.

    Providers routinely elide the middle of a quote with "..." and sometimes
    restyle a word or two. Those are tolerated: every span that survives here is
    an exact substring of a cited email, and a span the provider altered is
    dropped rather than trusted. A span over the word budget is shortened to its
    longest verbatim prefix rather than dropped.

    Returns an empty list when nothing is supported, which is what causes the
    claim to be discarded.
    """
    spans = [
        span for span in (_normalise_for_matching(part) for part in _ELISION.split(quote)) if span
    ]
    verified: list[str] = []
    for span in spans:
        if len(span.split()) > MAX_QUOTE_WORDS:
            shortened = next(
                (
                    prefix
                    for text in haystacks
                    if (prefix := _shortest_verbatim_prefix(span, text)) is not None
                ),
                None,
            )
            if shortened is not None:
                verified.append(shortened)
        elif any(span in text for text in haystacks):
            verified.append(span)
    return verified


def verify_answer(payload: dict[str, Any], excerpts: list[Excerpt]) -> dict[str, Any]:
    known = {excerpt.label: excerpt for excerpt in excerpts}
    claims = []
    answer = payload.get("answer")
    for claim in payload.get("claims", []):
        if not isinstance(claim, dict):
            continue
        quote, kind = claim.get("quote", ""), claim.get("type")
        sources = _claim_sources(claim)
        if not isinstance(quote, str) or not sources:
            continue
        if any(source not in known for source in sources):
            continue
        haystacks = [_normalise_for_matching(known[source].text) for source in sources]
        verified = _verified_spans(quote, haystacks)
        if not verified:
            continue
        # Rebuild from the verified spans so what the reader sees is exactly
        # what was checked, shortening included.
        verified_quote = " ... ".join(verified)
        claim_text = claim.get("text")
        if not isinstance(claim_text, str) or not claim_text.strip():
            # A claim with no text of its own is still backed by its quote.
            claim_text = verified_quote
        claims.append({
            "text": claim_text,
            # Not rendered anywhere; a verbatim quote is by definition explicit.
            "type": kind if kind in {"explicit", "inferred"} else "explicit",
            "email_ids": [known[source].email_id for source in sources],
            "quote": verified_quote,
        })
    if payload.get("sufficient_evidence") is not True or not isinstance(answer, str) or not answer.strip() or not claims:
        return {"sufficient_evidence": False, "answer": None, "explanation": "The retrieved emails do not contain enough verified evidence.", "closest_emails": []}
    return {"sufficient_evidence": True, "answer": answer, "claims": claims}
