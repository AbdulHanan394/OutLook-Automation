import re
from typing import Any

_RECAP_TRIGGERS = (
    "what have i missed",
    "what happened recently",
    "catch me up",
    "give me a rundown",
    "brief me",
    "what is new",
    "what's new",
    "summarize",
    "summarise",
    "summary",
    "overview",
    "recap",
    "highlights",
    "rundown",
)

_MAIL_NOUNS = (
    "conversations",
    "conversation",
    "messages",
    "message",
    "emails",
    "email",
    "mailbox",
    "inbox",
    "mail",
)

# Words that carry no topic: they only shape the recap request itself.
_RECAP_FILLER = frozenset(
    {
        "a", "about", "an", "and", "any", "are", "as", "at", "be", "brief", "can",
        "detailed", "did", "do", "does", "easy", "for", "friendly", "from", "get",
        "give", "gentle", "has", "have", "high", "how", "i", "in", "into", "is",
        "it", "latest", "level", "me", "my", "new", "newest", "of", "on", "or",
        "overall", "please", "polite", "quick", "recent", "recently", "rough",
        "short", "show", "simple", "so", "some", "tell", "that", "the", "there",
        "thorough", "to", "up", "us", "was", "were", "what", "whats", "with",
        "you", "your",
    }
)


def is_recent_summary_question(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.lower()).strip()
    asks_for_summary = any(
        phrase in normalized
        for phrase in (
            "summarize",
            "summary",
            "overview",
            "catch me up",
            "what's new",
            "what is new",
            "what happened recently",
            "what have i missed",
        )
    )
    refers_to_mail = any(
        term in normalized
        for term in ("email", "emails", "mail", "inbox", "mailbox", "conversation", "conversations", "messages")
    )
    return asks_for_summary and refers_to_mail


def recent_summary_topic(question: str) -> str:
    """The topic of a recap question, once the recap wording itself is removed.

    "summarize my latest emails" has no topic and wants recency.
    "summarize my latest emails about SE hiring" has one, and treating it as a
    bare recap throws the topic away -- the answer then describes whichever
    messages happen to be newest rather than the ones that were asked about.
    """
    text = re.sub(r"\s+", " ", question.lower()).strip()
    for trigger in _RECAP_TRIGGERS:
        text = text.replace(trigger, " ")
    for noun in _MAIL_NOUNS:
        text = re.sub(rf"\b{noun}\b", " ", text)
    words = [word for word in re.findall(r"[a-z0-9][a-z0-9'+-]*", text) if word not in _RECAP_FILLER]
    return " ".join(words)


def extract_search_terms(payload: dict[str, Any], question: str) -> str:
    keywords = payload.get("keywords")
    if not isinstance(keywords, list):
        return question
    clean_keywords = [value.strip() for value in keywords if isinstance(value, str) and value.strip()]
    return " ".join(clean_keywords[:12]) or question
