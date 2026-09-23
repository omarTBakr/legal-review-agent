/*
 * Every call the page makes to the API.
 *
 * The paths live in `endpoints` rather than being spelled out at each call
 * site, so a route rename shows up in one file — and so a test can check them
 * against the OpenAPI schema.
 */

import { byId } from "./dom.js";

export const endpoints = {
  health: "/health",
  legal: "/legal",
  review: (taskId) => `/legal/${encodeURIComponent(taskId)}`,
  respond: (taskId) => `/legal/${encodeURIComponent(taskId)}/respond`,
  projects: "/projects",
  project: (projectId) => `/projects/${encodeURIComponent(projectId)}`,
  projectReview: (projectId, taskId) => `/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(taskId)}`,
  chat: (projectId, taskId) => `/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(taskId)}/chat`,
  chatStream: (projectId, taskId) =>
    `/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(taskId)}/chat/stream`,
  chatAudio: (projectId, taskId, turn, kind) =>
    `/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(taskId)}/audio/${turn}/${kind}`,
  transcribe: "/voice/transcribe",
  speak: "/voice/speak",
  timings: (projectId, taskId, turn) =>
    `/voice/timings/${encodeURIComponent(projectId)}/${encodeURIComponent(taskId)}/${turn}`,
};

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(0, "Can't reach the API. Is it running (uv run python main.py)?");
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    // not JSON; the status code is all there is
  }

  if (!response.ok) {
    throw new ApiError(response.status, describeError(response.status, body));
  }

  return body;
}

export function describeError(status, body) {
  const detail = body && body.detail;
  let text = `The API answered ${status}`;

  if (typeof detail === "string") text = detail;
  // FastAPI's validation errors are a list of {loc, msg}
  else if (Array.isArray(detail)) text = detail.map((item) => item.msg).join("; ");

  if (status === 503) {
    // 503 has two quite different causes here, and the wrong hint sends you
    // looking in the wrong place
    return text.includes("voice service")
      ? `${text}. Start it with: uv run --directory voice main.py`
      : `${text}. Check that the Temporal server and the legal worker are running.`;
  }
  return text;
}

function postJson(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/* --- reviews ------------------------------------------------------------- */

export function startReview(files, { projectId = "", email = "" } = {}) {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  if (projectId) form.append("project_id", projectId);
  if (email) form.append("email", email);

  return api(endpoints.legal, { method: "POST", body: form });
}

export function fetchReview(taskId) {
  return api(endpoints.review(taskId));
}

export function answerQuestion(taskId, pdfKey, answer) {
  return postJson(endpoints.respond(taskId), { pdf_key: pdfKey, answer });
}

/* --- projects ------------------------------------------------------------ */

export function createProject({ name, description = "", email = "" }) {
  return postJson(endpoints.projects, { name, description, email });
}

export function fetchProjects() {
  return api(endpoints.projects);
}

export function fetchProject(projectId) {
  return api(endpoints.project(projectId));
}

export function fetchStoredReview(projectId, taskId) {
  return api(endpoints.projectReview(projectId, taskId));
}

/* --- chat ---------------------------------------------------------------- */

export function fetchThread(projectId, taskId) {
  return api(endpoints.chat(projectId, taskId));
}

export function askQuestion(projectId, taskId, question, { spoken = false, questionAudio = "" } = {}) {
  return postJson(endpoints.chat(projectId, taskId), { question, spoken, question_audio: questionAudio });
}

/**
 * Asks, and calls `onDelta` with the answer as it is written.
 *
 * Resolves with the stored turn once the model has finished. A failure before
 * the first byte is an ApiError with a status, as everywhere else; one after
 * that arrives as an `error` event, because by then the 200 has been sent.
 */
export async function askStreaming(projectId, taskId, question, { spoken = false, questionAudio = "", onDelta } = {}) {
  let response;
  try {
    response = await fetch(endpoints.chatStream(projectId, taskId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, spoken, question_audio: questionAudio }),
    });
  } catch {
    throw new ApiError(0, "Can't reach the API. Is it running (uv run main.py)?");
  }

  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      // not JSON; the status is all there is
    }
    throw new ApiError(response.status, describeError(response.status, body));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // events are separated by a blank line; a partial one stays in the buffer
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop();

    for (const block of blocks) {
      const event = parseEvent(block);
      if (!event) continue;

      if (event.name === "delta" && onDelta) onDelta(event.payload.text || "");
      if (event.name === "error") throw new ApiError(0, event.payload.detail || "the answer stopped");
      if (event.name === "turn") finished = event.payload;
    }
  }

  if (!finished) throw new ApiError(0, "The answer ended before it was finished.");

  return finished;
}

function parseEvent(block) {
  const lines = block.split("\n").filter(Boolean);
  const name = lines.find((line) => line.startsWith("event:"));
  const data = lines.find((line) => line.startsWith("data:"));

  if (!name || !data) return null;

  try {
    return { name: name.slice(6).trim(), payload: JSON.parse(data.slice(5).trim()) };
  } catch {
    return null;
  }
}

/* --- voice --------------------------------------------------------------- */

export async function transcribe(wav, { projectId = "", taskId = "", turn = -1 } = {}) {
  const form = new FormData();
  form.append("audio", wav, "question.wav");

  // with a turn to file it under, the recording is kept beside the thread
  if (projectId && taskId && turn >= 0) {
    form.append("project_id", projectId);
    form.append("task_id", taskId);
    form.append("turn", String(turn));
  }

  return api(endpoints.transcribe, { method: "POST", body: form });
}

/**
 * Reads `text` aloud, and says when each word is spoken.
 *
 * Asks for JSON rather than a WAV so the timings come with the audio: the
 * page needs both to highlight the word being read, and a second request
 * would mean synthesising it twice.
 */
export async function synthesize(text, { projectId = "", taskId = "", turn = -1 } = {}) {
  const response = await fetch(endpoints.speak, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ text, project_id: projectId, task_id: taskId, turn }),
  });

  if (!response.ok) {
    let detail = `The API answered ${response.status}`;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      // not JSON; the status is all there is
    }
    throw new ApiError(response.status, detail);
  }

  const body = await response.json();

  return { audio: base64ToBlob(body.audio), words: body.words || [] };
}

function base64ToBlob(encoded) {
  const binary = atob(encoded || "");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);

  return new Blob([bytes], { type: "audio/wav" });
}

/** The stored timings for an answer that was spoken before. */
export async function storedTimings(projectId, taskId, turn) {
  try {
    return (await api(endpoints.timings(projectId, taskId, turn))).words || [];
  } catch {
    // no timings kept: the clip still plays, it just does not follow along
    return [];
  }
}

/** A recording that was kept, or null when there is none. */
export async function storedAudio(projectId, taskId, turn, kind) {
  const response = await fetch(endpoints.chatAudio(projectId, taskId, turn, kind));

  return response.ok ? response.blob() : null;
}

/* --- health -------------------------------------------------------------- */

export async function checkHealth() {
  const node = byId("api-status");
  const label = node.querySelector(".status-text");
  try {
    await api(endpoints.health);
    node.dataset.state = "ok";
    label.textContent = "API connected";
  } catch {
    node.dataset.state = "down";
    label.textContent = "API unreachable";
  }
}
