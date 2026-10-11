from app.ingest.extract import content_hash


def chunk_email(subject: str | None, sender: str | None, body: str, target_tokens: int = 400, overlap: int = 40) -> list[tuple[str, str]]:
    prefix = f"Subject: {subject or '(no subject)'}\nSender: {sender or '(unknown)'}\n\n"
    words = body.split()
    if not words:
        return [(prefix.strip(), content_hash(prefix))]
    output: list[tuple[str, str]] = []
    start = 0
    while start < len(words):
        part = " ".join(words[start : start + target_tokens])
        text = prefix + part
        output.append((text, content_hash(text)))
        if start + target_tokens >= len(words):
            break
        start += max(1, target_tokens - overlap)
    return output
