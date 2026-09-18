import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type WaveSurfer from "wavesurfer.js";
import { useI18n } from "../i18n";

export interface AudioPlayerHandle {
  seek: (seconds: number) => void;
}

/**
 * Playback for a finished meeting, and the reason it matters more than it looks.
 *
 * The best-regarded product in this category transcribes and discards, so its own
 * reviewers list "no playback for verification" as the gap. This application keeps
 * the mix on disk, which means a generated claim can be checked against what was
 * actually said — first to the transcript line, then to the audio itself. That is
 * the whole argument for building this rather than leaving a bare <audio> tag.
 *
 * Two deliberate choices:
 *
 * The <audio> element stays the transport. The server sets Accept-Ranges on the
 * WAV, so it streams and seeks without downloading the file, and playback starts
 * immediately whatever the meeting's length.
 *
 * The waveform is opt-in and lazily imported. Drawing it means decoding the whole
 * file: at 16 kHz that is roughly 58 MB an hour, and nobody should pay that on
 * page load to read a summary. Pressed, it attaches to the same element rather
 * than taking over playback — wavesurfer draws and syncs, the audio element plays.
 */
const AudioPlayer = forwardRef<AudioPlayerHandle, {
  src: string;
  onTime?: (seconds: number) => void;
}>(function AudioPlayer({ src, onTime }, ref) {
  const { t } = useI18n();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const waveRef = useRef<WaveSurfer | null>(null);
  const [showWave, setShowWave] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [failed, setFailed] = useState(false);

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
    if (!showWave || waveRef.current || !containerRef.current || !audioRef.current) return;
    let disposed = false;
    setDrawing(true);
    void (async () => {
      try {
        const { default: WaveSurferClass } = await import("wavesurfer.js");
        if (disposed || !containerRef.current || !audioRef.current) return;
        const style = getComputedStyle(document.documentElement);
        const wave = WaveSurferClass.create({
          container: containerRef.current,
          media: audioRef.current,
          height: 56,
          waveColor: style.getPropertyValue("--border-strong").trim() || "#999",
          progressColor: style.getPropertyValue("--accent").trim() || "#0f6f68",
          cursorColor: style.getPropertyValue("--text-primary").trim() || "#111",
          cursorWidth: 1,
          barWidth: 2,
          barGap: 1,
          barRadius: 1,
          normalize: true,
        });
        wave.on("ready", () => setDrawing(false));
        wave.on("error", () => {
          setFailed(true);
          setDrawing(false);
        });
        waveRef.current = wave;
      } catch {
        if (!disposed) {
          setFailed(true);
          setDrawing(false);
        }
      }
    })();
    return () => {
      disposed = true;
    };
  }, [showWave]);

  // Destroyed only on unmount: tearing it down when `showWave` goes false would
  // mean decoding the file again to show it a second time.
  useEffect(
    () => () => {
      waveRef.current?.destroy();
      waveRef.current = null;
    },
    [],
  );

  const handleTime = useCallback(() => {
    const audio = audioRef.current;
    if (audio && onTime) onTime(audio.currentTime);
  }, [onTime]);

  return (
    <div className="mb-3" data-testid="audio-player">
      {/* dir is pinned: a transport is an instrument, and play never mirrors. */}
      <audio
        dir="ltr"
        ref={audioRef}
        data-testid="audio"
        src={src}
        controls
        preload="none"
        className="w-full"
        onTimeUpdate={handleTime}
        onSeeked={handleTime}
      />
      <div className="mt-1 flex items-center gap-3">
        <button
          type="button"
          data-testid="toggle-waveform"
          aria-expanded={showWave}
          onClick={() => setShowWave((open) => !open)}
          className="text-xs text-secondary underline underline-offset-2"
        >
          {showWave ? t("meeting.hideWaveform") : t("meeting.showWaveform")}
        </button>
        {drawing && (
          <span className="text-xs text-tertiary" data-testid="waveform-loading">
            {t("meeting.waveformLoading")}
          </span>
        )}
        {failed && (
          <span className="text-xs text-danger" data-testid="waveform-failed">
            {t("meeting.waveformFailed")}
          </span>
        )}
      </div>
      <div
        dir="ltr"
        ref={containerRef}
        data-testid="waveform"
        className={showWave && !failed ? "mt-2 rounded border border-line-subtle bg-raised p-2" : "hidden"}
      />
    </div>
  );
});

export default AudioPlayer;
