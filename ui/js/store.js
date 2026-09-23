/*
 * What this browser remembers: the reviews opened here, and the project a
 * review belongs to.
 *
 * localStorage is per browser and can be unavailable (private mode, blocked
 * site data), so every read and write is guarded and the page works without
 * it. Nothing here is authoritative: the bucket is.
 */

import { displayName } from "./format.js";

const RECENT_KEY = "legal-review-agent:recent";
const RECENT_LIMIT = 20;

export function loadRecent() {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.taskId === "string")
      .map((item) => ({ ...item, files: Array.isArray(item.files) ? item.files : [] }));
  } catch {
    return [];
  }
}

export function saveRecent(items) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, RECENT_LIMIT)));
  } catch {
    // storage is unavailable; the list just won't persist
  }
}

export function rememberReview(entry) {
  saveRecent([entry, ...loadRecent().filter((item) => item.taskId !== entry.taskId)]);
}

export function forgetReview(taskId) {
  saveRecent(loadRecent().filter((item) => item.taskId !== taskId));
}

export function projectOf(taskId) {
  const item = loadRecent().find((entry) => entry.taskId === taskId);
  return (item && item.projectId) || "";
}

function documentKeys(body) {
  if (Array.isArray(body.documents)) return body.documents.map((doc) => doc.pdf_key);
  return Object.keys(body.documents || {});
}

export function updateRecent(taskId, body) {
  const items = loadRecent();
  let item = items.find((entry) => entry.taskId === taskId);

  if (!item) {
    // opened by id rather than started here
    item = { taskId, files: [], status: body.status, createdAt: Date.now() };
    items.unshift(item);
  } else if (item.status === body.status && item.files.length) {
    return false;
  }

  item.status = body.status;
  if (!item.files.length) item.files = documentKeys(body).map(displayName);

  saveRecent(items);
  return true;
}

export function describeFiles(item) {
  if (!item.files.length) return `Task ${item.taskId}`;
  const [first, ...rest] = item.files;
  return rest.length ? `${first} + ${rest.length} more` : first;
}
