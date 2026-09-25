"""Small regression suite for the highest-value conversation behavior."""

import os

os.environ["LLM_PROVIDER"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app.main import app, session_store
from app.schema import FieldStatus


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_id(client):
    response = client.post("/session")
    assert response.status_code == 200
    return response.json()["session_id"]


def prepare_children_names_question(session_id):
    state = session_store.get_state(session_id)
    state.full_name = FieldStatus(status="confirmed", value="Saitama")
    state.home_address = FieldStatus(status="confirmed", value="12 Elm Street")
    state.covers_worldwide_assets = FieldStatus(status="confirmed", value=True)
    state.has_children = FieldStatus(status="confirmed", value=True)
    session_store.update_state(session_id, state)


def test_name_updates_state_and_rendered_draft(client, session_id):
    """A confirmed value updates canonical state and the document together."""
    response = client.post(
        f"/session/{session_id}/message", json={"message": "My name is Saitama"}
    )
    data = response.json()

    assert data["state"]["full_name"] == {"status": "confirmed", "value": "Saitama"}
    assert "Saitama" in data["document"]


def test_children_names_answer_updates_only_children_names(client, session_id):
    """An active children-names answer cannot become executor information."""
    prepare_children_names_question(session_id)

    response = client.post(
        f"/session/{session_id}/message", json={"message": "Chris, Chris"}
    )
    data = response.json()

    assert data["state"]["children_names"]["value"] == ["Chris", "Chris"]
    assert data["state"]["executor"]["status"] == "unknown"


def test_named_after_requires_actual_children_names(client, session_id):
    """Naming inspiration stays ambiguous rather than inventing children's names."""
    prepare_children_names_question(session_id)

    response = client.post(
        f"/session/{session_id}/message",
        json={"message": "My children are all named after my wife Melissa."},
    )
    data = response.json()

    assert data["state"]["children_names"]["status"] == "unknown"
    assert data["pending_questions"][0] == "children's names (if applicable)"
    assert "actual name" in data["assistant_reply"].lower()


def test_off_topic_message_does_not_change_document_state(client, session_id):
    """A casual question cannot accidentally populate a document field."""
    response = client.post(
        f"/session/{session_id}/message", json={"message": "What color is the sky?"}
    )
    state = response.json()["state"]

    assert state["full_name"]["status"] == "unknown"
    assert state["home_address"]["status"] == "unknown"


def test_history_keeps_the_user_and_assistant_turn(client, session_id):
    """Conversation context is retained separately from structured state."""
    client.post(
        f"/session/{session_id}/message", json={"message": "My name is Saitama"}
    )
    history = client.get(f"/session/{session_id}/history").json()["history"]

    assert [entry["role"] for entry in history] == ["user", "assistant"]
    assert history[0]["text"] == "My name is Saitama"
