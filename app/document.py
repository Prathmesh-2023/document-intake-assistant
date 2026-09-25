"""
Pure-code document template renderer.

Generates the Personal Wishes Document from confirmed state.
This is deterministic and testable — no LLM calls.
"""

from app.schema import DocumentState


def render_document(state: DocumentState) -> str:
    """
    Render the Personal Wishes Document from confirmed state.

    Only uses confirmed field values. Unknown fields are rendered
    as placeholder text like "[Not yet provided]".

    Returns:
        A formatted text document with clear legal disclaimer.
    """
    confirmed = state.get_confirmed_fields()

    # Helper to get a value or placeholder
    def get_value(field_name: str, placeholder: str = "[Not yet provided]") -> str:
        value = confirmed.get(field_name)
        if value is None or value == "":
            return placeholder
        return str(value)

    def get_bool_text(field_name: str, yes_text: str, no_text: str, placeholder: str = "[Not yet specified]") -> str:
        value = confirmed.get(field_name)
        if value is None:
            return placeholder
        return yes_text if value else no_text

    # Build the document
    lines = []

    # Header and disclaimer
    lines.append("=" * 70)
    lines.append("PERSONAL WISHES DOCUMENT")
    lines.append("=" * 70)
    lines.append("")
    lines.append("⚠️  IMPORTANT LEGAL DISCLAIMER")
    lines.append("-" * 70)
    lines.append("This is a FICTIONAL document created for demonstration purposes only.")
    lines.append("This document does NOT constitute legal advice.")
    lines.append("This document is NOT a legally binding will or testament.")
    lines.append("For actual estate planning, please consult a qualified attorney.")
    lines.append("-" * 70)
    lines.append("")

    # Personal Information
    lines.append("PERSONAL INFORMATION")
    lines.append("-" * 70)
    lines.append(f"Full Name: {get_value('full_name')}")
    lines.append(f"Home Address: {get_value('home_address')}")
    lines.append("")

    # Scope
    lines.append("DOCUMENT SCOPE")
    lines.append("-" * 70)
    worldwide = get_bool_text(
        'covers_worldwide_assets',
        'This document covers my worldwide assets.',
        'This document covers assets in my country of residence only.',
        '[Scope not yet specified]'
    )
    lines.append(worldwide)
    lines.append("")

    # Family Information
    lines.append("FAMILY INFORMATION")
    lines.append("-" * 70)
    has_children = confirmed.get('has_children')
    if has_children is True:
        children_names = confirmed.get('children_names', [])
        if children_names and len(children_names) > 0:
            lines.append("I have the following children:")
            for name in children_names:
                lines.append(f"  • {name}")
        else:
            lines.append("I have children. [Names not yet provided]")
    elif has_children is False:
        lines.append("I do not have children.")
    else:
        lines.append("[Family information not yet provided]")
    lines.append("")

    # Executor
    lines.append("EXECUTOR")
    lines.append("-" * 70)
    executor = confirmed.get('executor')
    if executor:
        executor_name = executor.get('name', '[Not provided]')
        executor_rel = executor.get('relationship', '[Not provided]')
        lines.append(f"I appoint {executor_name}, my {executor_rel}, as executor of this document.")
    else:
        lines.append("[Executor not yet designated]")
    lines.append("")

    # Specific Gifts
    lines.append("SPECIFIC GIFTS")
    lines.append("-" * 70)
    gifts = confirmed.get('specific_gifts')
    if gifts and len(gifts) > 0:
        lines.append("I wish to leave the following specific gifts:")
        for idx, gift in enumerate(gifts, 1):
            item = gift.get('item', '[Item not specified]')
            recipient = gift.get('recipient', '[Recipient not specified]')
            lines.append(f"{idx}. {item} → to {recipient}")
    else:
        lines.append("No specific gifts designated.")
    lines.append("")

    # Additional Wishes
    lines.append("ADDITIONAL WISHES")
    lines.append("-" * 70)
    additional = confirmed.get('additional_wishes')
    if additional:
        lines.append(additional)
    else:
        lines.append("None specified.")
    lines.append("")

    # Footer
    lines.append("=" * 70)
    lines.append("END OF DOCUMENT")
    lines.append("=" * 70)

    return "\n".join(lines)


def render_document_preview(state: DocumentState) -> str:
    """
    Alias for render_document.

    Kept for semantic clarity — "preview" emphasizes this is
    a live draft that updates as state fills in.
    """
    return render_document(state)
