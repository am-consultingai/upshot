import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type WaveSurfer from "wavesurfer.js";
import { useI18n } from "../i18n";
import Tooltip from "./Tooltip";

export interface AudioPlayerHandle {
  seek: (seconds: number) => void;
}

/** 00:00:27 — the transcript's own timestamp format, so the two can be matched by eye. */
function clock(seconds: number): string {
  const whole = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(Math.floor(whole / 3600))}:${pad(Math.floor((whole % 3600) / 60))}:${pad(whole % 60)}`;
}

/**
 * The transport, sitting at the foot of the meeting.
 *
 * Nothing in this category ships `<audio controls>` — Descript, Happy Scribe,
 * Otter, Fireflies and Sonix all draw their own — and the browser's default
 * player was the one visibly undesigned thing left on this page.
 *
 * The element survives, as the engine. wavesurfer will make its own
 * `HTMLAudioElement` if you do not hand it one; here it is handed ours, so
 * playback streams by byte range and starts at once whatever the meeting's
 * length, while wavesurfer only draws and syncs. The waveform therefore appears
 * a moment after the audio is already playable, which is the right way round:
 * decoding an hour of 16 kHz audio is about 58 MB of work and nobody should wait
 * for it to press play.
 *
 * Until it resolves, a flat strip holds the space — so the bar does not jump when
 * the real peaks arrive.
 */
/** A stretch of the recording and whose voice it is. */
export interface Band {
  start: number;
  end: number;
  colour: string;
  name: string;
}

/*
 * The waveform is banded by speaker.
 *
 * wavesurfer draws one colour; the bands are drawn under it, a block per turn in the
 * speaker's colour, and the bars go on top in a translucent ink so they read as
 * texture rather than as a second colour scheme. The strip then answers "who was
 * talking here" at a glance, which is what the rail's talk-time bars answer in total
 * — same colours, so the two can be read against each other.
 */
const AudioPlayer = forwardRef<AudioPlayerHandle, {
  src: string;
  onTime?: (seconds: number) => void;
  bands?: Band[];
}>(function AudioPlayer({ src, onTime, bands = [] }, ref) {
  const { t } = useI18n();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const waveBox = useRef<HTMLDivElement | null>(null);
  const waveRef = useRef<WaveSurfer | null>(null);

  const [playing, setPlaying] = useState(false);
  const [at, setAt] = useState(0);
  const [total, setTotal] = useState(0);
  const [drawn, setDrawn] = useState(false);
  const [rate, setRate] = useState(1);
  const bandsRef = useRef(bands);
  bandsRef.current = bands;

  useImperativeHandle(ref, () => ({
    seek(seconds: number) {
      const audio = audioRef.current;
      if (!audio) return;
      audio.currentTime = seconds;
      // Seeking from the transcript means "play this bit", so it plays.
      void audio.play().catch(() => undefined);
    },
  }), []);

  useEffect(() => {
    let disposed = false;
    void (async () => {
      const { default: WaveSurferClass } = await import("wavesurfer.js");
      if (disposed || !waveBox.current || !audioRef.current) return;
      const style = getComputedStyle(document.documentElement);
      const dark = style.colorScheme === "dark" || document.documentElement.dataset.theme === "dark";
      const banded = bandsRef.current.length > 0;
      const wave = WaveSurferClass.create({
        container: waveBox.current,
        media: audioRef.current,
        height: 28,
        waveColor: banded
          ? dark
            ? "rgba(255,255,255,0.28)"
            : "rgba(0,0,0,0.16)"
          : style.getPropertyValue("--border-strong").trim() || "#bbb",
        progressColor: banded
          ? dark
            ? "rgba(255,255,255,0.6)"
            : "rgba(0,0,0,0.42)"
          : style.getPropertyValue("--accent").trim() || "#0f6f68",
        cursorWidth: banded ? 2 : 0,
        cursorColor: style.getPropertyValue("--text-primary").trim() || "#15161a",
        barWidth: 2,
        barGap: 1,
        barRadius: 1,
        normalize: true,
      });
      wave.on("ready", () => setDrawn(true));
      wave.on("error", () => setDrawn(false));
      waveRef.current = wave;
    })();
    return () => {
      disposed = true;
      waveRef.current?.destroy();
      waveRef.current = null;
    };
  }, [src]);

  const tick = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    setAt(audio.currentTime);
    onTime?.(audio.currentTime);
  }, [onTime]);

  const nudge = (by: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + by));
  };

  const cycleRate = () => {
    const next = rate === 1 ? 1.5 : rate === 1.5 ? 2 : 1;
    setRate(next);
    if (audioRef.current) audioRef.current.playbackRate = next;
  };

  return (
    // dir is pinned: a transport is an instrument, and play never mirrors.
    <div
      dir="ltr"
      data-testid="audio-player"
      className="flex items-center gap-3 border-t border-line-subtle bg-canvas/80 px-6 py-2.5 backdrop-blur"
    >
      <audio
        ref={audioRef}
        data-testid="audio"
        src={src}
        preload="metadata"
        className="hidden"
        onTimeUpdate={tick}
        onSeeked={tick}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onLoadedMetadata={(event) => setTotal(event.currentTarget.duration)}
      />

      <Tooltip label={t("meeting.back15")} side="top">
      <button
        type="button"
        data-testid="skip-back"
        onClick={() => nudge(-15)}
        aria-label={t("meeting.back15")}
        className="grid size-7 shrink-0 place-items-center rounded-md text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300"
      >
        <svg viewBox="0 0 16 16" className="size-4 fill-none stroke-current stroke-[1.5]">
          <path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9" />
          <path d="M4 1.5v3.2h3.2" />
        </svg>
      </button>
      </Tooltip>

      <button
        type="button"
        data-testid="play-pause"
        onClick={() => {
          const audio = audioRef.current;
          if (!audio) return;
          if (audio.paused) void audio.play().catch(() => undefined);
          else audio.pause();
        }}
        aria-label={playing ? t("meeting.pause") : t("meeting.play")}
        className="grid size-8 shrink-0 place-items-center rounded-full bg-accent text-on-accent shadow-[var(--shadow-sm),var(--shadow-edge)] hover:brightness-110 active:translate-y-px"
      >
        {playing ? (
          <svg viewBox="0 0 12 12" className="size-3 fill-current"><path d="M2.5 1.5h2.5v9H2.5zM7 1.5h2.5v9H7z" /></svg>
        ) : (
          <svg viewBox="0 0 12 12" className="ms-0.5 size-3 fill-current"><path d="M3 1.5v9l7-4.5z" /></svg>
        )}
      </button>

      <Tooltip label={t("meeting.forward15")} side="top">
      <button
        type="button"
        data-testid="skip-forward"
        onClick={() => nudge(15)}
        aria-label={t("meeting.forward15")}
        className="grid size-7 shrink-0 place-items-center rounded-md text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300"
      >
        <svg viewBox="0 0 16 16" className="size-4 fill-none stroke-current stroke-[1.5]">
          <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" />
          <path d="M12 1.5v3.2H8.8" />
        </svg>
      </button>
      </Tooltip>

      <span data-testid="transport-time" className="shrink-0 font-mono text-xs tabular-nums text-tertiary">
        {clock(at)} / {clock(total)}
      </span>

      <Tooltip label={t("meeting.railSpeakers")} hint={t("help.waveform")} side="top">
      <div className="relative min-w-0 flex-1">
        {bands.length > 0 && total > 0 && (
          <div
            data-testid="waveform-bands"
            aria-hidden="true"
            className="absolute inset-0 overflow-hidden rounded-xs bg-surface-3"
          >
            {bands.map((band, index) => (
              <span
                key={index}
                data-testid="waveform-band"
                title={band.name}
                className="absolute inset-y-0"
                style={{
                  left: `${(band.start / total) * 100}%`,
                  width: `${(Math.max(0, Math.min(band.end, total) - band.start) / total) * 100}%`,
                  background: band.colour,
                  opacity: 0.55,
                }}
              />
            ))}
          </div>
        )}
        <div ref={waveBox} data-testid="waveform" className={`relative ${drawn ? "" : "hidden"}`} />
        {!drawn && (
          <div className="flex h-7 items-center gap-px" aria-hidden="true">
            {Array.from({ length: 64 }, (_, index) => (
              <span key={index} className="h-1 flex-1 rounded-px bg-line" />
            ))}
          </div>
        )}
      </div>
      </Tooltip>

      <button
        type="button"
        data-testid="playback-rate"
        onClick={cycleRate}
        className="shrink-0 rounded-sm bg-surface-2 px-1.5 py-0.5 text-2xs tabular-nums text-secondary"
      >
        {rate.toFixed(1)}×
      </button>
    </div>
  );
});

export default AudioPlayer;
