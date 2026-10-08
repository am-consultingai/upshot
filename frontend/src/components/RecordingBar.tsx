import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { formatElapsed } from "../lib/format";
import { useRecordingControls } from "../lib/recording";
import Banner from "./Banner";
import Button from "./Button";
import RecordingWaveform from "./RecordingWaveform";

/**
 * Shown under the header on every page while a meeting is being recorded: what is being
 * recorded (its title, once known), for how long, a live waveform that says at a glance
 * whether anything is being heard, and Pause and Stop. A red badge on one card of the
 * timeline was the only sign before, and it is not on screen from any other page.
 */
export default function RecordingBar() {
  const { t } = useI18n();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const recorder = status.isError ? undefined : status.data?.recorder;
  const meetingId = recorder && (recorder.active || recorder.paused) ? recorder.meeting_id : null;
  const meeting = useQuery({
    queryKey: ["meeting", meetingId],
    queryFn: () => api.meeting(meetingId as string),
    enabled: meetingId !== null,
  });
  const { stop, pause } = useRecordingControls();
  const queryClient = useQueryClient();
  const keep = useMutation({
    mutationFn: api.keepRecording,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["status"] }),
  });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (meetingId === null) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [meetingId]);

  if (meetingId === null || !recorder) return null;
  const paused = recorder.paused;
  // The call's app let go (D77): say so at once and count down to the save, with the two
  // ways out, instead of a bar that looks exactly like one that noticed nothing. The same
  // Banner either way, so the switch is a change of tone rather than a second entrance.
  const ending = recorder.ending;
  if (ending) {
    const left = Math.max(0, Math.ceil((Date.parse(ending.ends_at) - now) / 1000));
    return (
      <Banner
        data-testid="recording-bar"
        data-ending="true"
        tone="attention"
        actions={
          <>
            <Button
              data-testid="recording-bar-keep"
              variant="secondary"
              busy={keep.isPending}
              onClick={() => keep.mutate()}
            >
              {t("recording.keep")}
            </Button>
            <Button
              data-testid="recording-bar-stop"
              variant="record"
              busy={stop.isPending}
              onClick={() => stop.mutate()}
            >
              {t("recording.stopNow")}
            </Button>
          </>
        }
      >
        <span data-testid="recording-bar-ending" className="font-medium tabular-nums text-warning">
          {t("recording.callEnded").replace("{seconds}", String(left))}
        </span>
      </Banner>
    );
  }

  const title = meeting.data?.title;
  return (
    <Banner
      data-testid="recording-bar"
      data-paused={paused}
      tone="recording"
      actions={
        <>
          <Button
            data-testid="recording-bar-pause"
            busy={pause.isPending}
            onClick={() => pause.mutate()}
          >
            {paused ? t("recording.resume") : t("recording.pause")}
          </Button>
          <Button
            data-testid="recording-bar-stop"
            variant="record"
            busy={stop.isPending}
            onClick={() => stop.mutate()}
          >
            {t("timeline.stop")}
          </Button>
        </>
      }
    >
      <Link
        to={`/m/${meetingId}`}
        className="flex min-w-0 max-w-full shrink items-center gap-2 rounded-sm font-medium text-danger"
      >
        <span className="relative flex size-3 shrink-0" aria-hidden="true">
          {!paused && (
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-danger opacity-75" />
          )}
          <span className={`relative inline-flex size-3 rounded-full ${paused ? "bg-line-strong" : "bg-danger"}`} />
        </span>
        <span className="shrink-0">{paused ? t("recording.paused") : t("timeline.recording")}</span>
        {/* The meeting's title once it has one: what is being recorded, not only that. */}
        {title && (
          <span data-testid="recording-bar-title" dir="auto" className="min-w-0 truncate text-primary">
            {title}
          </span>
        )}
      </Link>
      {meeting.data && (
        <span
          data-testid="recording-bar-elapsed"
          title={t("recording.elapsed")}
          className="shrink-0 text-base font-medium tabular-nums text-danger"
        >
          {formatElapsed(meeting.data.started_at, now)}
        </span>
      )}
      <div className="min-w-0 flex-1 basis-64">
        <RecordingWaveform paused={paused} />
      </div>
    </Banner>
  );
}
