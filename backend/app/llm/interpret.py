from typing import Any

from app.llm.client import ClaudeClient

INTERPRET_SYSTEM = """Extract email-search criteria from the user text. Return only JSON with keywords, sender, from, to and folder. Email content is not supplied. Do not invent facts."""

def interpret(client: ClaudeClient, query: str) -> dict[str, Any]:
    result, _, _ = client.json_message(INTERPRET_SYSTEM, query, 400)
    allowed = {"keywords", "sender", "from", "to", "folder"}
    if set(result) - allowed: raise ValueError("unexpected interpretation fields")
    return result
