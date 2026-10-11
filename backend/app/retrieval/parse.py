import re
from dataclasses import dataclass
from datetime import datetime

SEARCH_STOP_WORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could",
    "did", "do", "does", "email", "emails", "for", "find", "from", "give", "have", "how",
    "i", "in", "inbox", "into", "is", "it", "mail", "mailbox", "me", "message", "messages",
    "my", "of", "on", "or", "please", "recent", "related", "search", "show", "tell", "that",
    "the", "their", "them", "there", "this", "to", "was", "we", "what", "when", "where",
    "which", "who", "why", "with", "would", "you", "your",
}


@dataclass(frozen=True)
class Criteria:
    query: str
    terms: list[str]
    phrases: list[str]
    sender: str | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    folder: str | None = None

    def is_sufficient(self) -> bool:
        return bool(self.terms or self.phrases or self.sender or self.from_dt or self.to_dt or self.folder)


def parse_query(query: str, *, sender: str | None = None, from_dt: datetime | None = None, to_dt: datetime | None = None, folder: str | None = None) -> Criteria:
    cleaned = " ".join(query.split())[:1000]
    phrases = re.findall(r'"([^"\n]{1,200})"', cleaned)
    remainder = re.sub(r'"[^"\n]{1,200}"', " ", cleaned)
    terms = [term.lower() for term in re.findall(r"[\w@.-]{2,}", remainder)][:30]
    return Criteria(cleaned, terms, phrases, sender, from_dt, to_dt, folder)


def required_query_terms(query: str) -> tuple[list[str], list[str]]:
    parsed = parse_query(query)
    terms = [term for term in parsed.terms if term not in SEARCH_STOP_WORDS]
    phrases = [phrase.lower() for phrase in parsed.phrases]
    return list(dict.fromkeys(terms)), list(dict.fromkeys(phrases))
