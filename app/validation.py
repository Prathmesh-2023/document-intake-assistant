"""
Validation layer for proposed updates from the LLM.

LLM output is untrusted input. This module enforces:
- Type correctness
- Cross-field consistency rules
- Rejection of hallucinated fields not in the schema
"""

from typing import Optional
from pydantic import ValidationError as PydanticValidationError
from app.schema import ProposedUpdate, Executor, Gift, DocumentState


class ValidationError(Exception):
    """Raised when a proposed update fails validation."""
    pass


class ValidationResult:
    """Result of validating a proposed update."""

    def __init__(
        self,
        is_valid: bool,
        errors: list[str],
        warnings: Optional[list[str]]  = None,
        safe_update: Optional[ProposedUpdate] = None,
    ): 
        self.is_valid = is_valid
        self.errors = errors
        self.warnings = warnings or []
        self.safe_update = safe_update or ProposedUpdate()  # default to empty update if none provided


def validate_proposed_update(
    proposed: ProposedUpdate,
    current_state: DocumentState
) -> ValidationResult:
    """
    Validate a proposed update against schema and cross-field rules.

    Returns ValidationResult with:
    - is_valid: whether the update can be safely applied
    - errors: blocking issues (type mismatches, contradictions)
    - warnings: non-blocking concerns (ambiguity, missing context)

    Validation rules:
    1. Type checking (already enforced by Pydantic at parse time)
    2. Cross-field consistency:
       - children_names only meaningful if has_children == True
       - If has_children is False, children_names must be empty
    3. Executor must have both name and relationship if present
    4. Specific gifts must have both item and recipient
    5. No hallucinated fields (enforced by Pydantic schema)
    """
    errors = []
    warnings = []

    # Cross-field rule: children_names vs has_children
    if proposed.has_children is not None:
        if proposed.has_children is False:
            # If explicitly saying no children, children_names must be empty
            if proposed.children_names and len(proposed.children_names) > 0:
                errors.append(
                    "Contradiction: has_children is False but children_names is non-empty"
                )
        elif proposed.has_children is True:
            # If has children but no names provided, that's a warning (might come later)
            if proposed.children_names is None or len(proposed.children_names) == 0:
                warnings.append(
                    "has_children is True but no children_names provided yet"
                )

    # If children_names provided without has_children status
    if proposed.children_names and len(proposed.children_names) > 0:
        if proposed.has_children is None:
            if current_state.has_children.status == "unknown":
                warnings.append(
                    "children_names provided but has_children status is unknown"
                )
            elif current_state.has_children.value is False:
                errors.append(
                    "Contradiction: children_names provided but has_children is confirmed False"
                )
    # Executor validation: must have both fields
    if proposed.executor is not None:
        if not proposed.executor.name or not proposed.executor.name.strip():
            errors.append("Executor name is empty or whitespace")
        if not proposed.executor.relationship or not proposed.executor.relationship.strip():
            errors.append("Executor relationship is empty or whitespace")

    # Specific gifts validation: must have both fields
    if proposed.specific_gifts is not None:
        for idx, gift in enumerate(proposed.specific_gifts):
            if not gift.item or not gift.item.strip():
                errors.append(f"Gift #{idx + 1}: item is empty or whitespace")
            if not gift.recipient or not gift.recipient.strip():
                errors.append(f"Gift #{idx + 1}: recipient is empty or whitespace")

    # Check for ambiguity flags from the LLM itself
    if proposed.ambiguous_fields:
        for field in proposed.ambiguous_fields:
            warnings.append(f"LLM flagged '{field}' as ambiguous")

    # String field length sanity checks (prevent absurdly long input)
    if proposed.full_name and len(proposed.full_name) > 200:
        errors.append("full_name exceeds reasonable length (200 chars)")

    if proposed.home_address and len(proposed.home_address) > 500:
        errors.append("home_address exceeds reasonable length (500 chars)")

    if proposed.additional_wishes and len(proposed.additional_wishes) > 2000:
        errors.append("additional_wishes exceeds reasonable length (2000 chars)")

    # Check executor fields length
    if proposed.executor:
        if len(proposed.executor.name) > 200:
            errors.append("executor.name exceeds reasonable length (200 chars)")
        if len(proposed.executor.relationship) > 100:
            errors.append("executor.relationship exceeds reasonable length (100 chars)")

    # Check gift fields length
    if proposed.specific_gifts:
        for idx, gift in enumerate(proposed.specific_gifts):
            if len(gift.item) > 300:
                errors.append(f"Gift #{idx + 1}: item exceeds reasonable length (300 chars)")
            if len(gift.recipient) > 200:
                errors.append(f"Gift #{idx + 1}: recipient exceeds reasonable length (200 chars)")

    # Ambiguous values must not reach canonical state until clarified.
    safe_update = proposed.model_copy(deep=True)
    document_fields = set(DocumentState.model_fields)
    for field_name in proposed.ambiguous_fields:
        if field_name in document_fields:
            setattr(safe_update, field_name, None)
    if "has_children" in proposed.ambiguous_fields:
        safe_update.children_names = None

    is_valid = len(errors) == 0
    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        safe_update=safe_update,
    )


def safe_parse_proposed_update(raw_data: dict) -> tuple[Optional[ProposedUpdate], list[str]]:
    """
    Attempt to parse raw LLM output into a ProposedUpdate.

    Returns:
        (ProposedUpdate | None, list of parse errors)

    If parsing succeeds, returns (update, []).
    If parsing fails, returns (None, [error messages]).

    This catches:
    - Malformed JSON (caller's responsibility to handle before this)
    - Type mismatches (wrong types for fields)
    - Hallucinated fields (extra keys not in schema)
    """
    errors = []

    try:
        # Pydantic will enforce the schema and reject unknown fields
        # if we configure it that way (extra='forbid')
        # For now, it will ignore extra fields by default

        # Filter out any keys that aren't in ProposedUpdate schema
        valid_fields = set(ProposedUpdate.model_fields.keys())
        filtered_data = {k: v for k, v in raw_data.items() if k in valid_fields}

        # Check if any fields were dropped
        dropped = set(raw_data.keys()) - valid_fields
        if dropped:
            errors.append(f"LLM hallucinated fields not in schema: {dropped}")

        proposed = ProposedUpdate.model_validate(filtered_data)
        return proposed, errors

    except PydanticValidationError as error:
        errors.append(f"Failed to parse proposed update: {error}")

        # Discard only malformed top-level fields, then keep any other values
        # that still satisfy the unchanged ProposedUpdate schema.
        invalid_fields = {issue["loc"][0] for issue in error.errors() if issue.get("loc")}
        valid_data = {key: value for key, value in filtered_data.items() if key not in invalid_fields}
        if not valid_data:
            return None, errors
        try:
            return ProposedUpdate.model_validate(valid_data), errors
        except PydanticValidationError as remaining_error:
            errors.append(f"Failed to retain valid proposed fields: {remaining_error}")
            return None, errors
    except Exception as error:
        errors.append(f"Failed to parse proposed update: {error}")
        return None, errors
