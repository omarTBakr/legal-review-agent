/* The words and chips for the API's enum values, in one place. */

import { el } from "./dom.js";

export const RUNNING = new Set(["processing", "awaiting_human"]);
export const SEVERITIES = ["critical", "high", "medium", "low"];

export const STATUS_LABELS = {
  loading: "Loading",
  processing: "Processing",
  awaiting_human: "Needs your answer",
  completed: "Completed",
  failed: "Failed",
  canceled: "Canceled",
  terminated: "Terminated",
  timed_out: "Timed out",
  continued_as_new: "Continued",
};

export const DOCUMENT_LABELS = {
  processing: "In progress",
  awaiting_human: "Waiting for your answer",
  completed: "Done",
};

export const DECISION_LABELS = {
  auto_approved: "Auto-approved",
  human_approved: "Revised with your answer",
  human_rejected: "Rejected by a reviewer",
  unreviewed_timeout: "Unreviewed",
};

export function statusPill(status, small = false) {
  return el("span", { class: `pill pill-${status}${small ? " pill-small" : ""}` }, STATUS_LABELS[status] || status || "Unknown");
}

export function severityChip(severity, text) {
  return el("span", { class: `severity severity-${severity}` }, text || severity);
}

export function severityRank(severity) {
  const rank = SEVERITIES.indexOf(severity);
  return rank === -1 ? SEVERITIES.length : rank;
}
