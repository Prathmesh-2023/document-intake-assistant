"""
FastAPI application for Document Intake Assistant.

Main entry point with all routes and turn orchestration logic.
"""

import os
import json
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from app.schema import (
    CreateSessionResponse,
    MessageRequest,
    MessageResponse,
    StateResponse,
    DocumentResponse,
    HistoryResponse,
    HistoryMessage,
    DocumentState,
    ProposedUpdate,
)
from app.state import (
    SessionStore,
    merge_update_into_state,
    identify_pending_field_names,
    identify_pending_questions,
)
from app.validation import validate_proposed_update
from app.document import render_document
from app.llm.interface import LLMClient, EXTRACTION_FAILURE_MARKER
from app.llm.mock_client import MockLLMClient
from app.llm.real_client import GroqLLMClient

logger = logging.getLogger(__name__)
DEBUG_LLM_LOGGING = os.environ.get("LLM_DEBUG_LOGGING", "").lower() in {"1", "true", "yes"}
if DEBUG_LLM_LOGGING:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log_turn_debug(session_id: str, step: str, **details) -> None:
    """Write opt-in structured turn diagnostics, including state and extraction data."""
    record = {
        "event": "document_intake.turn_debug",
        "session_id": session_id,
        "step": step,
        **details,
    }
    if DEBUG_LLM_LOGGING:
        logger.info(json.dumps(record, ensure_ascii=False, default=str))


PENDING_FIELD_LABELS = {
    "full_name": "your full name",
    "home_address": "your home address",
    "covers_worldwide_assets": "whether the document covers worldwide assets",
    "has_children": "whether you have children",
    "children_names": "the children's names",
    "executor": "the executor's name and relationship",
    "specific_gifts": "the specific gifts",
    "additional_wishes": "your additional wishes",
}


def clarification_reply(pending_field: str | None) -> str:
    """Create a safe user-facing clarification without internal error details."""
    label = PENDING_FIELD_LABELS.get(pending_field or "", "that information")
    return f"I couldn't confidently understand that answer. Could you provide {label} again?"


# --- LLM provider selection ---

def create_llm_client() -> LLMClient:
    """
    Create LLM client based on environment configuration.

    Checks LLM_PROVIDER env var:
    - "mock" (or unset): Use MockLLMClient
    - "groq" or "real": Use GroqLLMClient

    Falls back to mock if real provider fails to initialize.
    """
    provider = os.environ.get("LLM_PROVIDER", "mock").lower()

    if provider in ["groq", "real"]:
        try:
            return GroqLLMClient()
        except ValueError as e:
            # API key missing - fall back to mock with warning
            print(f"Warning: Failed to initialize Groq client ({e}). Falling back to mock mode.")
            return MockLLMClient()
    else:
        # Default to mock
        return MockLLMClient()


# --- FastAPI app setup ---

app = FastAPI(
    title="Document Intake Assistant",
    description="Personal Wishes Document intake system with conversational interface",
    version="1.0.0"
)

