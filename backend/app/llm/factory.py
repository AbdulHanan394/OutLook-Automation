from typing import Literal

from fastapi import HTTPException

from app.config import Settings
from app.llm.client import ClaudeClient, GroqClient, JsonMessageClient


def create_json_client(settings: Settings, purpose: Literal["interpret", "answer"]) -> JsonMessageClient:
    if settings.llm_provider == "groq":
        api_key = settings.groq_api_key.get_secret_value().strip() if settings.groq_api_key else ""
        if not api_key or "PASTE-YOUR" in api_key:
            raise HTTPException(503, "Groq is selected but GROQ_API_KEY is not configured.")
        return GroqClient(
            api_key,
            settings.groq_models_endpoint,
            settings.groq_model,
            settings.claude_timeout_seconds,
        )

    if not settings.anthropic_api_key:
        raise HTTPException(503, "Anthropic is selected but ANTHROPIC_API_KEY is not configured.")
    model = settings.claude_model_answer if purpose == "answer" else settings.claude_model_interpret
    if not model:
        name = "CLAUDE_MODEL_ANSWER" if purpose == "answer" else "CLAUDE_MODEL_INTERPRET"
        raise HTTPException(503, f"{name} is not configured.")
    return ClaudeClient(
        settings.anthropic_api_key.get_secret_value(),
        model,
        settings.claude_timeout_seconds,
        settings.claude_max_retries,
    )


def create_claude_interpret_client(settings: Settings) -> ClaudeClient:
    if not settings.anthropic_api_key:
        raise HTTPException(503, "Anthropic is required for commitment extraction but is not configured.")
    if not settings.claude_model_interpret:
        raise HTTPException(503, "CLAUDE_MODEL_INTERPRET is not configured.")
    return ClaudeClient(
        settings.anthropic_api_key.get_secret_value(),
        settings.claude_model_interpret,
        settings.claude_timeout_seconds,
        0,
    )
