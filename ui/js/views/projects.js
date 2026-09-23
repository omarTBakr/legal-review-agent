/* The project list, and the form that creates one. */

import { createProject, fetchProjects } from "../api.js";
import { byId, el, setBusy, showInlineError } from "../dom.js";
import { plural, relativeDate } from "../format.js";

export async function showProjects() {
  byId("main").replaceChildren(
    el("section", { class: "panel" }, el("h2", {}, "Projects"), el("p", { class: "muted" }, "Loading…")),
  );

  let projects = [];
  let failure = "";

  try {
    projects = (await fetchProjects()).projects || [];
  } catch (error) {
    failure = error.message;
  }

  byId("main").replaceChildren(
    newProjectPanel(),
    el(
      "section",
      { class: "panel" },
      el(
        "div",
        { class: "section-head" },
        el("h3", {}, "Your projects"),
        el("span", { class: "muted small" }, projects.length ? plural(projects.length, "project", "projects") : ""),
      ),
      failure ? el("p", { class: "form-error" }, failure) : null,
      projects.length ? el("ul", { class: "project-list" }, projects.map(projectRow)) : null,
      !failure && !projects.length
        ? el("p", { class: "muted" }, "No projects yet. A project is a folder in the bucket that its reviews are filed under.")
        : null,
    ),
  );
}

function projectRow(project) {
  return el(
    "li",
    {},
    el(
      "a",
      { class: "project-row", href: `#/projects/${project.id}` },
      el(
        "span",
        { class: "project-main" },
        el("strong", {}, project.name),
        project.description ? el("span", { class: "muted small" }, project.description) : null,
      ),
      el(
        "span",
        { class: "project-meta muted small" },
        project.email ? el("span", { title: `Reports are emailed to ${project.email}` }, "✉ " + project.email) : null,
        el("span", {}, relativeDate(project.created_at)),
      ),
    ),
  );
}

function newProjectPanel() {
  const name = el("input", { id: "project-name", required: true, maxlength: "120", placeholder: "Acme — NDAs" });
  const description = el("textarea", { id: "project-description", rows: "2", maxlength: "2000", placeholder: "What this project covers" });
  const email = el("input", { id: "project-email", type: "email", placeholder: "nobody@example.com", autocomplete: "email" });
  const error = el("p", { class: "form-error", role: "alert", hidden: true });
  const button = el("button", { type: "submit", class: "button primary" }, "Create project");

  const form = el(
    "form",
    {
      class: "project-form",
      onsubmit: async (event) => {
        event.preventDefault();

        if (!name.value.trim()) {
          showInlineError(error, "A project needs a name.");
          name.focus();
          return;
        }

        setBusy(button, true, "Creating…");
        showInlineError(error, "");

        try {
          const project = await createProject({
            name: name.value.trim(),
            description: description.value.trim(),
            email: email.value.trim(),
          });
          location.hash = `#/projects/${project.id}`;
        } catch (failure) {
          showInlineError(error, failure.message);
          setBusy(button, false);
        }
      },
    },
    el("div", { class: "field" }, el("label", { for: "project-name" }, "Name"), name),
    el(
      "div",
      { class: "field" },
      el("label", { for: "project-description" }, "Description ", el("span", { class: "muted small" }, "optional")),
      description,
    ),
    el(
      "div",
      { class: "field" },
      el("label", { for: "project-email" }, "Email reports to ", el("span", { class: "muted small" }, "optional")),
      email,
      el("p", { class: "muted small" }, "Every review in this project emails its finished report here. You can change it per review."),
    ),
    el("div", { class: "actions" }, button),
    error,
  );

  return el(
    "section",
    { class: "panel" },
    el("h2", {}, "New project"),
    el("p", { class: "muted" }, "A project gets its own folder in the bucket. Its documents, advice and review records all live there."),
    form,
  );
}