# CORS middleware for frontend (when built)
app.add_middleware(
    CORSMiddleware,
    # These are the local static-server origins used by the plain frontend.
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# Global session store and LLM client
session_store = SessionStore()
llm_client = create_llm_client()


# --- Routes ---

@app.get("/")
async def root():
    """Health check endpoint."""
    provider = "mock" if isinstance(llm_client, MockLLMClient) else "groq"
    return {
        "service": "Document Intake Assistant",
        "status": "running",
        "llm_provider": provider
    }


@app.post("/session", response_model=CreateSessionResponse)
async def create_session():
    """
    Create a new conversation session.

    Returns a unique session_id to use in subsequent requests.
    """
    session_id = session_store.create_session()
    return CreateSessionResponse(session_id=session_id)


@app.post("/session/{session_id}/message", response_model=MessageResponse)
async def post_message(session_id: str, request: MessageRequest):
    """
    Process a user message and return the assistant's response.

    This is the main turn orchestration:
    1. Retrieve current state
    2. Extract proposed update from message via LLM
    3. Validate the proposed update
    4. Merge valid updates into state
    5. Identify pending questions
    6. Generate next message via LLM
    7. Render current document
    8. Return everything to the UI
    """
    # Check session exists
    current_state = session_store.get_state(session_id)
    if current_state is None:
        log_turn_debug(session_id, "session_lookup", found=False, message=request.message)
        raise HTTPException(status_code=404, detail="Session not found")
    log_turn_debug(
        session_id,
        "session_lookup",
        found=True,
        message=request.message,
        state_before=current_state.model_dump(mode="json"),
    )

    # History is reply context only; structured state remains the sole source
    # for validation, merging, and document rendering.
    deferred_fields = session_store.get_deferred_fields(session_id)
    pending_field_names = identify_pending_field_names(
        current_state,
        deferred_fields,
        session_store.get_clarification_fields(session_id),
    )
    pending_field = pending_field_names[0] if pending_field_names else None
    session_store.append_history(session_id, "user", request.message)

    # Step 1: Extract proposed update from user message
    try:
        proposed_update = llm_client.extract(request.message, current_state, pending_field)
    except Exception:
        logger.exception("LLM extraction failed for session %s", session_id)
        log_turn_debug(session_id, "extraction_exception")
        proposed_update = ProposedUpdate(clarification_needed="extraction_failed")
    log_turn_debug(
        session_id,
        "extraction_result",
        raw_client_output=proposed_update.model_dump(mode="json"),
    )

    # Step 2: Validate the proposed update
    validation_result = validate_proposed_update(proposed_update, current_state)
    accepted_fields = {
        name: getattr(validation_result.safe_update, name)
        for name in DocumentState.model_fields
        if getattr(validation_result.safe_update, name) is not None
    }
    log_turn_debug(
        session_id,
        "validation_result",
        accepted=validation_result.is_valid,
        accepted_fields=accepted_fields,
        rejected_reasons=validation_result.errors,
        warnings=validation_result.warnings,
    )
    captured_fields = list(accepted_fields) if validation_result.is_valid else []

    # Step 3: Merge if valid
    if validation_result.is_valid:
        current_state = merge_update_into_state(current_state, validation_result.safe_update)
        session_store.update_state(session_id, current_state)
        if proposed_update.defer_current_question:
            if pending_field is not None:
                session_store.defer_field(session_id, pending_field)
        elif captured_fields:
            session_store.clear_deferred_fields(session_id, captured_fields)
        session_store.prioritize_clarification_fields(
            session_id, proposed_update.ambiguous_fields
        )
        if captured_fields:
            session_store.clear_clarification_fields(session_id, captured_fields)

    persisted_state=session_store.get_state(session_id)
    if persisted_state is not None:
        persisted_state_dump = persisted_state.model_dump(mode="json")
    else:
        persisted_state_dump = None

    log_turn_debug(
        session_id,
        "state_after_merge",
        state_after=current_state.model_dump(mode="json"),
        persisted_state=persisted_state_dump,
    )

    # Step 4: Identify what's still pending
    pending_questions = identify_pending_questions(
        current_state,
        session_store.get_deferred_fields(session_id),
        session_store.get_clarification_fields(session_id),
    )

    # Step 5: Generate next message
    # If validation failed, inform the user
    if not validation_result.is_valid:
        logger.warning(
            "Rejected LLM update for session %s: %s",
            session_id,
            validation_result.errors,
        )
        assistant_reply = clarification_reply(pending_field)
    # Provider and schema failures need a safe fixed clarification. For an
    # ordinary unclear answer, let the reply model address the raw message.
    elif proposed_update.clarification_needed == EXTRACTION_FAILURE_MARKER:
        assistant_reply = clarification_reply(pending_field)
    else:
        try:
            assistant_reply = llm_client.next_message(
                request.message,
                current_state,
                pending_questions,
                captured_fields,
                proposed_update.ambiguous_fields,
                session_store.get_history(session_id, limit=8),
            )
            log_turn_debug(
                session_id,
                "next_message_result",
                state_passed=current_state.model_dump(mode="json"),
                pending_questions=pending_questions,
                captured_fields=captured_fields,
                assistant_reply=assistant_reply,
            )
        except Exception as error:
            logger.exception("LLM reply generation failed for session %s", session_id)
            log_turn_debug(session_id, "next_message_exception")
            assistant_reply = clarification_reply(pending_field)
            log_turn_debug(
                session_id,
                "next_message_fallback",
                state_passed=current_state.model_dump(mode="json"),
                pending_questions=pending_questions,
                assistant_reply=assistant_reply,
            )

    # Step 6: Render current document
    document = render_document(current_state)
    log_turn_debug(
        session_id,
        "turn_complete",
        assistant_reply=assistant_reply,
        pending_questions=pending_questions,
    )

    session_store.append_history(session_id, "assistant", assistant_reply)

    # Step 7: Return full response
    return MessageResponse(
        assistant_reply=assistant_reply,
        state=current_state,
        document=document,
        pending_questions=pending_questions
    )


@app.get("/session/{session_id}/history", response_model=HistoryResponse)
async def get_history(session_id: str):
    """Retrieve the raw transcript recorded for a session."""
    if not session_store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    
    history = [
        HistoryMessage.model_validate(entry)
        for entry in session_store.get_history(session_id)
    ]
    return HistoryResponse(history=history)

@app.get("/session/{session_id}/state", response_model=StateResponse)
async def get_state(session_id: str):
    """
    Retrieve the current state for a session.
    """
    state = session_store.get_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return StateResponse(state=state)


@app.get("/session/{session_id}/document", response_model=DocumentResponse)
async def get_document(session_id: str):
    """
    Retrieve the current rendered document for a session.
    """
    state = session_store.get_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")

    document = render_document(state)
    return DocumentResponse(document=document)


# --- For local development ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
