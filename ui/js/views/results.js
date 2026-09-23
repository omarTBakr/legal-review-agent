/* One document's advice, as a card. Shared by the live review and the stored one. */

import { fetchAnnotated } from "../api.js";
import { copyButton, downloadBlob, el, setBusy, showInlineError } from "../dom.js";
import { displayName } from "../format.js";
import { DECISION_LABELS, severityRank } from "../labels.js";

/**
 * A button that saves the contract with its risks highlighted.
 *
 * Fetched rather than linked: the download has to carry the API key, and a
 * navigation cannot set a header. Marking up a long contract takes a moment, so
 * the button says it is working.
 */
function annotatedButton(doc, taskId, projectId) {
  const error = el("p", { class: "form-error", role: "alert", hidden: true });

  const button = el(
    "button",
    {
      type: "button",
      class: "button",
      onclick: async () => {
        setBusy(button, true, "Marking up…");
        showInlineError(error, "");
        try {
          const blob = await fetchAnnotated(taskId, doc.pdf_key, projectId);
          downloadBlob(`${displayName(doc.pdf_key).replace(/\.pdf$/i, "")}-reviewed.pdf`, blob);
        } catch (failure) {
          showInlineError(error, failure.message);
        } finally {
          setBusy(button, false);
        }
      },
    },
    "Download marked-up PDF",
  );

  return el("div", { class: "card-actions" }, button, error);
}

export function stat(value, label) {
  return el("div", { class: "stat" }, el("span", { class: "stat-value" }, value), el("span", { class: "stat-label" }, label));
}

function riskEvidence(risk) {
  // advice stored before quotes existed has none; show nothing rather than a false alarm
  if (!risk.quote) return null;

  return el(
    "figure",
    { class: "evidence" },
    el("blockquote", {}, risk.quote),
    el(
      "figcaption",
      { class: "muted small" },
      risk.page ? `p. ${risk.page}` : null,
      risk.quote_verified
        ? null
        : el(
            "span",
            { class: "unverified", title: "This quote could not be found in the document. Check it by hand." },
            "Unverified quote",
          ),
    ),
  );
}

export function resultCard(doc, taskId = "", projectId = "") {
  const risks = [...(doc.key_risks || [])].sort((a, b) => severityRank(a.severity) - severityRank(b.severity));

  return el(
    "article",
    { class: `panel result-card${doc.needs_attention ? " needs-attention" : ""}` },
    el(
      "header",
      { class: "result-head" },
      el("h3", { title: doc.pdf_key }, displayName(doc.pdf_key)),
      el(
        "span",
        { class: `decision decision-${doc.review_decision}` },
        DECISION_LABELS[doc.review_decision] || doc.review_decision,
      ),
    ),
    doc.needs_attention
      ? el("p", { class: "attention" }, "The model had a question that nobody answered in time. Treat this advice as a draft.")
      : null,
    el("p", { class: "summary" }, doc.summary),
    el("h4", {}, risks.length ? `Key risks (${risks.length})` : "No key risks flagged"),
    risks.length
      ? el(
          "ol",
          { class: "risks" },
          risks.map((risk) =>
            el(
              "li",
              { class: "risk" },
              el("span", { class: `severity severity-${risk.severity}` }, risk.severity),
              el(
                "div",
                {},
                el("p", {}, risk.description),
                risk.location ? el("p", { class: "muted small" }, risk.location) : null,
                riskEvidence(risk),
              ),
            ),
          ),
        )
      : null,
    // needs a task id to fetch through; the stored view has one, and so does
    // the live one once a document has finished
    taskId ? annotatedButton(doc, taskId, projectId) : null,
    doc.s3_path
      ? el(
          "footer",
          { class: "result-foot" },
          el("span", { class: "muted small" }, "Stored at"),
          el("code", {}, doc.s3_path),
          copyButton(doc.s3_path, "Copy S3 path"),
        )
      : null,
  );
}
