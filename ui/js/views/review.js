/*
 * Following one review.
 *
 * Polls GET /legal/{task_id} while the review is running, answers the model's
 * questions, and shows each document's advice as it finishes. When Temporal no
 * longer has the workflow — its history has a retention window — and the review
 * belongs to a project, the advice is read back from the bucket instead.
 */

import { fetchReview, fetchStoredReview, answerQuestion, ApiError } from "../api.js";
import { byId, copyButton, downloadJson, el, setBusy, showInlineError } from "../dom.js";
import { displayName, plural } from "../format.js";
import { DOCUMENT_LABELS, RUNNING, SEVERITIES, STATUS_LABELS, severityChip, statusPill } from "../labels.js";
import { forgetReview, projectOf, updateRecent } from "../store.js";
import { renderRecent } from "../sidebar.js";
import { chatPanel } from "./chat.js";
import { dictationButton } from "./dictation.js";
import { resultCard, stat } from "./results.js";

const POLL_MS = 2500;
const MAX_BACKOFF_MS = 30000;
const QUIET_HINT_MS = 45000;

const state = {
  // identifies the review on screen, so a poll that returns after the user has
  // moved on is ignored
  view: null,
  timer: null,
  failures: 0,
  lastSignature: "",
  lastChangeAt: 0,
  // "<task_id>/<pdf_key>" for answers sent from this page
  answered: new Set(),
  // pdf_key -> question card, kept across polls so a half-typed answer survives
  questionCards: new Map(),
  // pdf_keys whose result card is already on screen while the review runs
  resultCards: new Set(),
  // the project this review belongs to, if any: chat needs somewhere durable
  // to read the advice and the documents back from
  project: "",
};

export function stopPolling() {
  clearTimeout(state.timer);
  state.timer = null;
  state.view = null;
}

export function showReview(taskId, projectId = "") {
  const token = {};
  state.view = token;
  state.failures = 0;
  state.lastSignature = "";
  state.lastChangeAt = Date.now();
  state.questionCards = new Map();
  state.resultCards = new Set();

  const project = projectId || projectOf(taskId);
  state.project = project;

  byId("main").replaceChildren(
    el(
      "section",
      { class: "panel review-head" },
      el(
        "div",
        {},
        el("h2", {}, "Review"),
        el("div", { class: "task-line" }, el("span", { class: "muted" }, "Task"), el("code", {}, taskId), copyButton(taskId, "Copy task id")),
        project ? el("a", { class: "muted small", href: `#/projects/${project}` }, "Back to the project") : null,
      ),
      el(
        "div",
        { class: "review-status", "aria-live": "polite" },
        el("span", { id: "overall-status" }, statusPill("loading")),
        el("span", { id: "checked-at", class: "muted small" }, "Fetching status…"),
      ),
    ),
    el("div", { id: "notice", "aria-live": "polite" }),
    el("section", { id: "questions", class: "questions", hidden: true }),
    el("section", { id: "progress", class: "panel", hidden: true }),
    el("section", { id: "results", class: "results", hidden: true }),
  );

  poll(taskId, token, project);
}

function schedule(taskId, token, project, delay) {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => poll(taskId, token, project), delay);
}

async function poll(taskId, token, project) {
  if (state.view !== token) return;

  let body;
  try {
    body = await fetchReview(taskId);
  } catch (error) {
    if (state.view !== token) return;
    if (error.status === 404) {
      await showStoredOrMissing(taskId, project);
      return;
    }
    state.failures += 1;
    renderNotice("error", `${error.message} Trying again…`);
    schedule(taskId, token, project, Math.min(POLL_MS * 2 ** state.failures, MAX_BACKOFF_MS));
    return;
  }

  if (state.view !== token) return;

  state.failures = 0;
  renderReview(taskId, body);
  if (updateRecent(taskId, body)) renderRecent();

  if (RUNNING.has(body.status)) schedule(taskId, token, project, POLL_MS);
}

function renderReview(taskId, body) {
  byId("overall-status").replaceChildren(statusPill(body.status));
  byId("checked-at").textContent = `Checked ${new Date().toLocaleTimeString()}`;

  if (body.status === "completed") {
    clearNotice();
    byId("questions").hidden = true;
    byId("progress").hidden = true;
    renderResults(taskId, body);
    return;
  }

  if (!RUNNING.has(body.status)) {
    byId("questions").hidden = true;
    byId("progress").hidden = true;
    renderNotice(
      "error",
      `This review ended as "${STATUS_LABELS[body.status] || body.status}" without results. The Temporal UI (port 8233 with the dev server) shows which step failed and why.`,
    );
    return;
  }

  renderQuestions(taskId, body.pending_questions || []);
  renderProgress(taskId, body.documents || {});
  renderFinished(taskId, body.results || [], body.documents || {});
  noticeWhenQuiet(body);
}

