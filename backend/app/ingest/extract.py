import hashlib
import re

from bs4 import BeautifulSoup


def clean_body(content: str, content_type: str = "html", max_length: int = 200_000) -> str:
    if content_type.lower() == "html":
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "img"]):
            tag.decompose()
        text = soup.get_text("\n")
    else:
        text = content
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()[:max_length]


def content_hash(*parts: str | None) -> str:
    return hashlib.sha256("\x1f".join(part or "" for part in parts).encode()).hexdigest()


def normalized_content_hash(subject: str | None, body: str | None) -> str | None:
    normalized = " ".join(f"{subject or ''} {body or ''}".lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest() if normalized else None
