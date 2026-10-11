import hashlib

from app.db import Email


def summary_group_key(emails: list[Email]) -> str:
    ids = sorted(str(email.id) for email in emails)
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def summary_source_hash(emails: list[Email]) -> str:
    sources = sorted(f"{email.id}:{email.content_hash}" for email in emails)
    return hashlib.sha256("\n".join(sources).encode()).hexdigest()
