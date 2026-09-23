/*
 * Every risk in a project, worst first.
 *
 * The one page that answers "what is the worst thing across this client's
 * contracts" without opening each review. Filtering is done here rather than by
 * re-fetching: the whole register is already in hand, and a severity filter
 * that costs a round trip discourages using it.
 */

import { fetchRegister } from "../api.js";
import { byId, el } from "../dom.js";
import { displayName, plural } from "../format.js";
import { severityRank } from "../labels.js";

const BANDS = ["critical", "high", "medium", "low"];

// what is on screen, so a filter click does not refetch
let loaded = null;
let floor = "";

function summary(counts) {
  const shown = BANDS.filter((band) => counts[band]);

  return shown.length
    ? shown.map((band) => el("span", { class: `severity severity-${band}` }, `${counts[band]} ${band}`))
    : [el("span", { class: "muted" }, "No risks flagged.")];
}

function filterButton(band, label) {
  const active = floor === band;

  return el(
    "button",
    {
      type: "button",
      class: `button small${active ? " primary" : ""}`,
      "aria-pressed": active ? "true" : "false",
      onclick: () => {
        floor = active ? "" : band;
        render();
      },
    },
    label,
  );
}

function riskRow(risk, projectId) {
  return el(
    "li",
    { class: "risk" },
    el("span", { class: `severity severity-${risk.severity}` }, risk.severity),
    el(
      "div",
      {},
      el("p", {}, risk.description),
      el(
        "p",
        { class: "muted small" },
        el("a", { href: `#/projects/${projectId}/reviews/${risk.task_id}` }, displayName(risk.pdf_key)),
        risk.location ? ` · ${risk.location}` : "",
        risk.page ? ` · p. ${risk.page}` : "",
        risk.quote_verified ? "" : " · ",
        risk.quote_verified
          ? null
          : el("span", { class: "unverified", title: "This quote could not be found in the document." }, "unverified"),
      ),
      risk.quote ? el("figure", { class: "evidence" }, el("blockquote", {}, risk.quote)) : null,
    ),
  );
}

function render() {
  const body = loaded;
  const visible = floor
    ? body.risks.filter((risk) => severityRank(risk.severity) <= severityRank(floor))
    : body.risks;

  byId("main").replaceChildren(
    el(
      "section",
      { class: "panel" },
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, `Risk register — ${body.project_name}`),
        el("a", { class: "button", href: `#/projects/${body.project_id}` }, "Back to the project"),
      ),
      el(
        "p",
        { class: "muted small" },
        `${plural(body.risk_count, "risk", "risks")} across ${plural(body.documents, "document", "documents")}`,
        body.superseded_reviews
          ? ` · ${plural(body.superseded_reviews, "earlier round", "earlier rounds")} left out`
          : "",
        body.unverified ? ` · ${body.unverified} resting on an unverified quote` : "",
      ),
      el("div", { class: "register-totals" }, ...summary(body.counts)),
      el(
        "div",
        { class: "register-filters" },
        el("span", { class: "muted small" }, "Show at least:"),
        ...BANDS.slice(0, 3).map((band) => filterButton(band, band)),
        // a plain button, not a filter: "Clear" is an action, and giving it
        // aria-pressed would tell a screen reader it is a fourth active filter
        floor
          ? el(
              "button",
              {
                type: "button",
                class: "button small",
                onclick: () => {
                  floor = "";
                  render();
                },
              },
              "Clear",
            )
          : null,
      ),
      body.pending && body.pending.length
        ? el("p", { class: "muted small" }, `${plural(body.pending.length, "document is", "documents are")} still being reviewed.`)
        : null,
    ),
    el(
      "section",
      { class: "panel" },
      visible.length
        ? el("ol", { class: "risks" }, visible.map((risk) => riskRow(risk, body.project_id)))
        : el("p", { class: "muted" }, floor ? `Nothing at ${floor} or above.` : "No risks in this project yet."),
    ),
  );
}

export async function showRegister(projectId) {
  floor = "";
  byId("main").replaceChildren(
    el("section", { class: "panel" }, el("h2", {}, "Risk register"), el("p", { class: "muted" }, "Loading…")),
  );

  try {
    loaded = await fetchRegister(projectId);
  } catch (error) {
    byId("main").replaceChildren(
      el(
        "section",
        { class: "panel" },
        el("h2", {}, "Could not load the register"),
        el("p", { class: "muted" }, error.message),
        el("div", { class: "actions" }, el("a", { class: "button", href: `#/projects/${projectId}` }, "Back to the project")),
      ),
    );
    return;
  }

  render();
}
