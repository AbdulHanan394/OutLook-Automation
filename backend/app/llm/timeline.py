import re
from typing import Any

from app.llm.answer import Excerpt

TIMELINE_PATTERNS = (
    re.compile(r"\b(?:timeline|chronology|chronological(?:ly)?)\b", re.IGNORECASE),
    re.compile(r"\bsequence of events\b|\bin order\b|\bstep by step\b", re.IGNORECASE),
    re.compile(r"\b(?:over time|what happened)\b", re.IGNORECASE),
    re.compile(r"\bwhen did .{1,100}\b(?:happen|start|change|finish|end)\b", re.IGNORECASE),
    re.compile(r"\bwhat happened (?:first|then|next|after that)\b", re.IGNORECASE),
    re.compile(r"\bhow did .{1,100}\b(?:evolve|develop|progress)\b", re.IGNORECASE),
)


def is_timeline_question(question: str) -> bool:
    return any(pattern.search(question) for pattern in TIMELINE_PATTERNS)


def build_timeline(result: dict[str, Any], excerpts: list[Excerpt]) -> list[dict[str, str | None]]:
    if not result.get("sufficient_evidence"):
        return []
    by_email_id = {excerpt.email_id: excerpt for excerpt in excerpts}
    timeline = []
    seen = set()
    for claim in result.get("claims", []):
        quote = re.sub(r"\s+", " ", claim["quote"]).strip()
        for email_id in claim["email_ids"]:
            excerpt = by_email_id.get(email_id)
            if excerpt is None:
                continue
            normalized_source = re.sub(r"\s+", " ", excerpt.text).strip()
            if quote not in normalized_source:
                continue
            key = (email_id, claim["text"], quote)
            if key in seen:
                continue
            seen.add(key)
            timeline.append({
                "date": excerpt.date,
                "email_id": email_id,
                "subject": excerpt.subject,
                "sender": excerpt.sender,
                "text": claim["text"],
                "quote": claim["quote"],
                "outlook_url": excerpt.outlook_url,
            })
    return sorted(timeline, key=lambda item: (item["date"] or "", item["email_id"] or ""))
