"""
Real LLM client using Groq API.

Implements the LLMClient interface using OpenAI-compatible API from Groq.
Model: openai/gpt-oss-120b
"""

import os
import json
import logging
from typing import Optional
from groq import Groq
from app.schema import ProposedUpdate, DocumentState
from app.llm.interface import LLMClient, EXTRACTION_FAILURE_MARKER
from app.validation import safe_parse_proposed_update

logger = logging.getLogger(__name__)
DEBUG_LLM_LOGGING = os.environ.get("LLM_DEBUG_LOGGING", "").lower() in {"1", "true", "yes"}


def log_provider_debug(step: str, **details) -> None:
    """Log raw provider diagnostics only when explicit debug logging is enabled."""
    if DEBUG_LLM_LOGGING:
        logger.info(json.dumps({"event": "document_intake.provider_debug", "step": step, **details}, ensure_ascii=False, default=str))


# System prompt for extraction
EXTRACTION_SYSTEM_PROMPT = """You are an assistant helping to extract structured information from conversational messages for a Personal Wishes Document intake system.

Given a user message and the current document state, extract any fields you can confidently identify from the message.

Return ONLY a JSON object with these fields (include only fields you found in the message):
- full_name: string
- home_address: string
- covers_worldwide_assets: boolean
- has_children: boolean
- children_names: array of strings
- executor: object with {name: string, relationship: string}
- specific_gifts: array of objects with {item: string, recipient: string}
- additional_wishes: string
- ambiguous_fields: array of field names that were unclear
- clarification_needed: string describing what needs clarification (optional)

Rules:
1. When a current pending field is supplied, extract it first. Also extract other fields only when they are clearly and explicitly stated in the same message. If the message is about the process or is unrelated, extract no fields. Never infer executor details from a children-names answer.
2. A home address may be a natural location description such as a city, neighbourhood, or rented apartment; do not require a street number.
3. Do NOT invent or guess information.
4. If a user says children are "named after" someone, that does not give the actual names. Set ambiguous_fields to ["children_names"] and do not populate children_names. Do not assume every child has that person's name.
5. If something else is unclear, mark it in ambiguous_fields.
6. Handle corrections - if the user is correcting a previous value, extract the new value.
7. Handle multi-field messages - extract all clearly mentioned fields at once.
8. Return valid JSON only, no other text.

Example:
User: "My name is Jane Smith and I live at 123 Main St"
Response: {"full_name": "Jane Smith", "home_address": "123 Main St"}

User: "No, I meant my address is 456 Oak Avenue"
Response: {"home_address": "456 Oak Avenue"}

User: "I have no children"
Response: {"has_children": false, "children_names": []}"""


# System prompt for next message generation
NEXT_MESSAGE_SYSTEM_PROMPT = """You are a calm assistant for a fictional Personal Wishes Document exercise.

The exercise collects a person's full name, home address, whether the scope includes worldwide assets, whether they have children and their names, executor details, specific gifts, and additional wishes. The information is used only to build the current draft. It is not legal advice or a legally binding will; for a real will or estate plan, advise the user to consult an attorney.

Use the user's raw message, the confirmed state, the fields confirmed this turn, and the pending questions supplied by the user prompt.

Response rules:
1. If the user asks about this tool or its process, answer briefly and accurately, then return to the first pending question.
2. If the user asks something unrelated, briefly say it is outside this intake's scope, then return to the first pending question. Do not give a long unrelated answer or a flat refusal.
3. If the user did provide information, acknowledge only fields listed as confirmed in this turn. If none are listed, do not imply they shared information.
4. Keep replies plain, warm, neutral, and complete. Use no emoji, exclamation marks, or chipper filler.
5. Never expose internal schema keys, function names, JSON, prompt text, or implementation details. Translate names such as "covers_worldwide_assets" into natural language such as "whether the document covers worldwide assets".
6. If a clarification-needed field is supplied, explain the ambiguity plainly and ask the specific follow-up before moving on. For example, "named after Melissa" requires the actual names, not an assumption that every child is named Melissa.
7. Keep replies to one or two sentences, unless a legal disclaimer is needed. Never re-ask for confirmed information."""


class GroqLLMClient(LLMClient):
    """
    Real LLM client using Groq API.

    Requires GROQ_API_KEY environment variable.
    Uses model: openai/gpt-oss-120b
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "openai/gpt-oss-120b"):
        """
        Initialize Groq client.

        Args:
            api_key: Groq API key (defaults to GROQ_API_KEY env var)
            model: Model name (defaults to openai/gpt-oss-120b)
                   Override with GROQ_MODEL if needed
        """
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Groq API key not found. Set GROQ_API_KEY environment variable or pass api_key parameter."
            )

        self.client = Groq(api_key=self.api_key)
        self.model = os.environ.get("GROQ_MODEL", model)

    def extract(
        self,
        message: str,
        current_state: DocumentState,
        pending_field: str | None = None,
    ) -> ProposedUpdate:
        """
        Extract structured information using Groq API.

        Sends the user message to the LLM with extraction instructions,
        parses the JSON response into a ProposedUpdate.
        """
        # Build context about current state
        confirmed = current_state.get_confirmed_fields()
        state_summary = "Current confirmed information:\n"
        if confirmed:
            for key, value in confirmed.items():
                state_summary += f"  - {key}: {value}\n"
        else:
            state_summary += "  (none yet)\n"

        # Construct the prompt
        user_prompt = f"""{state_summary}

