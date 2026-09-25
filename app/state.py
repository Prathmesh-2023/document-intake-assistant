"""
Session store and state merge logic.

Responsibilities:
- In-memory session storage (would move to Redis/DB for production)
- Merging validated updates into canonical state
- Handling corrections (overwrites with the new value)
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from app.schema import DocumentState, ProposedUpdate, FieldStatus


class SessionStore:
    """
    In-memory session storage.

    Production would use Redis or a database with TTL/expiry,
    but for this exercise, a simple dict is sufficient.
    """

    def __init__(self):
        self._sessions: dict[str, DocumentState] = {}
        self._histories: dict[str, list[dict[str, str]]] = {}
        self._deferred_fields: dict[str, list[str]] = {}
        self._clarification_fields: dict[str, list[str]] = {}

    def create_session(self) -> str:
        """Create a new session and return its ID."""
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = DocumentState()
        self._histories[session_id] = []
        self._deferred_fields[session_id] = []
        self._clarification_fields[session_id] = []
        return session_id

    def get_state(self, session_id: str) -> Optional[DocumentState]:
        """Retrieve state for a session, or None if not found."""
        return self._sessions.get(session_id)

    def update_state(self, session_id: str, state: DocumentState) -> None:
        """Update the state for a session."""
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        self._sessions[session_id] = state

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        self._sessions.pop(session_id, None)
        self._histories.pop(session_id, None)
        self._deferred_fields.pop(session_id, None)
        self._clarification_fields.pop(session_id, None)

    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists."""
        return session_id in self._sessions

    def append_history(self, session_id: str, role: str, text: str) -> None:
        """Append a raw conversation turn without changing canonical document state."""
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        self._histories[session_id].append({
            "role": role,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_history(self, session_id: str, limit: int | None = None) -> list[dict[str, str]]:
        """Return a copy of the session transcript, optionally limited to recent turns."""
        history = self._histories.get(session_id, [])
        return [entry.copy() for entry in (history[-limit:] if limit else history)]

    def defer_field(self, session_id: str, field_name: str) -> None:
        """Keep an unknown field pending but ask about it after other unanswered fields."""
        if field_name and field_name not in self._deferred_fields[session_id]:
            self._deferred_fields[session_id].append(field_name)

    def clear_deferred_fields(self, session_id: str, field_names: list[str]) -> None:
        """Remove fields from the deferred list once the user supplies a confirmed value."""
        self._deferred_fields[session_id] = [
            field for field in self._deferred_fields[session_id] if field not in field_names
        ]

    def get_deferred_fields(self, session_id: str) -> list[str]:
        """Return fields that should be asked after active unanswered fields."""
        return self._deferred_fields.get(session_id, []).copy()

    def prioritize_clarification_fields(self, session_id: str, field_names: list[str]) -> None:
        """Keep ambiguous fields at the front until the user clarifies them."""
        known_fields = set(DocumentState.model_fields)
        for field_name in field_names:
            if field_name in known_fields and field_name not in self._clarification_fields[session_id]:
                self._clarification_fields[session_id].append(field_name)

    def clear_clarification_fields(self, session_id: str, field_names: list[str]) -> None:
        """Remove fields from clarification priority once a value is confirmed."""
        self._clarification_fields[session_id] = [
            field for field in self._clarification_fields[session_id] if field not in field_names
        ]

    def get_clarification_fields(self, session_id: str) -> list[str]:
        """Return unresolved fields that need a direct follow-up."""
        return self._clarification_fields.get(session_id, []).copy()


def merge_update_into_state(
    current_state: DocumentState,
    validated_update: ProposedUpdate
) -> DocumentState:
    """
    Merge a validated ProposedUpdate into the current state.

    Rules:
    1. Only fields present in the update (not None) are considered
    2. Each field moved to "confirmed" status
    3. Corrections overwrite previous confirmed values
    4. Returns a NEW DocumentState (doesn't mutate input)

    The update has already been validated, so we trust it here.
    """
    # Work with a copy to avoid mutating the input
    new_state = current_state.model_copy(deep=True)

    # Helper to update a field
    def update_field(field_name: str, value):
        if value is not None:
            setattr(
                new_state,
                field_name,
                FieldStatus(status="confirmed", value=value)
            )

    # Update each field that's present in the proposed update
    if validated_update.full_name is not None:
        update_field("full_name", validated_update.full_name)

    if validated_update.home_address is not None:
        update_field("home_address", validated_update.home_address)

    if validated_update.covers_worldwide_assets is not None:
        update_field("covers_worldwide_assets", validated_update.covers_worldwide_assets)

    if validated_update.has_children is not None:
        update_field("has_children", validated_update.has_children)
        if validated_update.has_children is False and validated_update.children_names is None:
            update_field("children_names", [])

    if validated_update.children_names is not None:
        update_field("children_names", validated_update.children_names)

    if validated_update.executor is not None:
        # Store as dict for consistency
        update_field("executor", validated_update.executor.model_dump())

    if validated_update.specific_gifts is not None:
        # Store as list of dicts
        gifts_data = [gift.model_dump() for gift in validated_update.specific_gifts]
        update_field("specific_gifts", gifts_data)

    if validated_update.additional_wishes is not None:
        update_field("additional_wishes", validated_update.additional_wishes)

    return new_state


def identify_pending_field_names(
    state: DocumentState,
    deferred_fields: list[str] | None = None,
    clarification_fields: list[str] | None = None,
) -> list[str]:
    """Return unknown field names, keeping direct clarifications at the front."""
    unknown = state.get_unknown_fields()

    if state.has_children.status == "confirmed" and state.has_children.value is False:
        unknown = [field for field in unknown if field != "children_names"]

    deferred = deferred_fields or []
    clarification = clarification_fields or []
    priority = [field for field in clarification if field in unknown]
    active = [field for field in unknown if field not in deferred and field not in priority]
    deferred_unknown = [field for field in unknown if field in deferred and field not in priority]
    return priority + active + deferred_unknown


def identify_pending_questions(
    state: DocumentState,
    deferred_fields: list[str] | None = None,
    clarification_fields: list[str] | None = None,
) -> list[str]:
    """
    Identify which required fields are still unknown.

    Returns a list of human-readable field descriptions.
    This is used by the LLM to decide what to ask next,
    but the questions themselves are generated by the LLM.
    """
    unknown = identify_pending_field_names(state, deferred_fields, clarification_fields)

    # Map internal field names to human-readable descriptions
    field_labels = {
        "full_name": "full legal name",
        "home_address": "home address",
        "covers_worldwide_assets": "whether document covers worldwide assets",
        "has_children": "whether they have children",
        "children_names": "children's names (if applicable)",
        "executor": "executor details (name and relationship)",
        "specific_gifts": "any specific gifts to leave",
        "additional_wishes": "any additional wishes or instructions"
    }

    questions = []
    for field in unknown:
        if field in field_labels:
            questions.append(field_labels[field])

    return questions
