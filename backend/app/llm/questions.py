import re
from typing import Any


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
        for term in ("email", "emails", "inbox", "mailbox", "conversation", "conversations", "messages")
    )
    return asks_for_summary and refers_to_mail


def extract_search_terms(payload: dict[str, Any], question: str) -> str:
    keywords = payload.get("keywords")
    if not isinstance(keywords, list):
        return question
    clean_keywords = [value.strip() for value in keywords if isinstance(value, str) and value.strip()]
    return " ".join(clean_keywords[:12]) or question
