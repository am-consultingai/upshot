import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type { AudioLevel } from "../api";

/** Segments in the bar. Enough to read as continuous, few enough to stay legible. */
const SEGMENTS = 24;
/** Below this the source is treated as producing nothing at all, not merely a quiet room. */
const SILENT_AFTER_MS = 2500;
/** Sentinel rather than a translated string: nothing inside the effect may depend on `t`. */
const STREAM_ENDED = "\u0000stream-ended";

/**
 * A live input meter fed by the `/api/audio/level` SSE stream.
 *
 * `rms` drives the lit segments and `peak` a hold marker that falls back on its own, the
 * way a hardware meter behaves — an instantaneous bar alone makes a working microphone
 * look broken between syllables.
 */
export default function MicMeter({
  device,
  track = "me",
  hint,
}: {
  device: number | null;
  /** "me" is the microphone, "them" the system-audio loopback. */
  track?: "me" | "them";
  hint?: string;
}) {
  const { t } = useI18n();
  const [level, setLevel] = useState<AudioLevel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastSignal = useRef<number>(Date.now());
  const [silent, setSilent] = useState(false);

  useEffect(() => {
    let source: EventSource | null = null;
    let watchdog = 0;

    const open = () => {
      if (source) return;
      const params = new URLSearchParams({ track });
      if (device !== null && track === "me") params.set("device", String(device));
      source = new EventSource(`/api/audio/level?${params.toString()}`);
      lastSignal.current = Date.now();
      setError(null);
      setSilent(false);

      source.onmessage = (event) => {
        const reading = JSON.parse(event.data) as AudioLevel;
        if (reading.done) {
          // A deliberate stop from the server. Close, or EventSource reconnects and the
          // microphone is held open forever by a tab nobody is looking at.
          close();
          return;
        }
        if (reading.error) {
          setError(reading.error);
          close();
          return;
        }
        if (reading.peak > 0) lastSignal.current = Date.now();
        setLevel(reading);
      };
      // Fires on a real transport failure and on the normal close after a server-side
      // error, so it must never overwrite a message the server already sent.
      source.onerror = () => setError((current) => current ?? STREAM_ENDED);

      watchdog = window.setInterval(
        () => setSilent(Date.now() - lastSignal.current > SILENT_AFTER_MS),
        500,
      );
    };

    const close = () => {
      window.clearInterval(watchdog);
      watchdog = 0;
      source?.close(); // releases the microphone on the server
      source = null;
    };

    // The meter only makes sense while someone is looking at it. Holding the endpoint
    // open behind a hidden tab keeps the device busy for nothing.
    const onVisibility = () => (document.visibilityState === "visible" ? open() : close());
    document.addEventListener("visibilitychange", onVisibility);
    onVisibility();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      close();
    };
    // `device` only. `t` is a fresh function on every render, so depending on it made
    // this effect tear down and reopen the microphone on every incoming level — the
    // device was reopened several times a second, forever.
  }, [device, track]);

  const rms = level?.rms ?? 0;
  const peak = level?.peak ?? 0;
  // Amplitude is perceptually useless on a linear scale: normal speech sits near 0.05.
  // -60 dBFS..0 maps the usable range across the whole bar.
  const toFraction = (value: number) =>
    value <= 0 ? 0 : Math.min(1, Math.max(0, (20 * Math.log10(value) + 60) / 60));
  const lit = Math.round(toFraction(rms) * SEGMENTS);
  const peakSegment = Math.round(toFraction(peak) * SEGMENTS);

  return (
    <div data-testid={`mic-meter-${track}`}>
      <div className="mb-1 text-sm font-medium">{hint ?? t("settings.micLevel")}</div>
      <div
        className="flex h-6 gap-[2px]"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Number(rms.toFixed(3))}
        aria-label={t("settings.micLevel")}
        data-level={rms.toFixed(3)}
        data-lit={lit}
      >
        {Array.from({ length: SEGMENTS }, (_, index) => {
          const on = index < lit;
          const isPeak = index === peakSegment - 1 && peakSegment > lit;
          // Colour by position, not by whether it is lit, so the scale stays readable.
          const hot = index >= SEGMENTS - 3;
          const warm = index >= SEGMENTS - 7;
          const colour = on || isPeak
            ? hot
              ? "bg-red-500"
              : warm
                ? "bg-amber-400"
                : "bg-emerald-500"
            : "bg-neutral-200";
          return <div key={index} className={`flex-1 rounded-[1px] ${colour}`} />;
        })}
      </div>
      <p className="mt-1 text-sm text-neutral-600" data-testid={`mic-meter-hint-${track}`}>
        {error
          ? error === STREAM_ENDED
            ? t("settings.micStreamEnded")
            : error
          : level?.source === "recorder"
            ? t("settings.micRecording")
            : level?.clipped
              ? t("settings.micClipped")
              : silent
                ? t("settings.micSilent")
                : t("settings.micHint")}
      </p>
    </div>
  );
}