function renderFinished(taskId, results, documents) {
  // cards are added as documents finish and never redrawn, so reading one is
  // not interrupted by the next poll; the completed view replaces them all
  const section = byId("results");
  if (!results.length) {
    section.hidden = true;
    return;
  }

  if (!section.dataset.partial) {
    section.dataset.partial = "true";
    section.replaceChildren(
      el("div", { class: "section-head" }, el("h3", {}, "Finished so far"), el("span", { class: "muted small", id: "finished-count" })),
    );
  }

  for (const doc of results) {
    if (state.resultCards.has(doc.pdf_key)) continue;
    state.resultCards.add(doc.pdf_key);
    section.append(resultCard(doc, taskId, state.project));
  }

  byId("finished-count").textContent = `${results.length} of ${Object.keys(documents).length} documents`;
  section.hidden = false;
}

function renderQuestions(taskId, questions) {
  const section = byId("questions");
  const pending = new Map(questions.map((item) => [item.pdf_key, item.question]));

  if (!section.childElementCount) {
    section.append(
      el(
        "div",
        { class: "section-head" },
        el("h3", {}, "The model has a question"),
        el(
          "p",
          { class: "muted small" },
          "That document waits for your answer. If nobody answers in time it finishes anyway and is flagged as unreviewed.",
        ),
      ),
    );
  }

  for (const [key, card] of state.questionCards) {
    if (!pending.has(key)) {
      card.remove();
      state.questionCards.delete(key);
    }
  }

  for (const [key, question] of pending) {
    if (!state.questionCards.has(key)) {
      const card = questionCard(taskId, key, question);
      state.questionCards.set(key, card);
      section.append(card);
    }
  }

  section.hidden = pending.size === 0;
}

