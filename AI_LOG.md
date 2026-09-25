# AI Execution Log

## Purpose

This log records the key implementation decisions and corrections made while building the Document Intake Assistant. It is a development review artifact, not a runtime transcript and not a source of document state.

## Key prompts and work completed

### Backend conversation flow

**Prompt:** Build a conversational intake flow that extracts structured information, validates it, merges confirmed fields into state, and renders a draft.

**Implemented:**

- FastAPI session endpoints for messages, state, document, and history.
- A `DocumentState` model with confirmed or unknown status for every collected field.
- Extraction → validation → merge flow where only validated values update the draft.
- Mock and Groq-backed LLM clients behind a shared interface.

### Frontend

**Prompt:** Build a plain HTML, CSS, and JavaScript interface with chat on the left and document/details views on the right.

**Implemented:**

- Static `frontend/` files with no framework or build step.
- Responsive split layout, document preview, structured Details checklist, provider badge, and mock-mode phrasing hints.
- Confirmed details are visually distinguished from unknown details.

### Conversation context and mock mode

**Prompt:** Keep conversation history for reply continuity while treating structured state as the only source for validation and document generation.

**Implemented:**

- Per-session role, text, and timestamp history.
- The latest conversation turns are passed only to reply generation.
- Regex-based mock extraction supports common answers, lists, skips, and clarifications without an API key.

## Notable iterations and corrections

### Repeated pending question

**Observed output:** The assistant repeatedly asked about worldwide assets after answers such as “yeah”, “yes”, and “assets in a specific country.”

**Cause:** The mock extraction path was not reliably mapping short answers to the active pending field.

**Correction:** The current pending field is now passed into extraction. Mock rules use it to interpret short answers, and tests verify state advances to the next question.

### Conversation responses ignored the user

**Observed output:** A process question such as “what is this draft used for?” received the same address request as before.

**Cause:** The reply path treated every message only as field input.

**Correction:** The reply model receives the raw message, confirmed state, pending questions, and recent history. It can answer process questions briefly, redirect unrelated questions, and then continue the intake.

### Raw Pydantic errors in chat

**Observed output:** Internal messages such as `executor.relationship Field required` appeared in the chat.

**Cause:** Validation errors were concatenated directly into the assistant response.

**Correction:** Detailed errors are logged server-side. The user receives a clean clarification tied to the pending field, and invalid updates never merge into state.

### Wrong field selected during children-name intake

**Observed output:** A list of children’s names could be interpreted as executor data.

**Cause:** Extraction did not consistently anchor an answer to the active question.

**Correction:** The extraction prompt and client logic prioritize the active field. A names-list regression test confirms that `children_names` updates while `executor` remains untouched.

### “Named after” mistaken for actual names

**Observed output:** “My children are named after my wife Melissa” was expected to produce children’s names.

**Review:** This phrase explains the inspiration for the names; it does not state the actual names. Assuming every child is named Melissa would create incorrect state.

**Correction:** The assistant records any clear related facts, marks `children_names` as ambiguous, keeps that clarification at the front of the interview, and asks for each child’s actual name.

### Multiple explicit details in one message

**Observed output:** A message containing an address, executor, and children could save only the currently pending address.

**Cause:** The active-field guard discarded other valid explicit values.

**Correction:** The active field is extracted first, but other clearly stated fields from the same message are retained after validation. Malformed nested data does not prevent unrelated valid values from being saved.

## Test coverage added during iteration

- Short yes/no and free-text worldwide-assets answers advance the state.
- Mock clean answer, list answer, skip answer, and unmatched answer fixtures.
- Conversation history endpoint stores user and assistant turns.
- Children-name list updates only `children_names`, not executor fields.
- Malformed provider output is logged internally and never returned to the user.
- Multi-detail extraction retains valid address and family details.
- “Named after” input stays ambiguous and prompts for actual names.

## Current design rules

1. Structured state is the source of truth for validation, merging, and draft generation.
2. History is context for conversational replies only.
3. An active pending field guides extraction, but clearly explicit extra details can be collected in the same message.
4. Ambiguous details do not enter state; they receive a direct follow-up.
5. Provider, parsing, and validation internals are never shown in the chat.
6. Mock mode remains functional without an API key.
