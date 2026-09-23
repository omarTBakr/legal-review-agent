/*
 * "Speak into this field."
 *
 * One microphone button, bound to whatever input or textarea it is given: the
 * chat box, and the form where you answer the model's question about a
 * document. Both are places where a reviewer is reading a contract with their
 * hands somewhere else, and both put the transcript in the field rather than
 * sending it — recognition mishears, and an answer nobody checked is worse
 * here than in chat, because it is what the advice gets revised with.
 *
 * Only one recording runs at a time across the page: the browser gives out one
 * microphone, and two buttons fighting over it is a bug waiting to happen.
 */

import { transcribe } from "../api.js";
import { Recorder, isSupported } from "../audio.js";
import { el, showInlineError } from "../dom.js";
import { Waveform } from "./waveform.js";

const recorder = new Recorder();
let recordingNow = null;

/**
 * A mic button that writes what it hears into `field`.
 *
 * `status` is where progress is written, `error` where failures go, and
 * `context` optionally says which review the recording belongs to, so it can
 * be kept in the bucket; without it the audio is transcribed and dropped. It
 * is a function, not an object, because the turn a recording belongs to is
 * only known when the recording stops.
 */
export function dictationButton({ field, status, error, context = null, label = "Speak", onTranscript = null }) {
  const wave = new Waveform();
  const text = el("span", { class: "mic-label" }, `🎙 ${label}`);

  const button = el(
    "button",
    {
      type: "button",
      class: "button mic",
      title: isSupported() ? "Record an answer instead of typing it" : "This browser cannot record audio",
      disabled: !isSupported(),
    },
    text,
    wave.node,
  );

  const idle = () => {
    text.textContent = `🎙 ${label}`;
    button.classList.remove("recording");
    wave.stop();
  };

  button.addEventListener("click", async () => {
    if (recordingNow === button) {
      idle();
      say(status, "Transcribing…");

      try {
        const wav = await recorder.stop();
        recordingNow = null;
        if (!wav) {
          say(status, "");
          return;
        }

        const heard = await transcribe(wav, context ? context() : {});
        write(field, heard.text);
        if (onTranscript) onTranscript(heard);
        say(status, "Check what it heard before sending.");
        field.focus();
      } catch (failure) {
        recordingNow = null;
        say(status, "");
        showInlineError(error, failure.message);
      }
      return;
    }

    if (recordingNow) return; // another field is already listening

    showInlineError(error, "");

    try {
      await recorder.start();
      recordingNow = button;
      text.textContent = "◼ Stop";
      button.classList.add("recording");
      wave.start(() => recorder.level());
      say(status, "Listening… the bars move when it hears you.");
    } catch {
      showInlineError(error, "The microphone is not available. Check the browser's permission for this page, then try again.");
    }
  });

  return button;
}

function say(status, message) {
  if (status) status.textContent = message;
}

function write(field, heard) {
  // appended rather than replacing, so a half-typed answer is not thrown away
  const existing = field.value.trim();
  field.value = existing ? `${existing} ${heard}` : heard;
}
