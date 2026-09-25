const API_BASE = "http://localhost:8000";

const FIELD_DEFINITIONS = [
  { key: "full_name", label: "Full name" },
  { key: "home_address", label: "Home address" },
  { key: "covers_worldwide_assets", label: "Worldwide assets" },
  { key: "has_children", label: "Children" },
  { key: "children_names", label: "Children's names" },
  { key: "executor", label: "Executor" },
  { key: "specific_gifts", label: "Specific gifts" },
  { key: "additional_wishes", label: "Additional wishes" },
];

const chatLog = document.querySelector("#chat-log");
const chatStatus = document.querySelector("#chat-status");
const messageForm = document.querySelector("#message-form");
const messageInput = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const documentCopy = document.querySelector("#document-copy");
const detailsList = document.querySelector("#details-list");
const confirmedCount = document.querySelector("#confirmed-count");
const providerBadge = document.querySelector("#provider-badge");
const mockHint = document.querySelector("#mock-hint");

const MOCK_HINTS = {
  "full legal name": "My name is Saitama",
  "home address": "I live at 12 Elm Street",
  "whether document covers worldwide assets": "worldwide",
  "whether they have children": "yes",
  "children's names (if applicable)": "My children's names are Alice, Bob",
  "executor details (name and relationship)": "My brother James Smith should be executor",
  "any specific gifts to leave": "Leave my watch to Mia",
  "any additional wishes or instructions": "I also want to leave a note for my family",
};

let sessionId = null;
let currentState = null;
let messageInFlight = false;
let isMockMode = false;
let pendingQuestions = [];

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  let result = {};
  try {
    result = await response.json();
  } catch {
    // Keep an empty result for responses that do not contain JSON.
  }

  if (!response.ok) {
    throw new Error(result.detail || `Request failed (${response.status})`);
  }

  return result;
}

async function loadProviderBadge() {
  try {
    const health = await request("/");
    const provider = String(health.llm_provider || "").toLowerCase();
    isMockMode = provider === "mock";
    providerBadge.textContent = isMockMode
      ? "Mock mode"
      : provider
        ? `Live · ${provider}`
        : "Mode unavailable";
  } catch {
    providerBadge.textContent = "Mode unavailable";
  }
  renderMockHint();
}

