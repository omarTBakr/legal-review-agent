/*
 * Browser UI for the legal review pipeline.
 *
 * It drives the same endpoints a script would: POST /legal starts a review,
 * GET /legal/{task_id} follows it, POST /legal/{task_id}/respond answers the
 * model's questions, and /projects files reviews under a client's folder. No
 * build step and no dependencies: ES modules, served as they are written.
 */

import { checkHealth, onUnauthorized } from "./api.js";
import { byId } from "./dom.js";
import { parse } from "./router.js";
import { renderRecent, clearRecent } from "./sidebar.js";
import { showNewReview } from "./views/new-review.js";
import { showComparison } from "./views/compare.js";
import { askForKey } from "./views/key.js";
import { showProject } from "./views/project.js";
import { showProjects } from "./views/projects.js";
import { showReview, stopPolling } from "./views/review.js";

const HEALTH_MS = 15000;
const RECENT_TICK_MS = 60000;

const VIEWS = {
  "new-review": (route) => showNewReview(route.projectId),
  review: (route) => showReview(route.taskId, route.projectId),
  project: (route) => showProject(route.projectId),
  compare: (route) => showComparison(route.projectId, route.base, route.against),
  projects: () => showProjects(),
};

function route() {
  // whichever view is leaving, its polling must not outlive it
  stopPolling();

  const current = parse();
  VIEWS[current.view](current);

  renderRecent();
}

function init() {
  byId("open-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = byId("open-task");
    // accept the workflow id too, since that is what the Temporal UI shows
    const taskId = input.value.trim().replace(/^legal-review-/, "");
    if (!/^[A-Za-z0-9_-]+$/.test(taskId)) {
      input.focus();
      return;
    }
    input.value = "";
    location.hash = `#/review/${taskId}`;
  });

  byId("clear-recent").addEventListener("click", clearRecent);

  // a file dropped outside the drop zone must not navigate away from the page
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  window.addEventListener("hashchange", () => {
    route();
    byId("main").focus({ preventScroll: true });
  });

  // any 401, from any view, brings up the key form
  onUnauthorized(askForKey);

  route();
  checkHealth();
  setInterval(checkHealth, HEALTH_MS);
  // keeps "5 min ago" in the recent list honest
  setInterval(renderRecent, RECENT_TICK_MS);
}

init();
