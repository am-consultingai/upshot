import { useEffect, useRef } from "react";
import { useI18n } from "../i18n";

/** One bar per reading. The server sends 20 a second, so a 900 px lane holds 15 s. */
const BAR_PX = 2;
const GAP_PX = 1;
/** Enough readings to fill a very wide screen; older ones are dropped. */
const HISTORY = 1500;

const COLOUR = { me: "#059669", them: "#2563eb" } as const;
const PAUSED = "#d4d4d4";
const AXIS = "#e5e5e5";

type Track = keyof typeof COLOUR;
interface Reading {
  me: number;
  them: number;
  paused: boolean;
}

/** -60 dBFS..0 across the lane. Speech sits near 0.05 linear, invisible on a linear scale. */
function toFraction(value: number): number {
  return value <= 0 ? 0 : Math.min(1, Math.max(0, (20 * Math.log10(value) + 60) / 60));
}

/**
 * A scrolling two-lane waveform of the meeting being recorded, newest at the right edge,
 * mirrored about each lane's centre the way Audacity draws a track.
 *
 * Fed by `/api/recording/levels`, which only ever reads the recorder: it cannot open a
 * device, so leaving this on screen after Stop can never take the microphone.
 */
export default function RecordingWaveform() {
  const { t } = useI18n();
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const history = useRef<Reading[]>([]);

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

      const step = BAR_PX + GAP_PX;
      const readings = history.current.slice(-Math.floor(width / step));
      const origin = width - readings.length * step;
      const lane = height / 2;
      (["me", "them"] as Track[]).forEach((track, index) => {
        const middle = lane * index + lane / 2;
        context.fillStyle = AXIS;
        context.fillRect(0, middle - 0.5, width, 1);
        readings.forEach((reading, position) => {
          const half = Math.max(0.5, toFraction(reading[track]) * (lane / 2 - 2));
          context.fillStyle = reading.paused ? PAUSED : COLOUR[track];
          context.fillRect(origin + position * step, middle - half, BAR_PX, half * 2);
        });
      });
    };

    const source = new EventSource("/api/recording/levels");
    source.onmessage = (event) => {
      const reading = JSON.parse(event.data) as Reading & { done?: boolean };
      if (reading.done) {
        // The recording ended. Close, or EventSource reconnects to be told so again.
        source.close();
        return;
      }
      history.current.push(reading);
      if (history.current.length > HISTORY) history.current.splice(0, history.current.length - HISTORY);
      draw();
    };

    const resize = new ResizeObserver(draw);
    resize.observe(node);
    return () => {
      source.close();
      resize.disconnect();
    };
  }, []);

  return (
    // Time runs left to right whatever the interface language, as it does on any timeline.
    <div dir="ltr" className="relative h-20 w-full rounded border border-danger bg-raised" data-testid="recording-waveform">
      <canvas ref={canvas} className="block size-full" />
      <span className="pointer-events-none absolute start-1.5 top-0.5 text-[10px] font-medium" style={{ color: COLOUR.me }}>
        {t("recording.me")}
      </span>
      <span className="pointer-events-none absolute start-1.5 top-1/2 text-[10px] font-medium" style={{ color: COLOUR.them }}>
        {t("recording.them")}
      </span>
    </div>
  );
}
