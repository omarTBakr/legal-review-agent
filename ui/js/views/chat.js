/*
 * Asking questions about a finished review, by voice or by typing.
 *
 * Laid out as a conversation: your question on one side, the answer on the
 * other, in the order they were asked. The answer is written into its bubble
 * as the model produces it rather than appearing all at once, because most of
 * the wait is the writing and a page that shows nothing for ten seconds looks
 * broken.
 *
 * The microphone is an input method, not the feature: with it blocked, absent
 * or the voice service down, typing still works and the answers still arrive.
 */

import { askStreaming, fetchThread, storedAudio, storedTimings, synthesize } from "../api.js";
import { Player } from "../audio.js";
import { el, setBusy, showInlineError } from "../dom.js";
import { displayName, relativeDate } from "../format.js";
import { dictationButton } from "./dictation.js";
import { Reading, splitIntoWords } from "./reading.js";

const AUTOPLAY_KEY = "legal-review-agent:autoplay";
const SPEED_KEY = "legal-review-agent:playback-speed";
const MIN_SPEED = 0.5;
const MAX_SPEED = 2;
const SPEED_STEP = 0.1;

/* --- what this browser remembers ----------------------------------------- */

function autoplayWanted() {
  try {
    return localStorage.getItem(AUTOPLAY_KEY) !== "off";
  } catch {
    return true;
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage blocked; the choice still holds for this page
  }
}

function speedWanted() {
  try {
    return clampSpeed(Number.parseFloat(localStorage.getItem(SPEED_KEY)));
  } catch {
    return 1;
  }
}

function clampSpeed(rate) {
  if (!Number.isFinite(rate)) return 1;

  return Math.min(MAX_SPEED, Math.max(MIN_SPEED, rate));
}

/* --- the panel ------------------------------------------------------------ */

export function chatPanel(projectId, taskId) {
  // one clip at a time: two answers talking over each other helps nobody
  const player = new Player();
  // how many turns the thread holds, which is also the index of the next one:
  // recordings are filed under their turn number
  const counter = { turns: 0 };

  const thread = el("ol", { class: "chat-thread" });
  const empty = el(
    "p",
    { class: "muted small chat-empty" },
    "Ask about these documents — what the cap is, whether there is a non-compete, what to negotiate first. Answers come from the review and the pages themselves.",
  );
  const error = el("p", { class: "form-error", role: "alert", hidden: true });
  const status = el("span", { class: "muted small", role: "status" });

  const question = el("input", {
    id: "chat-question",
    class: "chat-input",
    placeholder: "Ask a question about these documents",
    autocomplete: "off",
  });
  const send = el("button", { type: "submit", class: "button primary" }, "Ask");

  const mic = dictationButton({
    field: question,
    status,
    error,
    label: "Speak",
    // filed under the turn it will become, so the recording sits with the
    // question it produced; read when the recording stops, not now
    context: () => ({ projectId, taskId, turn: counter.turns }),
    onTranscript: (heard) => {
      question.dataset.audioKey = heard.audio_key || "";
      question.dataset.spoken = "true";
    },
  });

  const autoplay = el("input", { type: "checkbox", id: "chat-autoplay", checked: autoplayWanted() });
  autoplay.addEventListener("change", () => remember(AUTOPLAY_KEY, autoplay.checked ? "on" : "off"));

  const speed = el("input", {
    type: "range",
    id: "chat-speed",
    class: "speed",
    min: String(MIN_SPEED),
    max: String(MAX_SPEED),
    step: String(SPEED_STEP),
    value: String(speedWanted()),
    "aria-label": "Playback speed",
  });
  const speedLabel = el("output", { class: "speed-value", for: "chat-speed" }, `${speedWanted().toFixed(1)}×`);

  speed.addEventListener("input", () => {
    const rate = clampSpeed(Number(speed.value));
    speedLabel.textContent = `${rate.toFixed(1)}×`;
    speed.setAttribute("aria-valuetext", `${rate.toFixed(1)} times`);
    // retimes the clip already playing, under the drag, rather than the next one
    player.setRate(rate);
  });
  speed.addEventListener("change", () => remember(SPEED_KEY, String(clampSpeed(Number(speed.value)))));

  // the answer currently being read aloud, so its highlight can be cleared
  // when another one starts
  const room = { projectId, taskId, thread, empty, status, error, send, autoplay, speed, counter, player, reading: null };

  const form = el(
    "form",
    {
      class: "chat-form",
      onsubmit: (event) => {
        event.preventDefault();
        const text = question.value.trim();
        if (!text) return;

        const spoken = question.dataset.spoken === "true";
        const questionAudio = question.dataset.audioKey || "";
        question.value = "";
        question.dataset.spoken = "";
        question.dataset.audioKey = "";

        ask(room, { text, spoken, questionAudio });
      },
    },
    el("label", { for: "chat-question", class: "visually-hidden" }, "Ask a question about these documents"),
    question,
    mic,
    send,
  );

  const panel = el(
    "section",
    { class: "panel chat-panel" },
    el(
      "div",
      { class: "section-head" },
      el("h3", {}, "Ask about this review"),
      el(
        "div",
        { class: "chat-controls muted small" },
        el("label", { class: "autoplay" }, autoplay, " Read answers aloud"),
        el("label", { class: "autoplay speed-control", for: "chat-speed" }, "Speed", speed, speedLabel),
      ),
    ),
    thread,
    empty,
    form,
    el("div", { class: "chat-status" }, status),
    error,
  );

  loadThread(room);

  return panel;
}

