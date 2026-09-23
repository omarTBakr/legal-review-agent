/*
 * Two rounds of a contract, side by side.
 *
 * Ordered the way the news matters rather than the way the document reads:
 * what got worse and what is new first, what was fixed last. A reviewer opening
 * this has already read round one — the question is what moved.
 */

import { fetchComparison } from "../api.js";
import { byId, el } from "../dom.js";
import { displayName, plural } from "../format.js";

// the order the sections appear in, and what each one is called
const SECTIONS = [
  ["new", "New in this round", "Risks that were not in the previous round."],
  ["worse", "Got worse", "The same clause, at a higher severity than before."],
  ["unchanged", "Still there", "Unchanged since the previous round."],
  ["better", "Improved", "The same clause, at a lower severity than before."],
  ["fixed", "No longer flagged", "Risks the previous round had and this one does not."],
];

function headline(totals) {
  const net = totals.net_severity_change;

  if (net < 0) return ["better", "This round is better than the last"];
  if (net > 0) return ["worse", "This round is worse than the last"];

  return ["level", "Nothing moved on balance"];
}

function changeRow(change) {
  const moved =
    change.was_severity && change.was_severity !== change.severity
      ? el("span", { class: "muted small" }, `was ${change.was_severity}`)
      : null;

  return el(
    "li",
    { class: `risk change change-${change.verdict}` },
    el("span", { class: `severity severity-${change.severity}` }, change.severity),
    el(
      "div",
      {},
      el("p", {}, change.description),
      moved,
      // the previous round's wording, when the model described the same clause
      // differently: the clause did not move, the description did
      change.was_description && change.was_description !== change.description
        ? el("p", { class: "muted small" }, `Previously: ${change.was_description}`)
        : null,
      change.quote
        ? el(
            "figure",
            { class: "evidence" },
            el("blockquote", {}, change.quote),
            change.page ? el("figcaption", { class: "muted small" }, `p. ${change.page}`) : null,
          )
        : null,
    ),
  );
}

function documentSection(document) {
  const sections = SECTIONS.map(([verdict, title, explanation]) => {
    const changes = (document.changes || []).filter((change) => change.verdict === verdict);
    if (!changes.length) return null;

    return el(
      "div",
      { class: "change-group" },
      el("h4", {}, `${title} (${changes.length})`),
      el("p", { class: "muted small" }, explanation),
      el("ol", { class: "risks" }, changes.map(changeRow)),
    );
  }).filter(Boolean);

  return el(
    "article",
    { class: "panel result-card" },
    el("header", { class: "result-head" }, el("h3", { title: document.document }, displayName(document.document))),
    el(
      "p",
      { class: "muted small" },
      `Compared against ${displayName(document.base_document)} · `,
      `${document.resolved} no longer flagged, ${document.introduced} new`,
    ),
    ...(sections.length ? sections : [el("p", { class: "muted" }, "Nothing changed in this document.")]),
  );
}

export async function showComparison(projectId, base, against) {
  byId("main").replaceChildren(
    el("section", { class: "panel" }, el("h2", {}, "Comparing rounds"), el("p", { class: "muted" }, "Loading…")),
  );

  let body;
  try {
    body = await fetchComparison(projectId, base, against);
  } catch (error) {
    byId("main").replaceChildren(
      el(
        "section",
        { class: "panel" },
        el("h2", {}, "Could not compare these reviews"),
        el("p", { class: "muted" }, error.message),
        el("div", { class: "actions" }, el("a", { class: "button", href: `#/projects/${projectId}` }, "Back to the project")),
      ),
    );
    return;
  }

  const totals = body.totals || {};
  const [tone, verdict] = headline(totals);

  byId("main").replaceChildren(
    el(
      "section",
      { class: "panel" },
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, "What changed"),
        el("a", { class: "button", href: `#/projects/${projectId}` }, "Back to the project"),
      ),
      el("p", { class: `compare-verdict compare-${tone}` }, verdict),
      el(
        "p",
        { class: "muted small" },
        `Round ${base} → ${against}. `,
        `${totals.fixed || 0} no longer flagged, ${totals.new || 0} new, ${totals.worse || 0} worse, `,
        `${totals.better || 0} improved, ${totals.unchanged || 0} unchanged.`,
      ),
      body.unpaired && body.unpaired.length
        ? el(
            "p",
            { class: "muted small" },
            `${plural(body.unpaired.length, "document", "documents")} had no counterpart in the other round and ` +
              `${body.unpaired.length === 1 ? "was" : "were"} left out: ${body.unpaired.map(displayName).join(", ")}.`,
          )
        : null,
      body.not_reviewed_yet && body.not_reviewed_yet.length
        ? el("p", { class: "muted small" }, "Some documents have no advice stored yet, so they are not compared.")
        : null,
    ),
    ...(body.documents || []).map(documentSection),
  );
}
