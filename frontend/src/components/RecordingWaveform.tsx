import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";

/** One bar per reading. The server sends 20 a second, so a 900 px lane holds 15 s. */
const BAR_PX = 2;
const GAP_PX = 1;
const READINGS_PER_S = 20;
/** Enough readings to fill a very wide screen; older ones are dropped. */
const HISTORY = 1500;

/**
 * Below this the lane counts as silent: -48 dBFS, under a quiet room's microphone hiss
 * on most laptops and well under speech (around -26 dBFS). Said after three seconds of
 * it, so the gaps between sentences never set it off.
 */
const SILENT_BELOW = 10 ** (-48 / 20);
const SILENT_AFTER_S = 3;

/*
 * How strongly each kind of bar is drawn, in the accent. The canvas cannot take a CSS
 * variable, so the colour is read from --accent at draw time (it is a plain colour in
 * both themes) and only its strength is set here.
 */
const ALPHA_SOUND = 1;
const ALPHA_QUIET = 0.3;
const ALPHA_PAUSED = 0.15;
const ALPHA_AXIS = 0.25;

export interface Reading {
  me: number;
  them: number;
  paused: boolean;
}

/** -60 dBFS..0 across the lane. Speech sits near 0.05 linear, invisible on a linear scale. */
function toFraction(value: number): number {
  return value <= 0 ? 0 : Math.min(1, Math.max(0, (20 * Math.log10(value) + 60) / 60));
}

/**
 * The two tracks as one level, the way they would sound played together: uncorrelated
 * signals add in power, so the mixed RMS is the root of the summed squares. Only the
 * picture is mixed; the recorder still writes the microphone and the computer's audio
 * to separate files.
 */
export function mixLevel(me: number, them: number): number {
  return Math.sqrt(me * me + them * them);
}

/**
 * The run of silent readings so far, with one more reading added: it grows while both
 * tracks stay under the line and starts again at 0 on any sound. A paused stretch is
 * not silence: nothing is being recorded, and the bar already says Paused.
 *
 * Kept as a count beside the drawing history rather than read back from it, because
 * that history is capped and a count taken from it stopped growing at 75 s.
 */
export function silentRun(run: number, reading: Reading): number {
  return reading.paused || mixLevel(reading.me, reading.them) >= SILENT_BELOW ? 0 : run + 1;
}

/** Whole seconds of a silent run, or 0 while it is too short to say so. */
export function silentSeconds(run: number): number {
  const seconds = Math.floor(run / READINGS_PER_S);
  return seconds >= SILENT_AFTER_S ? seconds : 0;
}

/**
 * A scrolling waveform of the meeting being recorded, both tracks mixed into one lane,
 * newest at the right edge, mirrored about the centre the way Audacity draws a track.
 *
 * It is the most distinctive thing on the screen, so it gets the room for it: tall, no
 * box around it, drawn in the accent. And it answers the one question a glance at it is
 * for, "is it hearing anything?": quiet bars fade, and after a few seconds of nothing on
 * either track it says so in words, with how long.
 *
 * Fed by `/api/recording/levels`, which only ever reads the recorder: it cannot open a
 * device, so leaving this on screen after Stop can never take the microphone.
 */
export default function RecordingWaveform({ paused = false }: { paused?: boolean }) {
  const { t } = useI18n();
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const history = useRef<Reading[]>([]);
  const run = useRef(0);
  const [silent, setSilent] = useState(0);

  useEffect(() => {
    const node = canvas.current;
    if (!node) return undefined;

    // Drawn when a reading arrives or the size changes, not every animation frame: the
    // picture only changes 20 times a second.
    const draw = () => {
      const width = node.clientWidth;
      const height = node.clientHeight;
      const ratio = window.devicePixelRatio || 1;
      if (node.width !== Math.round(width * ratio) || node.height !== Math.round(height * ratio)) {
        node.width = Math.round(width * ratio);
        node.height = Math.round(height * ratio);
      }
      const context = node.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      // Read per draw, so a theme switch mid-recording repaints in the new accent.
      context.fillStyle = getComputedStyle(node).getPropertyValue("--accent").trim();

      const step = BAR_PX + GAP_PX;
      const readings = history.current.slice(-Math.floor(width / step));
      const origin = width - readings.length * step;
      const middle = height / 2;
      context.globalAlpha = ALPHA_AXIS;
      context.fillRect(0, middle - 0.5, width, 1);
      readings.forEach((reading, position) => {
        const level = mixLevel(reading.me, reading.them);
        const half = Math.max(0.5, toFraction(level) * (middle - 2));
        context.globalAlpha = reading.paused ? ALPHA_PAUSED : level < SILENT_BELOW ? ALPHA_QUIET : ALPHA_SOUND;
        context.fillRect(origin + position * step, middle - half, BAR_PX, half * 2);
      });
      context.globalAlpha = 1;
    };

    const source = new EventSource("/api/recording/levels");
    source.onmessage = (event) => {
      const reading = JSON.parse(event.data) as Reading & { done?: boolean };
      if (reading.done) {
        // The recording ended. Close, or EventSource reconnects to be told so again.
        source.close();
        return;
      }
      run.current = silentRun(run.current, reading);
      history.current.push(reading);
      if (history.current.length > HISTORY) history.current.splice(0, history.current.length - HISTORY);
      // Re-rendered only when the whole number of seconds changes, not 20 times a second.
      setSilent(silentSeconds(run.current));
      draw();
    };

    const resize = new ResizeObserver(draw);
    resize.observe(node);
    return () => {
      source.close();
      resize.disconnect();
    };
  }, []);

  const quiet = silent > 0 && !paused;
  return (
    // Time runs left to right whatever the interface language, as it does on any timeline.
    <div
      dir="ltr"
      className="relative h-14 w-full"
      data-testid="recording-waveform"
      data-silent={quiet}
    >
      <canvas ref={canvas} className="block size-full" role="img" aria-label={t("recording.waveform")} />
      {quiet && (
        <span
          data-testid="recording-silent"
          className="absolute inset-0 m-auto flex h-control-sm w-fit items-center gap-1.5 rounded-full bg-raised px-2.5 text-xs font-medium text-secondary shadow-[var(--shadow-ring),var(--shadow-sm)]"
        >
          <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current" strokeWidth={1.5} aria-hidden="true">
            <path d="M6 2.5h4v6.5a2 2 0 0 1-4 0zM3.5 8a4.5 4.5 0 0 0 9 0M8 12.5V14M2.5 2.5l11 11" strokeLinecap="round" />
          </svg>
          <bdi dir="auto">{t("recording.silent").replace("{seconds}", String(silent))}</bdi>
        </span>
      )}
    </div>
  );
}
