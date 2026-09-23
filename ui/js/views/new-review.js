/*
 * Starting a review: choose PDFs, optionally inside a project, and say where
 * the finished report should be emailed.
 */

import { fetchProject, startReview } from "../api.js";
import { byId, el, setBusy, showInlineError } from "../dom.js";
import { formatBytes, plural } from "../format.js";
import { rememberReview } from "../store.js";

// files chosen for the next review; cleared once it has been submitted
let chosen = [];

export async function showNewReview(projectId = "") {
  chosen = [];

  let project = null;
  if (projectId) {
    try {
      project = await fetchProject(projectId);
    } catch {
      // the project is gone or unreachable; fall back to a review without one
      project = null;
    }
  }

  const input = el("input", {
    type: "file",
    id: "file-input",
    class: "visually-hidden",
    accept: "application/pdf,.pdf",
    multiple: true,
    "aria-label": "Choose PDF documents",
    onchange: (event) => {
      addFiles(event.target.files);
      event.target.value = "";
    },
  });

  const dropzone = el(
    "label",
    {
      class: "dropzone",
      ondragover: (event) => {
        event.preventDefault();
        dropzone.classList.add("dragging");
      },
      ondragleave: () => dropzone.classList.remove("dragging"),
      ondrop: (event) => {
        event.preventDefault();
        dropzone.classList.remove("dragging");
        addFiles(event.dataTransfer.files);
      },
    },
    input,
    el("span", { class: "dropzone-icon", "aria-hidden": "true" }, "⇪"),
    el("strong", {}, "Drop PDF documents here"),
    el("span", { class: "muted small" }, "or click to choose. You can add several at once."),
  );

  const email = el("input", {
    type: "email",
    id: "report-email",
    placeholder: "nobody@example.com",
    value: (project && project.email) || "",
    autocomplete: "email",
  });

  byId("main").replaceChildren(
    el(
      "section",
      { class: "panel" },
      el("h2", {}, project ? `New review in ${project.name}` : "New review"),
      el(
        "p",
        { class: "muted" },
        project
          ? "These documents are filed in this project's folder. Each one is read by the model and checked for legal risk."
          : "Each document is read by the model and checked for legal risk. Documents are sent to the model provider configured on the server.",
      ),
      project ? el("input", { type: "hidden", id: "project-id", value: project.id }) : null,
      dropzone,
      el("ul", { class: "file-list", id: "file-list" }),
      el(
        "div",
        { class: "field" },
        el("label", { for: "report-email" }, "Email the report to ", el("span", { class: "muted small" }, "optional")),
        email,
        el(
          "p",
          { class: "muted small" },
          project && project.email
            ? "The project's address, which you can change for this review."
            : "Leave empty to read the results here only.",
        ),
      ),
      el(
        "div",
        { class: "actions" },
        el("span", { class: "muted small", id: "file-summary" }),
        el("button", { type: "button", class: "button primary", id: "submit", onclick: submit }, "Start review"),
      ),
      el("p", { class: "form-error", id: "submit-error", role: "alert", hidden: true }),
    ),
    el(
      "section",
      { class: "panel" },
      el("h3", {}, "How a review works"),
      el(
        "ol",
        { class: "steps" },
        el(
          "li",
          {},
          el("div", {}, el("strong", {}, "Upload"), "Your PDFs are stored and a workflow starts. A few documents are analysed at a time."),
        ),
        el(
          "li",
          {},
          el(
            "div",
            {},
            el("strong", {}, "Answer questions"),
            "When the model needs something only you know, it asks here and that document waits for you.",
          ),
        ),
        el(
          "li",
          {},
          el(
            "div",
            {},
            el("strong", {}, "Read the risks"),
            "Each document gets a summary and its key risks, worst first, each quoting the passage it rests on.",
          ),
        ),
      ),
    ),
  );

  renderFiles();
}

function addFiles(fileList) {
  const problems = [];

  for (const file of fileList) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      problems.push(`${file.name} is not a PDF`);
    } else if (file.size === 0) {
      problems.push(`${file.name} is empty`);
    } else if (!chosen.some((already) => already.name === file.name && already.size === file.size)) {
      chosen.push(file);
    }
  }

  renderFiles();
  showInlineError(byId("submit-error"), problems.length ? `${problems.join(". ")}.` : "");
}

function renderFiles() {
  const list = byId("file-list");
  if (!list) return;

  list.replaceChildren(
    ...chosen.map((file, index) =>
      el(
        "li",
        { class: "file-row" },
        el("span", { class: "file-icon", "aria-hidden": "true" }, "PDF"),
        el("span", { class: "file-name", title: file.name }, file.name),
        el("span", { class: "muted small" }, formatBytes(file.size)),
        el(
          "button",
          {
            type: "button",
            class: "icon-button",
            "aria-label": `Remove ${file.name}`,
            onclick: () => {
              chosen.splice(index, 1);
              renderFiles();
            },
          },
          "Remove",
        ),
      ),
    ),
  );

  const total = chosen.reduce((sum, file) => sum + file.size, 0);
  byId("file-summary").textContent = chosen.length ? `${plural(chosen.length, "file", "files")} · ${formatBytes(total)}` : "No files chosen yet";
  byId("submit").disabled = chosen.length === 0;
}

async function submit() {
  const button = byId("submit");
  const projectId = byId("project-id") ? byId("project-id").value : "";
  const email = byId("report-email").value.trim();

  setBusy(button, true, "Uploading…");
  showInlineError(byId("submit-error"), "");

  try {
    const body = await startReview(chosen, { projectId, email });
    rememberReview({
      taskId: body.task_id,
      files: chosen.map((file) => file.name),
      status: body.status,
      projectId: body.project_id || "",
      createdAt: Date.now(),
    });
    chosen = [];
    location.hash = `#/review/${body.task_id}`;
  } catch (error) {
    showInlineError(byId("submit-error"), error.message);
    setBusy(button, false);
  }
}