function appendMessage(role, text, isError = false) {
  const article = document.createElement("article");
  article.className = `message ${role}-message${isError ? " error-message" : ""}`;

  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : "Wishes assistant";

  const messageText = document.createElement("p");
  messageText.textContent = text;
  article.append(label, messageText);
  chatLog.append(article);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setChatStatus(message, showRetry = false) {
  chatStatus.replaceChildren();
  if (message) {
    const text = document.createElement("span");
    text.textContent = message;
    chatStatus.append(text);
  }

  if (showRetry) {
    const retryButton = document.createElement("button");
    retryButton.className = "retry-button";
    retryButton.type = "button";
    retryButton.textContent = "Try again";
    retryButton.addEventListener("click", startSession);
    chatStatus.append(retryButton);
  }
}

function setComposerEnabled(enabled) {
  messageInput.disabled = !enabled;
  sendButton.disabled = !enabled || messageInFlight;
}

function valueForDisplay(field, entry) {
  if (!entry || entry.status !== "confirmed") {
    return "Not yet provided";
  }

  const value = entry.value;
  if (field === "covers_worldwide_assets") {
    return value ? "Included" : "Not included";
  }
  if (field === "has_children") {
    return value ? "Yes" : "No";
  }
  if (field === "children_names" || field === "specific_gifts") {
    if (!Array.isArray(value) || value.length === 0) {
      return "None provided";
    }
    if (field === "specific_gifts") {
      return value.map((gift) => `${gift.item} → ${gift.recipient}`).join("\n");
    }
    return value.join(", ");
  }
  if (field === "executor") {
    if (!value || typeof value !== "object") {
      return "None provided";
    }
    return [value.name, value.relationship].filter(Boolean).join(" · ") || "None provided";
  }
  if (typeof value === "string" && value.trim() === "") {
    return "None provided";
  }
  return String(value ?? "None provided");
}

function renderDetails(state, previousState = null) {
  detailsList.replaceChildren();
  let confirmedFields = 0;

  for (const field of FIELD_DEFINITIONS) {
    const entry = state[field.key] || { status: "unknown", value: null };
    const isConfirmed = entry.status === "confirmed";
    if (isConfirmed) confirmedFields += 1;

    const row = document.createElement("div");
    row.className = `detail-row ${isConfirmed ? "is-confirmed" : "is-unknown"}`;
    if (isConfirmed && previousState?.[field.key]?.status !== "confirmed") {
      row.classList.add("field-newly-confirmed");
    }

    const label = document.createElement("span");
    label.className = "detail-label";
    label.textContent = field.label;

    const value = document.createElement("span");
    value.className = `detail-value${isConfirmed ? "" : " is-unknown"}`;
    value.textContent = valueForDisplay(field.key, entry);

    const status = document.createElement("span");
    status.className = `field-status${isConfirmed ? " confirmed" : ""}`;
    status.textContent = isConfirmed ? "Confirmed" : "Unknown";

    row.append(label, value, status);
    detailsList.append(row);
  }

  confirmedCount.textContent = `${confirmedFields}/${FIELD_DEFINITIONS.length}`;
}

function renderDocument(documentText) {
  documentCopy.replaceChildren();
  let section = "";

  for (const [index, line] of documentText.split("\n").entries()) {
    const trimmed = line.trim();
    if (trimmed === "PERSONAL INFORMATION") section = "personal";
    if (trimmed === "DOCUMENT SCOPE") section = "scope";
    if (trimmed === "FAMILY INFORMATION") section = "family";
    if (trimmed === "EXECUTOR") section = "executor";
    if (trimmed === "SPECIFIC GIFTS") section = "gifts";
    if (trimmed === "ADDITIONAL WISHES") section = "wishes";

    const isFieldLine = (
      (section === "personal" && /^(Full Name|Home Address):/.test(trimmed))
      || (section === "scope" && trimmed.startsWith("This document covers"))
      || (section === "family" && (/^(I have|I do not have|•)/.test(trimmed)))
      || (section === "executor" && trimmed.startsWith("I appoint"))
      || (section === "gifts" && (/^\d+\.\s/.test(trimmed) || trimmed === "No specific gifts designated."))
      || (section === "wishes" && trimmed && !/^[-=]+$/.test(trimmed))
    );
    const lineElement = document.createElement("span");
    lineElement.className = isFieldLine
      ? (line.includes("[") || trimmed === "None specified." ? "document-placeholder" : "document-value")
      : "";
    lineElement.textContent = line;
    documentCopy.append(lineElement);
    if (index < documentText.split("\n").length - 1) {
      documentCopy.append(document.createTextNode("\n"));
    }
  }
}

function renderMockHint() {
  const example = MOCK_HINTS[pendingQuestions[0]];
  const shouldShow = isMockMode && Boolean(example);
  mockHint.hidden = !shouldShow;
  mockHint.textContent = shouldShow
    ? `Try: "${example}" · or "not sure"`
    : "";
  mockHint.dataset.example = example || "";
}

function renderResponse(state, documentText, nextPendingQuestions = []) {
  const previousState = currentState;
  currentState = state;
  pendingQuestions = nextPendingQuestions;
  renderDetails(state, previousState);
  renderDocument(documentText || "Your draft will take shape here as details are confirmed.");
  renderMockHint();
}

function renderHistory(history) {
  if (!Array.isArray(history) || history.length === 0) return;
  chatLog.replaceChildren();
  for (const message of history) {
    appendMessage(message.role, message.text);
  }
}

async function startSession() {
  sessionId = null;
  currentState = null;
  setComposerEnabled(false);
  setChatStatus("Starting a new session…");

  try {
    const session = await request("/session", { method: "POST" });
    if (!session.session_id) {
      throw new Error("The server did not return a session ID.");
    }
    sessionId = session.session_id;

    const [stateResult, documentResult, historyResult] = await Promise.all([
      request(`/session/${sessionId}/state`),
      request(`/session/${sessionId}/document`),
      request(`/session/${sessionId}/history`),
    ]);
    renderResponse(stateResult.state, documentResult.document, ["full legal name"]);
    renderHistory(historyResult.history);
    setChatStatus("");
    setComposerEnabled(true);
    messageInput.focus();
  } catch {
    setChatStatus("Couldn’t start a session. Check that the backend is running, then try again.", true);
  }
}

async function sendMessage(message) {
  if (!sessionId || messageInFlight) return;

  appendMessage("user", message);
  messageInFlight = true;
  setComposerEnabled(false);
  setChatStatus("The assistant is considering your message…");

  try {
    const result = await request(`/session/${sessionId}/message`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });

    renderResponse(result.state, result.document, result.pending_questions || []);
    appendMessage("assistant", result.assistant_reply || "I have updated your draft.");
    setChatStatus("");
  } catch {
    appendMessage("assistant", "I couldn’t reach the assistant. Please try sending your message again.", true);
    setChatStatus("");
  } finally {
    messageInFlight = false;
    setComposerEnabled(Boolean(sessionId));
    messageInput.focus();
  }
}

mockHint.addEventListener("click", () => {
  messageInput.value = mockHint.dataset.example;
  messageInput.focus();
});

messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || messageInFlight) return;

  messageInput.value = "";
  sendMessage(message);
});

for (const tab of document.querySelectorAll(".view-tab")) {
  tab.addEventListener("click", () => {
    for (const otherTab of document.querySelectorAll(".view-tab")) {
      const isSelected = otherTab === tab;
      otherTab.classList.toggle("is-active", isSelected);
      otherTab.setAttribute("aria-selected", String(isSelected));
      document.getElementById(otherTab.dataset.view).hidden = !isSelected;
    }
  });
}

document.body.dataset.mobilePane = "chat-pane";
for (const button of document.querySelectorAll(".mobile-switch-button")) {
  button.addEventListener("click", () => {
    const paneId = button.dataset.pane;
    document.body.dataset.mobilePane = paneId;
    for (const otherButton of document.querySelectorAll(".mobile-switch-button")) {
      const isSelected = otherButton === button;
      otherButton.classList.toggle("is-active", isSelected);
      otherButton.setAttribute("aria-pressed", String(isSelected));
    }
  });
}

loadProviderBadge();
startSession();
