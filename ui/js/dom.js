/*
 * Building and poking at DOM nodes.
 *
 * Everything the API returns reaches the page through text nodes: `el` appends
 * strings with createTextNode and never assigns innerHTML, so a summary or a
 * question written by the model cannot inject markup.
 */

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);

  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }

  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }

  return node;
}

export function byId(id) {
  return document.getElementById(id);
}

export function setBusy(button, busy, text) {
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = text;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

export function showInlineError(node, message) {
  if (!node) return;
  node.textContent = message;
  node.hidden = !message;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // navigator.clipboard needs a secure context, and plain http on a LAN
    // address is not one, so fall back to the old selection trick
    const area = el("textarea", { class: "visually-hidden", "aria-hidden": "true" });
    area.value = text;
    document.body.append(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

export function copyButton(text, label) {
  const button = el(
    "button",
    {
      type: "button",
      class: "icon-button",
      title: label,
      "aria-label": label,
      onclick: async () => {
        button.textContent = (await copyText(text)) ? "Copied" : "Copy failed";
        setTimeout(() => {
          button.textContent = "Copy";
        }, 1500);
      },
    },
    "Copy",
  );
  return button;
}

export function downloadJson(filename, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
