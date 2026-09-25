"""
Data models and API contracts for the Document Intake Assistant.

Defines:
- Core state schema with status tracking for each field
- Proposed updates from LLM (untrusted until validated)
- API request/response models
"""

from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


# --- Core state models ---

class Executor(BaseModel):
    """Executor details."""
    name: str
    relationship: str


class Gift(BaseModel):
    """A specific gift bequest."""
    item: str
    recipient: str


class FieldStatus(BaseModel):
    """Tracks whether a field value is known and confirmed."""
    status: Literal["unknown", "confirmed"]
    value: Optional[str | bool | list | dict] = None


class DocumentState(BaseModel):
    """
    Canonical state of the document.

    Each top-level field tracks both its value and confirmation status.
    Never rely on None/empty alone to signal "not yet provided" —
    the status field is the source of truth.
    """
    full_name: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown"))
    home_address: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown"))
    covers_worldwide_assets: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown"))
    has_children: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown"))
    children_names: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown", value=[]))
    executor: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown"))
    specific_gifts: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown", value=[]))
    additional_wishes: FieldStatus = Field(default_factory=lambda: FieldStatus(status="unknown"))

    def get_confirmed_fields(self) -> dict[str, Any]:
        """Extract only confirmed field values as a plain dict."""
        result = {}
        for field_name, field_status in self.model_dump().items():
            if field_status["status"] == "confirmed":
                result[field_name] = field_status["value"]
        return result

    def get_unknown_fields(self) -> list[str]:
        """Return list of field names that are still unknown."""
        unknown = []
        for field_name, field_status in self.model_dump().items():
            if field_status["status"] == "unknown":
                unknown.append(field_name)
        return unknown


# --- LLM interaction models ---

class ProposedUpdate(BaseModel):
    """
    A partial, possibly-ambiguous patch proposed by the LLM.

    This is UNTRUSTED input — it must pass through validation
    before being allowed to mutate canonical state.

    Each field maps to:
    - A concrete value if the LLM extracted one with confidence
    - None if the field wasn't mentioned or couldn't be extracted

    A field being present here doesn't mean it's valid or should
    be confirmed — that's determined by validation.
    """
    full_name: Optional[str] = None
    home_address: Optional[str] = None
    covers_worldwide_assets: Optional[bool] = None
    has_children: Optional[bool] = None
    children_names: Optional[list[str]] = None
    executor: Optional[Executor] = None
    specific_gifts: Optional[list[Gift]] = None
    additional_wishes: Optional[str] = None

    # Metadata from the LLM about its confidence/ambiguity
    ambiguous_fields: list[str] = Field(default_factory=list)
    clarification_needed: Optional[str] = None
    defer_current_question: bool = False


# --- API contracts ---

class CreateSessionResponse(BaseModel):
    """Response from POST /session"""
    session_id: str


class MessageRequest(BaseModel):
    """Request to POST /session/{id}/message"""
    message: str


class MessageResponse(BaseModel):
    """Response from POST /session/{id}/message"""
    assistant_reply: str
    state: DocumentState
    document: str
    pending_questions: list[str]


class StateResponse(BaseModel):
    """Response from GET /session/{id}/state"""
    state: DocumentState


class DocumentResponse(BaseModel):
    """Response from GET /session/{id}/document"""
    document: str


class HistoryMessage(BaseModel):
    """One conversational turn retained only as reply context."""
    role: Literal["user", "assistant"]
    text: str
    timestamp: str


class HistoryResponse(BaseModel):
    """Response from GET /session/{id}/history."""
    history: list[HistoryMessage]
