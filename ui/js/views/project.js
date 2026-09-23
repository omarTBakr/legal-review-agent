/* One project: what it is, and the reviews filed in it. */

import { fetchProject } from "../api.js";
import { byId, copyButton, el } from "../dom.js";
import { displayName, plural, relativeDate } from "../format.js";

export async function showProject(projectId) {
  byId("main").replaceChildren(el("section", { class: "panel" }, el("h2", {}, "Project"), el("p", { class: "muted" }, "Loading…")));

  let project;
  try {
    project = await fetchProject(projectId);
  } catch (error) {
    byId("main").replaceChildren(
      el(
        "section",
        { class: "panel" },
        el("h2", {}, error.status === 404 ? "Project not found" : "Could not load the project"),
        el("p", { class: "muted" }, error.message),
        el("div", { class: "actions" }, el("a", { class: "button primary", href: "#/projects" }, "All projects")),
      ),
    );
    return;
  }

  const reviews = project.reviews || [];

  byId("main").replaceChildren(
    el(
      "section",
      { class: "panel" },
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, project.name),
        el("a", { class: "button primary", href: `#/projects/${project.id}/new` }, "New review"),
      ),
      project.description ? el("p", {}, project.description) : null,
      el(
        "dl",
        { class: "project-facts" },
        el("dt", {}, "Folder"),
        el("dd", {}, el("code", {}, project.prefix), copyButton(project.prefix, "Copy the folder prefix")),
        el("dt", {}, "Reports"),
        el("dd", {}, project.email || el("span", { class: "muted" }, "Not emailed")),
        el("dt", {}, "Created"),
        el("dd", { class: "muted" }, relativeDate(project.created_at) || project.created_at),
      ),
    ),
    el(
      "section",
      { class: "panel" },
      el(
        "div",
        { class: "section-head" },
        el("h3", {}, "Reviews"),
        el("span", { class: "muted small" }, reviews.length ? plural(reviews.length, "review", "reviews") : ""),
      ),
      reviews.length
        ? el(
            "ul",
            { class: "project-list" },
            orderByRound(reviews).map(({ review, round }) => reviewRow(project.id, review, round)),
          )
        : el("p", { class: "muted" }, "No reviews yet. Everything you upload here is filed in this project's folder."),
    ),
  );
}

/**
 * Orders reviews so a round sits under the one it supersedes.
 *
 * A chain can be broken — the review it points at may have been submitted to a
 * different project, or never have existed — so anything whose predecessor is
 * not here is treated as a first round rather than dropped. A list that quietly
 * omitted a review would be worse than one that shows it at the top level.
 */
export function orderByRound(reviews) {
  const byId = new Map(reviews.map((review) => [review.task_id, review]));
  const followers = new Map();

  for (const review of reviews) {
    if (review.supersedes && byId.has(review.supersedes)) {
      followers.set(review.supersedes, [...(followers.get(review.supersedes) || []), review]);
    }
  }

  const ordered = [];

  const walk = (review, round) => {
    ordered.push({ review, round });
    for (const next of followers.get(review.task_id) || []) walk(next, round + 1);
  };

  for (const review of reviews) {
    if (!review.supersedes || !byId.has(review.supersedes)) walk(review, 1);
  }

  return ordered;
}

function reviewRow(projectId, review, round) {
  const names = (review.pdf_keys || []).map(displayName);

  return el(
    "li",
    { class: round > 1 ? "is-round" : "" },
    el(
      "a",
      { class: "project-row", href: `#/projects/${projectId}/reviews/${review.task_id}` },
      el(
        "span",
        { class: "project-main" },
        el("strong", { title: names.join(", ") }, names[0] || `Task ${review.task_id}`),
        el(
          "span",
          { class: "muted small" },
          round > 1 ? `Round ${round} · ` : "",
          names.length > 1 ? `+ ${names.length - 1} more · ` : "",
          el("code", {}, review.task_id),
        ),
      ),
      el("span", { class: "project-meta muted small" }, relativeDate(review.submitted_at)),
    ),
    // only a round has something to be compared against
    review.supersedes
      ? el(
          "a",
          {
            class: "compare-link small",
            href: `#/projects/${projectId}/compare/${review.supersedes}/${review.task_id}`,
          },
          "What changed since the previous round →",
        )
      : null,
  );
}