async function loadThread(room) {
  try {
    const stored = await fetchThread(room.projectId, room.taskId);
    (stored.turns || []).forEach((turn, index) => {
      room.thread.append(askedRow(turn), answeredRow(room, turn, index).row);
    });
    room.counter.turns = (stored.turns || []).length;
    room.empty.hidden = room.counter.turns > 0;
  } catch {
    // a thread that cannot be read is not worth an error here; asking still works
    room.empty.hidden = false;
  }
}

/* --- one exchange --------------------------------------------------------- */

function askedRow(turn) {
  return el(
    "li",
    { class: "chat-message from-you" },
    el("span", { class: "who" }, turn.spoken ? "🎙 You" : "You"),
    el("div", { class: "bubble" }, turn.question),
  );
}

function answeredRow(room, turn, index) {
  const text = el("div", { class: "bubble answer" }, turn.answer || "");
  const meta = el("div", { class: "muted small chat-meta" });
  const row = el("li", { class: "chat-message from-agent" }, el("span", { class: "who" }, "Review"), text, meta);

  if (turn.answer) finish(room, { turn, index, text, meta });

  return { row, text, meta };
}

/** Fills in the citations, the time and the transport once an answer is whole. */
function finish(room, { turn, index, text, meta }) {
  text.classList.remove("writing");
  // one span per word, so the voice has something to light up as it reads
  text.dataset.words = "true";
  text.__spans = splitIntoWords(text, turn.answer || text.textContent);
  // replaceChildren has no opinion about null, unlike el(): it would append
  // the string "null"
  const parts = [
    transport(room, { turn, index, text }),
    el("span", {}, citationsOf(turn) || "No page matched; answered from the review"),
    turn.asked_at ? el("span", {}, relativeDate(turn.asked_at)) : null,
  ].filter(Boolean);

  meta.replaceChildren(...parts);
}

function citationsOf(turn) {
  const citations = (turn.citations || []).map((citation) => {
    const [key, page] = String(citation).split(" p. ");
    return page ? `${displayName(key)} p. ${page}` : displayName(key);
  });

  return citations.length ? `Read ${citations.join(", ")}` : "";
}

/**
 * Play, pause and resume for one answer.
 *
 * The label follows the player rather than the click, so a clip stopped by
 * another answer starting shows as stopped here too.
 */
function transport(room, { turn, index, text }) {
  const button = el("button", { type: "button", class: "button small transport" }, "▶ Play");

  const render = () => {
    if (!room.player.holds(index)) {
      button.textContent = "▶ Play";
      return;
    }
    button.textContent = room.player.state === "playing" ? "⏸ Pause" : "▶ Resume";
  };

  room.player.onChange(render);

  button.addEventListener("click", async () => {
    if (room.player.holds(index)) {
      room.player.toggle();
      return;
    }

    // busy only while the audio is being fetched or synthesised; once it is
    // playing the button has to be live, or there is no way to press pause
    setBusy(button, true, "…");
    try {
      await speak(room, { answer: turn.answer, index, stored: Boolean(turn.answer_audio), text });
    } catch (failure) {
      showInlineError(room.error, failure.message);
    } finally {
      setBusy(button, false);
      render();
    }
  });

  return button;
}

/**
 * Plays an answer, preferring the recording kept when it was first spoken.
 *
 * Resolves once the clip is *playing*, and hands back `finished` for a caller
 * that wants to know when it ends. Awaiting the end here instead would leave
 * every caller — and the button they came from — stuck for the length of the
 * audio, which is exactly when a pause button needs to work.
 */
async function speak(room, { answer, index, stored = false, text = null }) {
  let audio = null;
  let words = [];

  if (stored) {
    audio = await storedAudio(room.projectId, room.taskId, index, "answer");
    if (audio) words = await storedTimings(room.projectId, room.taskId, index);
  }

  if (!audio) {
    const spoken = await synthesize(answer, { projectId: room.projectId, taskId: room.taskId, turn: index });
    audio = spoken.audio;
    words = spoken.words;
  }

  const reading = text && text.__spans ? new Reading(text.__spans, words) : null;
  room.reading?.clear();
  room.reading = reading;

  const finished = room.player.play(audio, { token: index, rate: clampSpeed(Number(room.speed.value)) });
  reading?.follow(room.player.audio);
  finished.then(() => reading?.clear());

  return { finished };
}

/* --- asking --------------------------------------------------------------- */

async function ask(room, { text, spoken, questionAudio }) {
  const index = room.counter.turns;
  const answer = answeredRow(room, { question: text, answer: "" }, index);

  room.thread.append(askedRow({ question: text, spoken }), answer.row);
  room.empty.hidden = true;
  answer.text.classList.add("writing");
  answer.row.scrollIntoView({ block: "nearest", behavior: "smooth" });

  setBusy(room.send, true, "Asking…");
  showInlineError(room.error, "");
  room.status.textContent = "Reading the documents…";

  try {
    const body = await askStreaming(room.projectId, room.taskId, text, {
      spoken,
      questionAudio,
      onDelta: (chunk) => {
        // the first words are the signal that it is working; everything after
        // that just keeps up
        room.status.textContent = "";
        answer.text.append(chunk);
        answer.row.scrollIntoView({ block: "nearest" });
      },
    });

    room.counter.turns = body.turn_count;
    finish(room, { turn: body.turn, index, text: answer.text, meta: answer.meta });
    setBusy(room.send, false);

    if (room.autoplay.checked) {
      room.status.textContent = "Speaking…";
      const { finished } = await speak(room, { answer: body.turn.answer, index, text: answer.text });
      await finished;
    }
  } catch (failure) {
    answer.text.classList.remove("writing");
    // whatever arrived before it failed is still on screen and still true
    if (!answer.text.textContent) answer.row.remove();
    showInlineError(room.error, failure.message);
  } finally {
    room.status.textContent = "";
    setBusy(room.send, false);
  }
}
