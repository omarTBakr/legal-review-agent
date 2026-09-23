/*
 * Following the voice through the answer, a word at a time.
 *
 * The model tells us when it says each word, so the page does not guess: the
 * highlight lands on the word being spoken and moves off it when the voice
 * does. Where an engine cannot say — Qwen3-TTS cannot — nothing is highlighted
 * at all, because a highlight drifting a sentence behind the voice is worse
 * than none.
 *
 * The text is split into spans once, when the answer is finished, and matched
 * to the spoken words in order. The two lists rarely line up exactly: the
 * voice says "12,000" as three tokens and the page shows it as one, so the
 * matching walks forward and skips what it cannot place rather than losing
 * its place for the rest of the paragraph.
 */

const WORD = /\S+/g;

/** Replaces a bubble's text with one span per word, and returns the spans. */
export function splitIntoWords(node, text) {
  const spans = [];
  let last = 0;

  node.replaceChildren();

  for (const match of text.matchAll(WORD)) {
    if (match.index > last) node.append(text.slice(last, match.index));

    const span = document.createElement("span");
    span.className = "word";
    span.textContent = match[0];
    node.append(span);
    spans.push(span);

    last = match.index + match[0].length;
  }

  if (last < text.length) node.append(text.slice(last));

  return spans;
}

/** Strips punctuation and case, which is all the two sides need to agree on. */
function normalize(word) {
  return String(word)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, "");
}

/**
 * Lines the spoken words up with the spans on the page.
 *
 * Returns, for each timing, the span it belongs to — or null when the word
 * could not be placed, which happens with numbers and abbreviations the voice
 * splits differently.
 */
export function alignWords(spans, words) {
  const plan = [];
  let cursor = 0;

  for (const timing of words) {
    const spoken = normalize(timing.word);
    if (!spoken) {
      plan.push(null);
      continue;
    }

    let found = null;

    // look ahead a few spans: the page may show punctuation or a joined-up
    // number where the voice produced several tokens
    for (let i = cursor; i < Math.min(spans.length, cursor + 6); i += 1) {
      const shown = normalize(spans[i].textContent);
      if (!shown) continue;

      if (shown === spoken || shown.startsWith(spoken) || spoken.startsWith(shown)) {
        found = spans[i];
        cursor = i + 1;
        break;
      }
    }

    plan.push(found);
  }

  return plan;
}

/**
 * Highlights the word being spoken, until the clip ends or is stopped.
 *
 * Driven by requestAnimationFrame off `audio.currentTime`, so it follows a
 * pause, a resume and a change of speed without being told about any of them.
 */
export class Reading {
  constructor(spans, words) {
    this.spans = spans;
    this.words = words;
    this.plan = alignWords(spans, words);
    this.frame = null;
    this.current = null;
  }

  get possible() {
    return this.spans.length > 0 && this.words.length > 0;
  }

  follow(audio) {
    if (!this.possible || !audio) return;

    const tick = () => {
      this.at(audio.currentTime);
      this.frame = requestAnimationFrame(tick);
    };

    this.stop();
    this.frame = requestAnimationFrame(tick);
  }

  /** Lights the word being spoken at `seconds`. */
  at(seconds) {
    let span = null;

    for (let i = 0; i < this.words.length; i += 1) {
      const word = this.words[i];
      if (seconds >= word.start && seconds < word.end) {
        span = this.plan[i];
        break;
      }
      // past this word: remember it, so a gap between words keeps the
      // highlight on the one just said rather than blinking off
      if (seconds >= word.end) span = this.plan[i];
    }

    if (span === this.current) return;

    if (this.current) this.current.classList.remove("speaking");
    if (span) span.classList.add("speaking");
    this.current = span;
  }

  stop() {
    if (this.frame !== null) {
      cancelAnimationFrame(this.frame);
      this.frame = null;
    }
  }

  clear() {
    this.stop();
    if (this.current) this.current.classList.remove("speaking");
    this.current = null;
  }
}
