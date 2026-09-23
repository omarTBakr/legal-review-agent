/* One document's advice, as a card. Shared by the live review and the stored one. */

import { copyButton, el } from "../dom.js";
import { displayName } from "../format.js";
import { DECISION_LABELS, severityRank } from "../labels.js";

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

export function resultCard(doc) {
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
