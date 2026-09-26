/**
 * The sound check's test sound, played in the page: the interface runs in a browser on
 * the same machine, so what it plays goes out through the computer's speakers and comes
 * back on the loopback Upshot records — which is exactly what the check has to prove.
 */
/** C, E, G: soft sine notes, loud enough to hear, never startling. */
export function chime(): void {
  try {
    const ctx = new AudioContext();
    [523.25, 659.25, 783.99].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const at = ctx.currentTime + i * 0.45;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(0.18, at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, at + 0.6);
      osc.connect(gain).connect(ctx.destination);
      osc.start(at);
      osc.stop(at + 0.65);
    });
    setTimeout(() => void ctx.close(), 2000);
  } catch {
    /* no audio output in this browser: the scripted level still plays */
  }
}

/** How long the chime lasts, and so how long the check listens. */
export const CHIME_SECONDS = 1.8;
