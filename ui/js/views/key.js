/*
 * Asking for the API key when the API says 401.
 *
 * An inline panel rather than `window.prompt`: a browser modal blocks every
 * script on the page until it is dismissed, which breaks anything driving the
 * page and cannot be styled or explained.
 *
 * It renders once. A page load makes several requests and they will all come
 * back 401 together; four stacked copies of the same form is not four times the
 * information.
 */

import { rememberKey } from "../api.js";
import { byId, el } from "../dom.js";

const GATE_ID = "key-gate";

export function askForKey() {
  if (byId(GATE_ID)) return;

  const input = el("input", {
    type: "password",
    id: "api-key-input",
    name: "api-key",
    autocomplete: "current-password",
    placeholder: "API key",
    required: "",
  });

  const form = el(
    "form",
    { class: "panel key-panel" },
    el("h2", {}, "This API needs a key"),
    el(
      "p",
      { class: "muted" },
      "Whoever runs the service set ",
      el("code", {}, "API_KEY"),
      ". Paste it here and it is kept for this tab only.",
    ),
    el("div", { class: "field" }, el("label", { for: "api-key-input" }, "Key"), input),
    el("div", { class: "actions" }, el("button", { class: "button primary", type: "submit" }, "Unlock")),
  );

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const key = input.value.trim();
    if (!key) {
      input.focus();
      return;
    }

    rememberKey(key);
    // a reload rather than re-running the current view: several requests failed
    // to get here, and replaying exactly the ones that did is more bookkeeping
    // than it is worth
    location.reload();
  });

  document.body.append(el("div", { class: "key-gate", id: GATE_ID }, form));
  input.focus();
}
