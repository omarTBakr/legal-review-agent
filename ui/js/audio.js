/*
 * Recording a question, and playing an answer.
 *
 * The browser records whatever codec it prefers (webm/opus on Chrome, mp4 on
 * Safari), decodes it here, mixes it to mono, resamples to 16 kHz and writes a
 * WAV. Doing that in the page means the server needs no ffmpeg and the voice
 * service is handed exactly the format the ASR model wants.
 */

const TARGET_RATE = 16000;
// a spoken question; past this something has gone wrong with the stop button
const MAX_SECONDS = 120;

export function isSupported() {
  return Boolean(navigator.mediaDevices && window.MediaRecorder && (window.AudioContext || window.webkitAudioContext));
}

export class Recorder {
  constructor() {
    this.recorder = null;
    this.stream = null;
    this.chunks = [];
    // the live level of what the microphone is hearing, for the meter that
    // tells you it is actually picking you up
    this.context = null;
    this.analyser = null;
    this.samples = null;
  }

  /**
   * How loud the microphone is right now, from 0 to 1.
   *
   * Root mean square of the current window rather than the peak, so it tracks
   * speech rather than jumping at every click, and scaled so a normal speaking
   * voice uses most of the meter.
   */
  level() {
    if (!this.analyser) return 0;

    this.analyser.getByteTimeDomainData(this.samples);

    let sum = 0;
    for (const sample of this.samples) {
      const centred = (sample - 128) / 128;
      sum += centred * centred;
    }

    return Math.min(1, Math.sqrt(sum / this.samples.length) * 4);
  }

  get recording() {
    return Boolean(this.recorder && this.recorder.state === "recording");
  }

  /** Asks for the microphone and starts recording. Throws if permission is refused. */
  async start() {
    if (this.recording) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    this.listen();

    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream);
    this.recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size) this.chunks.push(event.data);
    });
    this.recorder.start();

    // a forgotten recording should not grow until the tab runs out of memory
    this.limit = setTimeout(() => this.recording && this.recorder.stop(), MAX_SECONDS * 1000);
  }

  /** Stops, and resolves with the recording as 16 kHz mono WAV bytes. */
  async stop() {
    if (!this.recorder) return null;

    const finished = new Promise((resolve) => this.recorder.addEventListener("stop", resolve, { once: true }));
    if (this.recorder.state !== "inactive") this.recorder.stop();
    await finished;

    clearTimeout(this.limit);
    this.release();

    const blob = new Blob(this.chunks, { type: this.chunks[0] ? this.chunks[0].type : "audio/webm" });
    this.chunks = [];

    return blob.size ? toWav(await blob.arrayBuffer()) : null;
  }

  /** Taps the live stream for the level meter. Failing is not fatal: no meter. */
  listen() {
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      this.context = new Context();
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 1024;
      this.samples = new Uint8Array(this.analyser.fftSize);
      this.context.createMediaStreamSource(this.stream).connect(this.analyser);
    } catch {
      this.analyser = null;
    }
  }

  /** Drops the microphone, so the browser stops showing the recording indicator. */
  release() {
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
    if (this.context) {
      this.context.close();
      this.context = null;
    }
    this.analyser = null;
    this.recorder = null;
  }
}

async function toWav(buffer) {
  const Context = window.AudioContext || window.webkitAudioContext;
  const context = new Context();

  try {
    const decoded = await context.decodeAudioData(buffer);
    return encodeWav(resample(toMono(decoded), decoded.sampleRate, TARGET_RATE), TARGET_RATE);
  } finally {
    // each recording otherwise leaves an audio context behind, and browsers
    // allow only a handful of them
    context.close();
  }
}

function toMono(decoded) {
  const [first] = [decoded.getChannelData(0)];
  if (decoded.numberOfChannels === 1) return first;

  const mixed = new Float32Array(first.length);
  for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
    const data = decoded.getChannelData(channel);
    for (let i = 0; i < mixed.length; i += 1) mixed[i] += data[i] / decoded.numberOfChannels;
  }

  return mixed;
}

function resample(samples, rate, targetRate) {
  if (rate === targetRate) return samples;

  const ratio = rate / targetRate;
  const out = new Float32Array(Math.floor(samples.length / ratio));

  // linear interpolation: plenty for speech, and it keeps this file dependency-free
  for (let i = 0; i < out.length; i += 1) {
    const position = i * ratio;
    const index = Math.floor(position);
    const next = Math.min(index + 1, samples.length - 1);
    out[i] = samples[index] + (samples[next] - samples[index]) * (position - index);
  }

  return out;
}

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const text = (offset, value) => {
    for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
  };

  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true); // PCM header length
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // bytes per second
  view.setUint16(32, 2, true); // bytes per sample
  view.setUint16(34, 16, true); // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);

  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}

/**
 * One clip at a time, with a transport the page can drive.
 *
 * A panel owns a Player; each answer's button asks it to play that answer, or
 * pauses it if it is already the one playing. Starting a second clip stops the
 * first, because two answers talking over each other helps nobody.
 *
 * `preservesPitch` keeps a voice sped up to 1.5x sounding like the same person
 * talking faster rather than like a chipmunk.
 */
export class Player {
  constructor() {
    this.audio = null;
    this.url = "";
    // what is playing: whatever the caller used to identify it
    this.token = null;
    this.rate = 1;
    this.listeners = new Set();
  }

  /** "playing", "paused", or "idle". */
  get state() {
    if (!this.audio) return "idle";

    return this.audio.paused ? "paused" : "playing";
  }

  /** Whether `token` is the clip the player currently holds. */
  holds(token) {
    return this.token !== null && this.token === token;
  }

  onChange(listener) {
    this.listeners.add(listener);

    return () => this.listeners.delete(listener);
  }

  announce() {
    for (const listener of this.listeners) listener(this);
  }

  /** Plays `blob`, replacing whatever was playing. Resolves when it ends. */
  play(blob, { token = null, rate = this.rate } = {}) {
    this.stop();

    this.url = URL.createObjectURL(blob);
    this.token = token;
    this.rate = rate;

    const audio = new Audio(this.url);
    audio.playbackRate = rate;
    audio.preservesPitch = true;
    // in the document, so the speed slider can find it with querySelectorAll
    // and retime it mid-sentence; hidden, because the page has its own controls
    audio.hidden = true;
    document.body.append(audio);
    this.audio = audio;

    audio.addEventListener("play", () => this.announce());
    audio.addEventListener("pause", () => this.announce());

    return new Promise((resolve) => {
      const done = () => {
        this.stop();
        resolve();
      };
      audio.addEventListener("ended", done, { once: true });
      audio.addEventListener("error", done, { once: true });
      // autoplay can be blocked; that is not worth an error, the text is there
      audio.play().catch(done);
      this.announce();
    });
  }

  /** Pauses if playing, resumes if paused. */
  toggle() {
    if (!this.audio) return;

    if (this.audio.paused) this.audio.play().catch(() => this.stop());
    else this.audio.pause();

    this.announce();
  }

  stop() {
    if (this.audio) {
      this.audio.pause();
      this.audio.remove();
      this.audio = null;
    }
    if (this.url) {
      URL.revokeObjectURL(this.url);
      this.url = "";
    }
    this.token = null;
    this.announce();
  }

  setRate(rate) {
    this.rate = rate;
    if (this.audio) this.audio.playbackRate = rate;
  }
}
