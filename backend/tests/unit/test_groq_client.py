import json

import httpx
import pytest

from app.config import Settings
from app.llm.answer import Excerpt, build_context, verify_answer
from app.llm.client import GroqClient
from app.llm.questions import extract_search_terms


def test_groq_client_sends_json_chat_completion(monkeypatch):
    request_details = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"sufficient_evidence": false, "claims": []}'}}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 4},
            }

    class Client:
        def __init__(self, timeout):
            assert timeout == 12

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def post(self, url, headers, json):
            request_details.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    client = GroqClient("test-key", "https://api.groq.com/openai/v1/", "openai/gpt-oss-120b", 12)

    payload, input_tokens, output_tokens = client.json_message("system prompt", "user prompt")

    assert request_details["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert request_details["headers"]["Authorization"] == "Bearer test-key"
    assert request_details["json"]["model"] == "openai/gpt-oss-120b"
    assert request_details["json"]["max_completion_tokens"] == 1200
    assert request_details["json"]["reasoning_effort"] == "low"
    assert request_details["json"]["response_format"] == {"type": "json_object"}
    assert payload == {"sufficient_evidence": False, "claims": []}
    assert (input_tokens, output_tokens) == (17, 4)


def test_groq_client_rejects_non_object_json(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([])}}]}

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def post(self, url, headers, json):
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    client = GroqClient("test-key", "https://api.groq.com/openai/v1", "test-model", 10)

    with pytest.raises(TypeError, match="JSON object"):
        client.json_message("system", "prompt")


def test_groq_client_uses_openai_compatible_token_limit_for_other_models(monkeypatch):
    request_payload = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {},
            }

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def post(self, url, headers, json):
            request_payload.update(json)
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    GroqClient("test-key", "https://api.groq.com/openai/v1", "llama-model", 10).json_message("system", "prompt", 250)

    assert request_payload["max_completion_tokens"] == 250
    assert "max_tokens" not in request_payload
    assert "reasoning_effort" not in request_payload


def test_answer_contains_only_verifiable_citations_and_escaped_metadata():
    excerpt = Excerpt("E1", "id1", 'Project "plan"', "sender", "2026-01-01", "deliver on 30 April")
    context = build_context([excerpt])
    assert 'subject="Project &quot;plan&quot;"' in context

    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "Delivery is on 30 April.",
            "claims": [
                {"text": "Delivery date", "type": "explicit", "sources": ["E1"], "quote": "deliver on 30 April"},
                {"text": "Unverified", "type": "explicit", "sources": ["E9"], "quote": "made up quote"},
            ],
        },
        [excerpt],
    )

    assert result["sufficient_evidence"] is True
    assert result["answer"] == "Delivery is on 30 April."
    assert len(result["claims"]) == 1
    assert result["claims"][0]["email_ids"] == ["id1"]


def test_answer_without_verified_claim_is_refused():
    result = verify_answer(
        {
            "sufficient_evidence": True,
            "answer": "An unsupported answer.",
            "claims": [{"text": "Claim", "type": "explicit", "sources": ["E1"], "quote": "not in source"}],
        },
        [Excerpt("E1", "id1", "subject", "sender", "2026-01-01", "source text")],
    )

    assert result["sufficient_evidence"] is False
    assert result["answer"] is None


def test_natural_question_keywords_are_cleaned_and_bounded():
    question = "Could you kindly summarize what happened with the launch?"
    keywords = [f" launch {index} " for index in range(14)]

    assert extract_search_terms({"keywords": keywords}, question) == " ".join(
        f"launch {index}" for index in range(12)
    )


@pytest.mark.parametrize("payload", [{"keywords": "launch"}, {"keywords": []}, {}])
def test_search_terms_fall_back_to_original_question(payload):
    question = "Could you summarize the launch discussion?"

    assert extract_search_terms(payload, question) == question


@pytest.mark.parametrize("endpoint_env", ["GROQ_MODELS_ENDPOINT", "GQROQ_MODELS_ENDPOINT"])
def test_groq_endpoint_setting_accepts_canonical_and_legacy_name(monkeypatch, endpoint_env):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv(endpoint_env, "https://groq.example/v1")

    settings = Settings(
        _env_file=None,
        session_secret="test-session-secret",
        token_encryption_key="test-token-encryption-key",
        llm_provider="groq",
    )

    assert settings.groq_api_key.get_secret_value() == "test-key"
    assert settings.groq_models_endpoint == "https://groq.example/v1"
