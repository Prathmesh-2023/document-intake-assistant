"""
Mock LLM client for testing and development.

Provides deterministic, fixture-based responses.
Fully functional without any API keys.
"""

import re

from app.schema import ProposedUpdate, DocumentState, Executor, Gift
from app.llm.interface import LLMClient


class MockLLMClient(LLMClient):
    """
    Deterministic mock implementation for testing.

    Returns fixture-based responses without any external API calls.
    This is a first-class mode, not an afterthought — the entire
    system should be testable using only this client.
    """

    @staticmethod
    def _first_pending_field(state: DocumentState) -> str | None:
        """Return the first unanswered field in the same order used by the interview."""
        unknown = state.get_unknown_fields()
        if state.has_children.status == "confirmed" and state.has_children.value is False:
            unknown = [field for field in unknown if field != "children_names"]
        return unknown[0] if unknown else None

    @staticmethod
    def _correction_field_aliases() -> dict[str, str]:
        return {
            "full legal name": "full_name",
            "full name": "full_name",
            "name": "full_name",
            "home address": "home_address",
            "address": "home_address",
            "worldwide assets": "covers_worldwide_assets",
            "document scope": "covers_worldwide_assets",
            "scope": "covers_worldwide_assets",
            "children names": "children_names",
            "kids names": "children_names",
            "children": "children_names",
            "kids": "children_names",
            "executor": "executor",
            "specific gifts": "specific_gifts",
            "gifts": "specific_gifts",
            "additional wishes": "additional_wishes",
            "wishes": "additional_wishes",
        }

    @classmethod
    def _extract_correction(
        cls, message: str, current_state: DocumentState, pending_field: str | None
    ) -> ProposedUpdate | None:
        aliases = cls._correction_field_aliases()
        alias_pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
        correction_match = re.fullmatch(
            rf"(?:change|update) my (?P<field>{alias_pattern}) to (?P<value>.+?)\.?|"
            rf"actually,? my (?P<actual_field>{alias_pattern}) is (?P<actual_value>.+?)\.?|"
            rf"make my (?P<make_field>{alias_pattern}) (?P<make_value>.+?)\.?",
            message.strip(),
            re.IGNORECASE,
        )

        value = None
        field_name = None
        if correction_match:
            field_alias = (
                correction_match.group("field")
                or correction_match.group("actual_field")
                or correction_match.group("make_field")
            ).lower()
            field_name = aliases[field_alias]
            value = (
                correction_match.group("value")
                or correction_match.group("actual_value")
                or correction_match.group("make_value")
            ).strip()
        else:
            recently_relevant = pending_field or (
                next(
                    (field for field in aliases.values() if current_state.__getattribute__(field).status == "confirmed"),
                    None,
                )
            )
            if recently_relevant and re.fullmatch(
                r"actually it(?:'s| is)\s+.+?\.?", message.strip(), re.IGNORECASE
            ):
                field_name = recently_relevant
                value = re.sub(r"^actually it(?:'s| is)\s+", "", message.strip(), flags=re.IGNORECASE).rstrip(".").strip()

        if not field_name or not value:
            return None

        update = ProposedUpdate()
        if field_name == "full_name":
            update.full_name = value
        elif field_name == "home_address":
            update.home_address = value
        elif field_name == "covers_worldwide_assets":
            value_lower = value.lower()
            if re.search(r"worldwide|world|global|all countries|everywhere", value_lower):
                update.covers_worldwide_assets = True
            elif re.search(r"country|local|not worldwide|one country", value_lower):
                update.covers_worldwide_assets = False
            else:
                return None
        elif field_name == "children_names":
            update.children_names = [name.strip() for name in re.split(r",|\band\b", value, flags=re.IGNORECASE) if name.strip()]
        elif field_name == "executor":
            existing = current_state.executor.value if current_state.executor.status == "confirmed" else {}
            relationship = existing.get("relationship", "") if isinstance(existing, dict) else ""
            update.executor = Executor(name=value, relationship=relationship)
        elif field_name == "specific_gifts":
            gift_match = re.fullmatch(r"(.+?)\s+(?:to|for)\s+(.+)", value, re.IGNORECASE)
            if not gift_match:
                return None
            update.specific_gifts = [Gift(item=gift_match.group(1).strip(), recipient=gift_match.group(2).strip())]
        elif field_name == "additional_wishes":
            update.additional_wishes = value
        return update

    def extract(
        self,
        message: str,
        current_state: DocumentState,
        pending_field: str | None = None,
    ) -> ProposedUpdate:
        """
        Extract information using simple pattern matching.

        This is deliberately simple — just enough to exercise the
        validation and merge logic in tests.
        """
        message_lower = message.lower()
        answer = message_lower.strip().rstrip(".!?, ")
        pending_field = pending_field or self._first_pending_field(current_state)
        update = ProposedUpdate()

        correction = self._extract_correction(message, current_state, pending_field)
        if correction is not None:
            return correction

        if answer in {
            "not sure", "unsure", "don't know", "do not know", "skip", "later",
            "prefer not to say",
        }:
            update.defer_current_question = True
            return update

        # Extract an explicitly introduced name.
        name_match = re.search(
            r"(?:my name is|i am|i'm)\s+([\w'-]+(?:\s+[\w'-]+){0,3})",
            message,
            re.IGNORECASE,
        )
        if name_match:
            potential_name = name_match.group(1).strip()
            # Stop before another clause in a multi-field message.
            potential_name = re.split(r"\s+(?:and|but)\s+|,|;|\.", potential_name, maxsplit=1)[0]
            if potential_name:
                update.full_name = potential_name
        elif (
            pending_field == "full_name"
            and not re.match(r"^(?:what|why|how|who|where|when|hey|hello)", message_lower)
            and "?" not in message
            and re.fullmatch(
                r"[A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,3}", message.strip()
            )
        ):
            update.full_name = message.strip()

        # Extract address
        address_match = re.search(
            r"(?:address is|address:|live at|live in|living in|residing at)\s+(.+?)(?=[.;]|\s+(?:with|and)\s+my\s+(?:executor|children)|$)",
            message,
            re.IGNORECASE,
        )
        if address_match:
            potential_address = address_match.group(1).strip().rstrip(",")
            if potential_address and len(potential_address) < 500:
                update.home_address = potential_address
        elif pending_field == "home_address" and (re.search(r"\d", message) or re.search(r"\b(?:live|living|residing)\s+(?:in|at)\b", message_lower)) and len(message) < 500:
            update.home_address = message.strip().rstrip(".")

        # A short yes/no answer applies to the first unanswered question.
        negative_answers = {"no", "nope", "nah"}
        positive_answers = {"yes", "yeah", "yep", "yup", "sure", "correct"}

        # Extract has_children
        no_children = re.search(
            r"\b(?:no children|no kids|don't have (?:any )?(?:children|kids)|do not have (?:any )?(?:children|kids))\b",
            message_lower,
        )
        if no_children or (pending_field == "has_children" and answer in negative_answers):
            update.has_children = False
            update.children_names = []
        elif "have children" in message_lower or "have kids" in message_lower or "i have" in message_lower and ("child" in message_lower or "kids" in message_lower) or re.search(r"\bmy\s+\d+\s+(?:children|kids)\b", message_lower):
            update.has_children = True
        elif pending_field == "has_children" and answer in positive_answers:
            update.has_children = True

        if re.search(r"\b(?:all )?(?:named|name)d? after (?:my )?(?:wife|husband|spouse)\b", message_lower):
            update.ambiguous_fields.append("children_names")

        if pending_field == "children_names" and update.children_names is None and "children_names" not in update.ambiguous_fields:
            names_match = re.search(
                r"(?:children'?s? names? (?:are|is)?|names? (?:are|is)?|called)\s+(.+)",
                message,
                re.IGNORECASE,
            )
            names_text = names_match.group(1) if names_match else message
            names_text = re.sub(r"^(?:it'?s|they are)\s+", "", names_text.strip(), flags=re.IGNORECASE)
            if names_match or re.fullmatch(r"[A-Za-z][A-Za-z' -]*(?:,\s*[A-Za-z][A-Za-z' -]*)+(?:\s+and\s+[A-Za-z][A-Za-z' -]*)?", names_text.strip()):
                names = [name.strip() for name in re.split(r",|\band\b", names_text, flags=re.IGNORECASE)]
                names = [name for name in names if name and len(name) <= 100]
                if names:
                    update.children_names = names

        # Extract worldwide assets
        country_only = re.search(
            r"\b(?:specific country|one country|my country only|country only|country of residence|in my country|local assets)\b",
            message_lower,
        )
        excludes_worldwide = re.search(
            r"\b(?:not worldwide|no worldwide|don't include worldwide|do not include worldwide|just .*country|only .*country)\b",
            message_lower,
        )
        includes_worldwide = re.search(
            r"\b(?:worldwide|world|around the world|globally|all countries|everywhere)\b",
            message_lower,
        )
        if country_only or excludes_worldwide:
            update.covers_worldwide_assets = False
        elif includes_worldwide:
            update.covers_worldwide_assets = True
        elif pending_field == "covers_worldwide_assets" and answer in positive_answers:
            update.covers_worldwide_assets = True
        elif pending_field == "covers_worldwide_assets" and answer in negative_answers:
            update.covers_worldwide_assets = False

        # Extract executor
        relationships = "brother|sister|son|daughter|friend|spouse|wife|husband|partner|cousin|nephew|niece"
        name_words = r"[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,2}"
        executor_match = re.search(
            rf"(?:my\s+)?executor\s+(?:is\s+)?(?:my\s+)?(?P<relationship>{relationships})\s+(?P<name>{name_words})",
            message,
            re.IGNORECASE,
        )
        if executor_match is None:
            executor_match = re.search(
                rf"(?:my\s+)?(?P<relationship>{relationships})\s+(?P<name>{name_words})\s+(?:should be|to be|as)\s+(?:my\s+)?executor",
                message,
                re.IGNORECASE,
            )
        if executor_match is None:
            executor_match = re.search(
                rf"(?P<name>{name_words})\s+(?:should be|to be|as)\s+(?:my\s+)?executor(?:,?\s+my\s+(?P<relationship>{relationships}))?",
                message,
            )
        if executor_match and executor_match.group("relationship"):
            update.executor = Executor(
                name=executor_match.group("name").strip(),
                relationship=executor_match.group("relationship").lower(),
            )
        elif pending_field == "executor":
            executor_match = re.search(
                rf"(?P<name>{name_words})\s*,?\s*(?:my\s+)?(?P<relationship>{relationships})",
                message,
            )
            if executor_match:
                update.executor = Executor(
                    name=executor_match.group("name").strip(),
                    relationship=executor_match.group("relationship").lower(),
                )

        # Explicit negative answers are meaningful confirmations too.
        if re.search(r"\bno (?:specific )?gifts\b|\bnothing to leave\b", message_lower):
            update.specific_gifts = []
        elif pending_field == "specific_gifts":
            gift_match = re.search(r"(?:leave\s+)?(.+?)\s+(?:to|for)\s+(.+)", message, re.IGNORECASE)
            if gift_match:
                update.specific_gifts = [Gift(
                    item=gift_match.group(1).strip(),
                    recipient=gift_match.group(2).strip().rstrip("."),
                )]
        if re.search(r"\bno (?:additional|other) wishes\b", message_lower):
            update.additional_wishes = ""
        # Extract additional wishes
        if "wish" in message_lower or "want to" in message_lower:
            # This is a simple mock — just flag it if mentioned
            if "additional" in message_lower or "also" in message_lower:
                update.additional_wishes = message.strip()
        elif pending_field == "additional_wishes" and len(message.strip()) > 2:
            update.additional_wishes = message.strip()

        return update

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
        Generate a simple next question based on what's pending.

        In mock mode, we use a simple template-based approach.
        """
        if not pending_questions:
            return "All requested information has been collected. The draft is ready for review."

        if "children_names" in ambiguous_fields:
            return "I understand the names are connected to your wife, but I need each child?s actual name. What are their names?"

        # Ask about the first pending question
        first_pending = pending_questions[0]

        question_templates = {
            "full legal name": "What is your full name?",
            "home address": "What is your current home address?",
            "whether document covers worldwide assets": "Should this document cover your worldwide assets, or just assets in a specific country?",
            "whether they have children": "Do you have any children?",
            "children's names (if applicable)": "What are your children's names?",
            "executor details (name and relationship)": "Who would you like to name as your executor, and what is their relationship to you?",
            "any specific gifts to leave": "Are there any specific gifts you'd like to leave to particular people?",
            "any additional wishes or instructions": "Do you have any additional wishes or special instructions you'd like to include?"
        }

        question = question_templates.get(first_pending, f"Could you tell me about: {first_pending}?")

        if not captured_fields:
            if message.lower().strip().rstrip(".!?, ") in {
                "not sure", "unsure", "don't know", "do not know", "skip", "later",
                "prefer not to say",
            }:
                return f"We can return to that later. {question}"
            if len(history) % 4 == 0:
                return f"I could not place that detail yet. {question}"
            return f"I still need {first_pending}. {question}"

        labels = {
            "full_name": "your full name",
            "home_address": "your home address",
            "covers_worldwide_assets": "the document scope",
            "has_children": "your family information",
            "children_names": "your children's names",
            "executor": "your executor",
            "specific_gifts": "your specific gifts",
            "additional_wishes": "your additional wishes",
        }
        captured_labels = [labels[field] for field in captured_fields if field in labels]
        if not captured_labels:
            return question
        if len(captured_labels) == 1:
            acknowledgement = f"I recorded {captured_labels[0]}."
        else:
            acknowledgement = f"I recorded {', '.join(captured_labels[:-1])} and {captured_labels[-1]}."
        return f"{acknowledgement} {question}"
