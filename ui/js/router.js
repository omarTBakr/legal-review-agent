/*
 * Hash routing.
 *
 *   #/new                                  start a review
 *   #/projects                             every project, and the create form
 *   #/projects/<id>                        one project and its reviews
 *   #/projects/<id>/new                    start a review inside a project
 *   #/projects/<id>/reviews/<task_id>      follow that review
 *   #/projects/<id>/compare/<base>/<against>  what changed between two rounds
 *   #/review/<task_id>                     follow a review with no project
 */

const ID = "[A-Za-z0-9_-]+";

const ROUTES = [
  { pattern: new RegExp(`^#/projects/(${ID})/compare/(${ID})/(${ID})$`), view: "compare" },
  { pattern: new RegExp(`^#/projects/(${ID})/reviews/(${ID})$`), view: "review" },
  { pattern: new RegExp(`^#/projects/(${ID})/new$`), view: "new-review" },
  { pattern: new RegExp(`^#/projects/(${ID})$`), view: "project" },
  { pattern: /^#\/projects\/?$/, view: "projects" },
  { pattern: new RegExp(`^#/review/(${ID})$`), view: "review" },
];

export function parse(hash = location.hash) {
  for (const { pattern, view } of ROUTES) {
    const match = hash.match(pattern);
    if (!match) continue;

    if (view === "compare") {
      return { view, projectId: match[1], base: match[2], against: match[3], taskId: "" };
    }

    if (view === "review") {
      // the project form carries both ids; the plain one only a task id
      const [projectId, taskId] = match.length > 2 ? [match[1], match[2]] : ["", match[1]];
      return { view, projectId, taskId };
    }

    return { view, projectId: match[1] || "", taskId: "" };
  }

  return { view: "new-review", projectId: "", taskId: "" };
}

export function currentTaskId() {
  const route = parse();
  return route.view === "review" ? route.taskId : null;
}
