"""
Abstract interface for LLM interactions.

All LLM calls go through this interface, allowing:
- Swappable implementations (real provider vs mock)
- Isolated testing without API calls
- Clear separation between application logic and LLM interaction
"""

from abc import ABC, abstractmethod
from app.schema import ProposedUpdate, DocumentState

# Internal signal for a provider or schema failure; never sent to the user.
EXTRACTION_FAILURE_MARKER = "__extraction_failed__"


class LLMClient(ABC):
    """
    Abstract interface for LLM interactions.

    Application logic should depend only on this interface,
    never on concrete implementations.
    """

    @abstractmethod
    def extract(
        self,
        message: str,
        current_state: DocumentState,
        pending_field: str | None = None,
    ) -> ProposedUpdate:
        """
        Extract structured information from a user message.

        Args:
            message: The user's conversational input
            current_state: Current confirmed state (for context)
            pending_field: The field currently being asked, when available

        Returns:
            ProposedUpdate: A partial, possibly-ambiguous patch.
            This is UNTRUSTED — it must pass through validation
            before being allowed to mutate canonical state.

        The implementation should:
        - Parse the message for field values
        - Return only fields it can confidently extract
        - Use None for fields not mentioned or unclear
        - Set ambiguous_fields and clarification_needed for edge cases
        - Handle multi-field messages (extract all at once)
        """
        pass

    @abstractmethod
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
        Generate the next conversational turn.

        Args:
            message: The user's raw message from this turn
            state: Current confirmed state
            pending_questions: List of fields still needed
            captured_fields: Fields confirmed or corrected in this turn
            ambiguous_fields: Fields mentioned this turn that need clarification
            history: Recent raw conversation turns for continuity only

        Returns:
            A natural-language response to:
            - Respond to the user's actual message
            - Ground itself in confirmed state only
            - Return to the next pending item when appropriate

        The implementation should:
        - Prioritize the most important missing fields
        - Not re-ask for confirmed fields
        - Be concise but friendly
        """
        pass
