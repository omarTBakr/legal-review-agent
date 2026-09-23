/* The recent-reviews list in the sidebar. */

import { byId, el } from "./dom.js";
import { relativeTime } from "./format.js";
import { statusPill } from "./labels.js";
import { describeFiles, loadRecent, saveRecent } from "./store.js";
import { currentTaskId } from "./router.js";

export function renderRecent() {
  const items = loadRecent();
  const active = currentTaskId();

  byId("recent").replaceChildren(
    ...items.map((item) =>
      el(
        "li",
        {},
        el(
          "a",
          {
            class: "recent-item",
            href: item.projectId ? `#/projects/${item.projectId}/reviews/${item.taskId}` : `#/review/${item.taskId}`,
            "aria-current": item.taskId === active ? "page" : null,
          },
          el("span", { class: "recent-name", title: item.files.join(", ") }, describeFiles(item)),
          el(
            "span",
            { class: "recent-meta" },
            item.status ? statusPill(item.status, true) : null,
            el("span", {}, relativeTime(item.createdAt)),
          ),
        ),
      ),
    ),
  );

  byId("recent-empty").hidden = items.length > 0;
  byId("clear-recent").hidden = items.length === 0;
}

export function clearRecent() {
  saveRecent([]);
  renderRecent();
}