function questionCard(taskId, pdfKey, question) {
  const id = `answer-${pdfKey.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  const textarea = el("textarea", { id, rows: "3", required: true, placeholder: "Your answer…" });
  const error = el("p", { class: "form-error", role: "alert", hidden: true });
  const status = el("span", { class: "muted small", role: "status" });
  const button = el("button", { type: "submit", class: "button primary" }, "Send answer");
  // the same microphone as the chat box: this is an answer given while reading
  // a contract, which is an awkward moment to be typing
  const mic = dictationButton({ field: textarea, status, error, label: "Speak" });

  const form = el(
    "form",
    {
      class: "answer-form",
      onsubmit: async (event) => {
        event.preventDefault();

        const answer = textarea.value.trim();
        if (!answer) {
          showInlineError(error, "Write an answer first.");
          textarea.focus();
          return;
        }

        setBusy(button, true, "Sending…");
        textarea.disabled = true;
        showInlineError(error, "");

        try {
          await answerQuestion(taskId, pdfKey, answer);
          state.answered.add(`${taskId}/${pdfKey}`);
          form.classList.add("sent");
          button.textContent = "Answer sent";
        } catch (failure) {
          showInlineError(error, failure.message);
          setBusy(button, false);
          textarea.disabled = false;
        }
      },
    },
    el("label", { for: id, class: "visually-hidden" }, `Answer for ${displayName(pdfKey)}`),
    textarea,
    el(
      "div",
      { class: "actions" },
      status,
      el("span", { class: "muted small" }, "Ctrl+Enter to send"),
      mic,
      button,
    ),
    error,
  );

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) form.requestSubmit();
  });

  return el(
    "article",
    { class: "question-card" },
    el("div", { class: "question-doc" }, el("span", { class: "label" }, "Document"), el("strong", { title: pdfKey }, displayName(pdfKey))),
    el("blockquote", { class: "question" }, question),
    form,
  );
}

function renderProgress(taskId, documents) {
  const entries = Object.entries(documents);
  const done = entries.filter(([, status]) => status === "completed").length;
  const percent = entries.length ? Math.round((done / entries.length) * 100) : 0;

  const section = byId("progress");
  section.hidden = false;
  section.replaceChildren(
    el("div", { class: "section-head" }, el("h3", {}, "Documents"), el("span", { class: "muted small" }, `${done} of ${entries.length} done`)),
    el(
      "div",
      {
        class: "progress-bar",
        role: "progressbar",
        "aria-label": "Documents finished",
        "aria-valuemin": "0",
        "aria-valuemax": "100",
        "aria-valuenow": String(percent),
      },
      el("span", { style: `width: ${percent}%` }),
    ),
    el(
      "ul",
      { class: "doc-list" },
      entries.map(([key, status]) => {
        // after an answer the document is back to "processing" while the model revises
        const revising = status === "processing" && state.answered.has(`${taskId}/${key}`);
        return el(
          "li",
          { class: "doc-row" },
          el("span", { class: "doc-name", title: key }, displayName(key)),
          el(
            "span",
            { class: `doc-status doc-${revising ? "revising" : status}` },
            el("span", { class: "dot", "aria-hidden": "true" }),
            revising ? "Revising with your answer" : DOCUMENT_LABELS[status] || status,
          ),
        );
      }),
    ),
    el("p", { class: "muted small" }, "Only a few documents are analysed at once; the others show as in progress until their turn."),
  );
}

function noticeWhenQuiet(body) {
  const signature = JSON.stringify([body.documents, body.pending_questions]);
  const now = Date.now();

  if (signature !== state.lastSignature) {
    state.lastSignature = signature;
    state.lastChangeAt = now;
  }

  if (body.status === "processing" && now - state.lastChangeAt > QUIET_HINT_MS) {
    renderNotice(
      "info",
      "Still working. Every batch of pages is a separate model call, so long documents take a while. If nothing changes for several minutes, check that the legal worker is running (uv run python -m workers.legal_advice_worker).",
    );
  } else {
    clearNotice();
  }
}

function renderResults(taskId, body) {
  const section = byId("results");
  if (section.dataset.rendered === taskId) return;
  section.dataset.rendered = taskId;
  section.hidden = false;

  const documents = body.documents || [];
  const risks = documents.flatMap((doc) => doc.key_risks || []);
  const attention = documents.filter((doc) => doc.needs_attention).length;
  const unverified = risks.filter((risk) => risk.quote && !risk.quote_verified).length;
  const counts = {};
  for (const risk of risks) counts[risk.severity] = (counts[risk.severity] || 0) + 1;

  section.replaceChildren(
    el(
      "div",
      { class: "panel summary-strip" },
      stat(documents.length, documents.length === 1 ? "document" : "documents"),
      stat(risks.length, risks.length === 1 ? "risk flagged" : "risks flagged"),
      el(
        "div",
        { class: "severity-counts" },
        SEVERITIES.filter((severity) => counts[severity]).map((severity) => severityChip(severity, `${counts[severity]} ${severity}`)),
      ),
      attention ? el("span", { class: "attention-count" }, `${plural(attention, "document", "documents")} unreviewed`) : null,
      unverified ? el("span", { class: "attention-count" }, `${plural(unverified, "quote", "quotes")} unverified`) : null,
      el(
        "div",
        { class: "summary-actions" },
        el("button", { type: "button", class: "button", onclick: () => downloadJson(`legal-review-${taskId}.json`, body) }, "Download JSON"),
      ),
    ),
    // replaceChildren does not flatten arrays, unlike el()
    ...documents.map((doc) => resultCard(doc, taskId, state.project)),
  );

  // chat reads the advice and the pages back from the project's folder, so a
  // review started outside a project cannot offer it — say so rather than
  // leaving someone hunting for a panel that was never going to appear
  section.append(
    state.project
      ? chatPanel(state.project, taskId)
      : el(
          "section",
          { class: "panel chat-unavailable" },
          el("h3", {}, "Ask about this review"),
          el(
            "p",
            { class: "muted" },
            "Questions are answered from the advice and the document text stored in a project's folder, so they are offered on reviews started inside a project. This one was not. ",
            el("a", { href: "#/projects" }, "Start one in a project"),
            " and the chat appears here when it finishes.",
          ),
        ),
  );
}

async function showStoredOrMissing(taskId, project) {
  if (!project) {
    renderNotFound(taskId);
    return;
  }

  try {
    const stored = await fetchStoredReview(project, taskId);
    renderStored(taskId, project, stored);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) renderNotFound(taskId);
    else renderNotice("error", error.message);
  }
}

function renderStored(taskId, project, body) {
  byId("overall-status").replaceChildren(statusPill("completed"));
  byId("checked-at").textContent = "Read from storage";
  byId("questions").hidden = true;
  byId("progress").hidden = true;

  renderNotice(
    "info",
    "Temporal no longer has this review, so it is shown from the advice stored in the bucket. Questions can no longer be answered.",
  );

  renderResults(taskId, { ...body, status: "completed" });

  if (body.pending && body.pending.length) {
    byId("results").append(
      el(
        "p",
        { class: "muted small" },
        `${plural(body.pending.length, "document has", "documents have")} no stored advice: ${body.pending.map(displayName).join(", ")}`,
      ),
    );
  }
}

function renderNotFound(taskId) {
  byId("main").replaceChildren(
    el(
      "section",
      { class: "panel" },
      el("h2", {}, "Review not found"),
      el(
        "p",
        { class: "muted" },
        "Temporal has no review with task id ",
        el("code", {}, taskId),
        ". A Temporal dev server forgets its history when it restarts, unless it was started with --db-filename. Reviews started inside a project are also kept in the bucket, and those outlive Temporal.",
      ),
      el(
        "div",
        { class: "actions" },
        el(
          "button",
          {
            type: "button",
            class: "button",
            onclick: () => {
              forgetReview(taskId);
              renderRecent();
              location.hash = "#/new";
            },
          },
          "Remove from recent",
        ),
        el("a", { class: "button primary", href: "#/new" }, "Start a new review"),
      ),
    ),
  );
}

function renderNotice(kind, text) {
  const node = byId("notice");
  if (node) node.replaceChildren(el("div", { class: `notice notice-${kind}`, role: kind === "error" ? "alert" : "status" }, text));
}

function clearNotice() {
  const node = byId("notice");
  if (node) node.replaceChildren();
}
