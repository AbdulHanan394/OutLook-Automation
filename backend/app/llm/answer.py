import re
from dataclasses import dataclass
from html import escape
from typing import Any


def escape_email_content(text: str) -> str:
    return text.replace("</email>", "&lt;/email&gt;")


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


def verify_answer(payload: dict[str, Any], excerpts: list[Excerpt]) -> dict[str, Any]:
    known = {excerpt.label: excerpt for excerpt in excerpts}
    claims = []
    answer = payload.get("answer")
    for claim in payload.get("claims", []):
        if not isinstance(claim, dict):
            continue
        sources, quote, kind = claim.get("sources", []), claim.get("quote", ""), claim.get("type")
        if not isinstance(sources, list) or not isinstance(quote, str):
            continue
        normalized = re.sub(r"\s+", " ", quote).strip()
        claim_text = claim.get("text")
        if (
            kind not in {"explicit", "inferred"}
            or not isinstance(claim_text, str)
            or not claim_text.strip()
            or not sources
            or not normalized
            or len(normalized.split()) > 25
            or any(not isinstance(source, str) or source not in known for source in sources)
        ):
            continue
        if not any(normalized in re.sub(r"\s+", " ", known[source].text) for source in sources):
            continue
        claims.append({"text": claim_text, "type": kind, "email_ids": [known[source].email_id for source in sources], "quote": quote})
    if payload.get("sufficient_evidence") is not True or not isinstance(answer, str) or not answer.strip() or not claims:
        return {"sufficient_evidence": False, "answer": None, "explanation": "The retrieved emails do not contain enough verified evidence.", "closest_emails": []}
    return {"sufficient_evidence": True, "answer": answer, "claims": claims}