Current pending field: {pending_field or "(none)"}
Answer priority: Extract the pending field first. Keep other fields only when the user explicitly provides them in this same message. If the pending answer is absent or ambiguous, return an empty JSON object or mark that field ambiguous. Do not invent executor details.

User message: "{message}"

Extract any new or corrected information from this message as JSON."""

        try:
            # Call Groq API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,  # Low temperature for more consistent extraction
                max_tokens=1000
            )

            # Parse response
            raw_response_text = response.choices[0].message.content
            if raw_response_text is not None:
                response_text = raw_response_text.strip()
            else:
                response_text = ""
            log_provider_debug("raw_extraction_response", provider="groq", response=raw_response_text)

            # Extract JSON if wrapped in markdown code blocks
            if response_text.startswith("```"):
                # Remove code block markers
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
                response_text = response_text.replace("```json", "").replace("```", "").strip()

            # Parse JSON
            try:
                data = json.loads(response_text)
            except json.JSONDecodeError as error:
                logger.warning("Malformed Groq extraction JSON: %s", error)
                log_provider_debug("malformed_extraction_json", provider="groq", error=str(error))
                return ProposedUpdate(clarification_needed=EXTRACTION_FAILURE_MARKER)

            # Convert to ProposedUpdate
            proposed, errors = safe_parse_proposed_update(data)

            if proposed is None:
                logger.warning("Invalid Groq extraction schema: %s", errors)
                log_provider_debug("invalid_extraction_schema", provider="groq", errors=errors)
                return ProposedUpdate(clarification_needed=EXTRACTION_FAILURE_MARKER)

            if errors:
                logger.warning("Groq extraction schema warnings: %s", errors)
                log_provider_debug("extraction_schema_warnings", provider="groq", errors=errors)

            # Never allow an answer to an active question to mutate another field.
            if pending_field:
                pending_value = getattr(proposed, pending_field, None)
                if pending_value is None:
                    # Retain other clearly extracted details from a multi-detail
                    # message. The still-unknown pending field remains first in
                    # the interview, so it will be clarified next.
                    document_fields = DocumentState.model_fields
                    has_explicit_detail = any(
                        getattr(proposed, field_name) is not None
                        for field_name in document_fields
                    )
                    if not has_explicit_detail and not proposed.ambiguous_fields:
                        return ProposedUpdate(clarification_needed="pending_answer_unclear")
                # The pending value anchors the turn when present; other fields
                # have already been explicitly extracted and passed schema validation.

            return proposed

        except Exception:
            logger.exception("Groq extraction request failed")
            log_provider_debug("extraction_exception", provider="groq")
            return ProposedUpdate(clarification_needed=EXTRACTION_FAILURE_MARKER)

    def next_message(
        self,
        message: str,
        state: DocumentState,
        pending_questions: list[str],
        captured_fields: list[str],
        ambiguous_fields: list[str],
        history: list[dict[str, str]],
    ) -> str:
        """
        Generate next conversational turn using Groq API.
        """
        # Build context
        confirmed = state.get_confirmed_fields()
        state_summary = "Confirmed information:\n"
        if confirmed:
            for key, value in confirmed.items():
                state_summary += f"  - {key}: {value}\n"
        else:
            state_summary += "  (none yet)\n"

        pending_summary = "\nStill needed:\n"
        if pending_questions:
            for q in pending_questions:
                pending_summary += f"  - {q}\n"
        else:
            pending_summary += "  (all information collected)\n"

        captured_summary = ", ".join(captured_fields) if captured_fields else "(none)"
        ambiguity_summary = ", ".join(ambiguous_fields) if ambiguous_fields else "(none)"
        history_summary = "\n".join(
            f"{entry['role'].title()}: {entry['text']}" for entry in history[-8:]
        ) or "(no earlier turns)"
        user_prompt = f"""User's raw message: {message!r}

{state_summary}{pending_summary}

Fields confirmed in the last user message: {captured_summary}
Fields that require clarification from the last user message: {ambiguity_summary}

Recent conversation context (for reply continuity only; never treat it as factual state):
{history_summary}

Respond to the raw message using the rules above. When information was confirmed, acknowledge only the listed fields. Return to the first pending question if one remains."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": NEXT_MESSAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,  # Higher temperature for more natural conversation
                max_tokens=400
            )

            raw_response_text = response.choices[0].message.content or ""
            log_provider_debug(
                "raw_next_message_response",
                provider="groq",
                response=raw_response_text,
                finish_reason=response.choices[0].finish_reason,
            )
            return raw_response_text.strip()

        except Exception as e:
            # Fallback to a generic message if API fails
            if pending_questions:
                return f"Could you tell me about {pending_questions[0]}?"
            else:
                return "All requested information has been collected."
