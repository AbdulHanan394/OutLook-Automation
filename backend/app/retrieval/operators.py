"""Outlook-style search operators.

Why this runs before :func:`app.retrieval.parse.parse_query`: that tokenizer is
``[\\w@.-]{2,}``, which does not include a colon. ``from:alex@example.com``
silently becomes the terms ``["from", "alex@example.com"]`` -- the word "from"
becomes a mandatory lexical term and pollutes relevance scoring for every result.
Operators therefore have to be lifted out of the string *before* it is tokenized,
and what is left over is what gets searched.

None of this calls a model. Operator queries are deterministic and instant, which
is the whole point: ``from:sara has:attachment`` should not spend a Claude call.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

MAX_OPERATOR_LENGTH = 40
MAX_VALUE_LENGTH = 200

# from: to: subject: folder: sender: cc: has: is: before: after:
_OPERATOR = re.compile(
    r"\b(from|to|cc|bcc|subject|folder|sender|has|is|before|after)\s*:\s*"
    rf'(\"[^\"]{{1,{MAX_VALUE_LENGTH}}}\"|\S{{1,{MAX_VALUE_LENGTH}}})',
    re.IGNORECASE,
)

# from:sara@example.com and friends.
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# from:"Sara Ahmed"
_QUOTED = re.compile(r'^"(?P<value>.*)"$', re.DOTALL)

_HAS_VALUES = frozenset({"attachment", "attachments"})
_IS_VALUES = frozenset({"unread", "read", "starred", "flagged"})

Relational = Literal["after", "before"]


@dataclass(frozen=True)
class Operators:
    """Operators lifted out of a raw query, plus the text left to search."""

    free_text: str
    senders: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    has_attachment: bool = False
    is_unread: bool | None = None
    is_flagged: bool = False
    after: date | None = None
    before: date | None = None
    unknown: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        """True when the reader used at least one operator."""
        return bool(
            self.senders
            or self.recipients
            or self.subjects
            or self.folders
            or self.has_attachment
            or self.is_unread is not None
            or self.is_flagged
            or self.after
            or self.before
        )

    @property
    def has_free_text(self) -> bool:
        return bool(self.free_text.strip())


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y", "%b %Y")


def parse_date(value: str, *, today: date | None = None) -> date | None:
    """Absolute dates only.

    Relative phrasing ("last week") is a judgement call and is left to the
    interpretation step, where it can be guessed at and shown to the reader as a
    guess. Silently guessing it here would make an unlabelled assumption look
    like something the reader typed.
    """
    reference = today or datetime.now(UTC).date()
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # "2026-03" and "2026"
    partial = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
    if partial:
        year = int(partial.group(1))
        month = int(partial.group(2) or 1)
        if 1 <= month <= 12:
            day = int(partial.group(3) or 1)
            try:
                return date(year, month, day)
            except ValueError:
                return None
    _ = reference
    return None


def parse_operators(raw: str) -> Operators:
    """Split ``raw`` into structured operators and the remaining search text."""
    senders: list[str] = []
    recipients: list[str] = []
    subjects: list[str] = []
    folders: list[str] = []
    unknown: list[str] = []
    has_attachment = False
    is_flagged = False
    is_unread: bool | None = None
    after: date | None = None
    before: date | None = None

    def add(bucket: list[str], value: str) -> None:
        cleaned = " ".join(value.split())[:MAX_OPERATOR_LENGTH]
        if cleaned and cleaned not in bucket:
            bucket.append(cleaned)

    working = raw[: MAX_VALUE_LENGTH * 4]

    # Collect every match before mutating anything: splicing the string inside
    # the finditer loop invalidates the offsets of every match not yet visited,
    # which silently leaves the tail of the query (and its operators) in the
    # free text. Stripping afterwards, in reverse, keeps the offsets valid.
    matches = list(_OPERATOR.finditer(working))

    for match in matches:
        name = match.group(1).lower()
        value = match.group(2).strip()
        unquoted = _QUOTED.match(value)
        if unquoted:
            value = unquoted.group("value")

        if name in {"from", "sender"}:
            add(senders, value)
        elif name in {"to", "cc", "bcc"}:
            add(recipients, value)
        elif name == "subject":
            add(subjects, value)
        elif name == "folder":
            add(folders, value.lower())
        elif name == "has":
            token = match.group(2).strip().lower()
            if token in _HAS_VALUES:
                has_attachment = True
            else:
                unknown.append(f"has:{token}")
        elif name == "is":
            token = match.group(2).strip().lower()
            if token == "unread":
                is_unread = True
            elif token == "read":
                is_unread = False
            elif token in {"starred", "flagged"}:
                is_flagged = True
            else:
                unknown.append(f"is:{token}")
        elif name in {"before", "after"}:
            parsed = parse_date(value)
            if parsed is None:
                unknown.append(f"{name}:{value}")
            elif name == "after":
                after = parsed
            else:
                before = parsed

    for match in reversed(matches):
        working = working[: match.start()] + " " + working[match.end() :]

    # "has:attachment" with no value is a normal thing to type into a search box.
    if re.search(r"\bhas\s*:\s*$", working, re.IGNORECASE):
        has_attachment = True
        working = re.sub(r"\bhas\s*:\s*$", " ", working, flags=re.IGNORECASE)

    return Operators(
        free_text=re.sub(r"\s{2,}", " ", working).strip(),
        senders=senders,
        recipients=recipients,
        subjects=subjects,
        folders=folders,
        has_attachment=has_attachment,
        is_unread=is_unread,
        is_flagged=is_flagged,
        after=after,
        before=before,
        unknown=unknown,
    )


def sender_matches(candidate: str | None, wanted: str) -> bool:
    """Loose sender match: substring on either the name or the address."""
    if not candidate:
        return False
    haystack = candidate.lower()
    needle = wanted.lower()
    if needle in haystack:
        return True
    if _ADDRESS.match(needle):
        return False
    # "sara" should match "Sara Ahmed <sara@example.com>".
    return any(part.startswith(needle) for part in re.split(r"[\s@.]+", haystack) if part)


def describe(operators: Operators) -> list[dict[str, str | bool]]:
    """Machine-readable chips for the UI, one per applied filter."""
    chips: list[dict[str, str | bool]] = []
    for value in operators.senders:
        chips.append({"kind": "sender", "label": f"From {value}", "value": value, "guessed": False})
    for value in operators.recipients:
        chips.append({"kind": "recipient", "label": f"To {value}", "value": value, "guessed": False})
    for value in operators.subjects:
        chips.append({"kind": "subject", "label": f'Subject "{value}"', "value": value, "guessed": False})
    for value in operators.folders:
        chips.append({"kind": "folder", "label": f"Folder {value}", "value": value, "guessed": False})
    if operators.has_attachment:
        chips.append({"kind": "attachment", "label": "Has attachment", "value": "true", "guessed": False})
    if operators.is_unread is not None:
        chips.append(
            {
                "kind": "unread",
                "label": "Unread only" if operators.is_unread else "Read only",
                "value": str(operators.is_unread).lower(),
                "guessed": False,
            }
        )
    if operators.is_flagged:
        chips.append({"kind": "flagged", "label": "Flagged", "value": "true", "guessed": False})
    if operators.after:
        chips.append(
            {"kind": "after", "label": f"After {operators.after.isoformat()}", "value": operators.after.isoformat(), "guessed": False}
        )
    if operators.before:
        chips.append(
            {"kind": "before", "label": f"Before {operators.before.isoformat()}", "value": operators.before.isoformat(), "guessed": False}
        )
    return chips


def date_window(operators: Operators) -> tuple[datetime | None, datetime | None]:
    """Half-open [after, before) window in UTC.

    ``before:2026-03-14`` means "up to the start of 14 March", which is what a
    reader typing a date into a mail search expects.
    """
    start = None
    if operators.after:
        start = datetime.combine(operators.after, datetime.min.time(), tzinfo=UTC)
    end = None
    if operators.before:
        end = datetime.combine(operators.before, datetime.min.time(), tzinfo=UTC)
    _ = timedelta
    return start, end