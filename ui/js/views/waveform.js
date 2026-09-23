/*
 * The bars that move while you talk.
 *
 * Confirmation, not decoration: a microphone that is muted, picking the wrong
 * device, or not hearing you looks exactly like one that is working until
 * something moves. This reads the live level and shows it, so a silent
 * recording is obvious before it is sent rather than after it comes back as
 * an empty transcript.
 */

import { el } from "../dom.js";

const BARS = 9;
// each bar leans towards its target rather than jumping, so the row reads as a
// voice rather than as flickering
const SMOOTHING = 0.35;

export class Waveform {
  constructor(bars = BARS) {
    this.heights = new Array(bars).fill(0);
    this.bars = this.heights.map(() => el("span", { class: "wave-bar" }));
    this.node = el("span", { class: "wave", "aria-hidden": "true" }, ...this.bars);
    this.frame = null;
    this.source = null;
  }

  /** Starts animating from `source()`, a function returning a level from 0 to 1. */
  start(source) {
    this.source = source;
    this.node.classList.add("live");

    if (this.frame === null) this.frame = requestAnimationFrame(() => this.tick());
  }

  /** Stops, and settles the bars back to their resting height. */
  stop() {
    this.source = null;

    if (this.frame !== null) {
      cancelAnimationFrame(this.frame);
      this.frame = null;
    }

    this.node.classList.remove("live");
    this.heights.fill(0);
    this.bars.forEach((bar) => bar.style.setProperty("--height", "12%"));
  }

  tick() {
    const level = this.source ? this.source() : 0;

    // the newest level enters at one end and travels along, so the row shows
    // the last moment of speech rather than one number repeated nine times
    this.heights.pop();
    this.heights.unshift(level);

    this.heights.forEach((height, index) => {
      const previous = Number.parseFloat(this.bars[index].dataset.level || "0");
      const smoothed = previous + (height - previous) * SMOOTHING;
      this.bars[index].dataset.level = String(smoothed);
      // a floor so the bars stay visible when nothing is being said
      this.bars[index].style.setProperty("--height", `${Math.max(12, smoothed * 100)}%`);
    });

    this.frame = requestAnimationFrame(() => this.tick());
  }
}
