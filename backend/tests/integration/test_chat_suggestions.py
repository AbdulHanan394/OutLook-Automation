"""AI6: follow-up suggestions.

Suggestions are offered, never applied. The tests that matter are the negative
ones: a suggestion must never become a prefill, a non-question must never be
offered as something to ask, and a provider failure must never break the chat.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.assistant import store, suggestions
from tests.integration.mailbox_helpers import make_user, sign_in


class Stub:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def json_message(self, system: str, prompt: str, max_tokens: int = 400):
        self.prompts.append(prompt)
        return (self.payload, 5, 5)


class Broken:
    def json_message(self, system: str, prompt: str, max_tokens: int = 400):
        import httpx

        raise httpx.ConnectError("down")


def seed(db, client, turns: int = 2):
    user = make_user(db, f"sug-{uuid.uuid4().hex[:8]}@example.test")
    sign_in(db, client, user)
    from tests.integration.test_chats import seed_mailbox

    _, mailbox = seed_mailbox(db)
    conversation = client.post("/api/chats", json={"mailbox_id": str(mailbox.id)}).json()
    for index in range(turns):
        store.add_message(
            db,
            user_id=user.id,
            conversation_id=uuid.UUID(conversation["id"]),
            role="user",
            text_value=f"question {index} about the budget",
        )
        store.add_message(
            db,
            user_id=user.id,
            conversation_id=uuid.UUID(conversation["id"]),
            role="assistant",
            text_value="The revised budget was approved.",
            citations_json=[
                {"email_id": str(uuid.uuid4()), "subject": "Revised budget approval", "sender": "s", "received_at": "2026-03-12"}
            ],
        )
    return user, conversation


# ----------------------------------------------------------------- cleaning


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("What about the payment schedule?", "What about the payment schedule?"),
        ("  who sent that?  ", "who sent that?"),
        ("Summary of the thread", None),
        ("", None),
        ("ok", None),
        ("x" * 300, None),
    ],
)
def test_only_actual_questions_are_offered(raw: str, expected: str | None) -> None:
    """A label that is not a question is not something to ask."""
    assert suggestions._clean(raw) == expected


def test_at_most_three_are_returned(client: TestClient, db, monkeypatch) -> None:
    _user, conversation = seed(db, client)
    monkeypatch.setattr(
        "app.api.routes_chats._interpret_client",
        lambda settings: Stub(
            {
                "suggestions": [
                    "What happened with the launch?",
                    "Who signed it off?",
                    "When is the deadline?",
                    "Why did it slip?",
                    "Did anyone object?",
                ]
            }
        ),
    )
    found = client.get(f"/api/chats/{conversation['id']}/suggestions").json()["suggestions"]
    assert len(found) == 3


# ------------------------------------------------------------------ endpoint


def test_suggestions_are_returned_for_a_conversation(client: TestClient, db, monkeypatch) -> None:
    _user, conversation = seed(db, client)
    monkeypatch.setattr(
        "app.api.routes_chats._interpret_client",
        lambda settings: Stub(
            {"suggestions": ["What about the payment schedule?", "Who signed it off?"]}
        ),
    )
    response = client.get(f"/api/chats/{conversation['id']}/suggestions")
    assert response.status_code == 200
    assert response.json()["suggestions"] == [
        "What about the payment schedule?",
        "Who signed it off?",
    ]


def test_suggestions_never_contain_email_bodies(client: TestClient, db, monkeypatch) -> None:
    """Suggestions are generated from subjects, not from mail the reader has not seen."""
    _user, conversation = seed(db, client)
    stub = Stub({"suggestions": ["What about the dates?"]})
    monkeypatch.setattr("app.api.routes_chats._interpret_client", lambda settings: stub)
    client.get(f"/api/chats/{conversation['id']}/suggestions")
    prompt = stub.prompts[0]
    assert "Revised budget approval" in prompt
    assert "Email addresses under discussion" not in prompt


def test_a_provider_failure_returns_an_empty_list_not_an_error(
    client: TestClient, db, monkeypatch
) -> None:
    _user, conversation = seed(db, client)
    monkeypatch.setattr("app.api.routes_chats._interpret_client", lambda settings: Broken())
    response = client.get(f"/api/chats/{conversation['id']}/suggestions")
    assert response.status_code == 200
    assert response.json()["suggestions"] == []


def test_an_empty_conversation_suggests_nothing(client: TestClient, db, monkeypatch) -> None:
    user = make_user(db, f"sug-empty-{uuid.uuid4().hex[:8]}@example.test")
    sign_in(db, client, user)
    conversation = client.post("/api/chats", json={}).json()
    monkeypatch.setattr(
        "app.api.routes_chats._interpret_client",
        lambda settings: Stub({"suggestions": ["Anything?"]}),
    )
    assert client.get(f"/api/chats/{conversation['id']}/suggestions").json()["suggestions"] == []


def test_suggestions_are_per_user(client: TestClient, db, monkeypatch) -> None:
    _alice, conversation = seed(db, client)
    monkeypatch.setattr(
        "app.api.routes_chats._interpret_client",
        lambda settings: Stub({"suggestions": ["What about the dates?"]}),
    )
    assert client.get(f"/api/chats/{conversation['id']}/suggestions").status_code == 200

    sign_in(db, client, make_user(db, f"sug-mallory-{uuid.uuid4().hex[:8]}@example.test"))
    assert client.get(f"/api/chats/{conversation['id']}/suggestions").status_code == 404


def test_a_suggestion_is_not_written_into_the_transcript(
    client: TestClient, db, monkeypatch
) -> None:
    """Offering a suggestion must not create a turn.

    A suggestion is an offer. If it were stored, the transcript would show a
    question the reader never asked.
    """
    user, conversation = seed(db, client)
    before = len(store.list_messages(db, user_id=user.id, conversation_id=uuid.UUID(conversation["id"])))
    monkeypatch.setattr(
        "app.api.routes_chats._interpret_client",
        lambda settings: Stub({"suggestions": ["What about the dates?"]}),
    )
    client.get(f"/api/chats/{conversation['id']}/suggestions")
    after = len(store.list_messages(db, user_id=user.id, conversation_id=uuid.UUID(conversation["id"])))
    assert after == before


def test_the_cheap_model_is_used_not_the_answer_model(
    client: TestClient, db, monkeypatch
) -> None:
    """A suggestion is a label, and must not spend the answer budget."""
    _user, conversation = seed(db, client)
    seen: list[str] = []

    class Recorder:
        def __init__(self, role: str) -> None:
            self.role = role

        def json_message(self, system: str, prompt: str, max_tokens: int = 400):
            seen.append(self.role)
            return ({"suggestions": ["What about the dates?"]}, 5, 5)

    def factory(settings: object) -> Recorder:
        # create_json_client is called as (settings, role)
        return Recorder(role="interpret")

    monkeypatch.setattr("app.api.routes_chats.create_json_client", lambda s, role: Recorder(role))
    client.get(f"/api/chats/{conversation['id']}/suggestions")
    assert seen == ["interpret"], f"suggestions used {seen} instead of the cheap model"