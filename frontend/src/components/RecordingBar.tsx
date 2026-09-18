import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { formatElapsed } from "../lib/format";
import BusyButton from "./BusyButton";
import RecordingWaveform from "./RecordingWaveform";

/**
 * Shown under the header on every page while a meeting is being recorded: a live
 * waveform, the elapsed time and Stop. A red badge on one card of the timeline was the
 * only sign before, and it is not on screen from any other page.
 */
export default function RecordingBar() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const recorder = status.isError ? undefined : status.data?.recorder;
  const meetingId = recorder && (recorder.active || recorder.paused) ? recorder.meeting_id : null;
  const meeting = useQuery({
    queryKey: ["meeting", meetingId],
    queryFn: () => api.meeting(meetingId as string),
    enabled: meetingId !== null,
  });
  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (meetingId === null) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [meetingId]);

  if (meetingId === null || !recorder) return null;
  const paused = recorder.paused;

  return (
    <div data-testid="recording-bar" data-paused={paused} className="border-b border-red-200 bg-red-50">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-2">
        <Link to={`/m/${meetingId}`} className="flex items-center gap-2 font-medium text-red-700">
          <span className="relative flex size-3" aria-hidden="true">
            {!paused && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-red-400 opacity-75" />
            )}
            <span className={`relative inline-flex size-3 rounded-full ${paused ? "bg-neutral-400" : "bg-red-600"}`} />
          </span>
          {paused ? t("recording.paused") : t("timeline.recording")}
        </Link>
        {meeting.data && (
          <span data-testid="recording-bar-elapsed" className="text-sm tabular-nums text-red-800">
            {formatElapsed(meeting.data.started_at, now)}
          </span>
        )}
        <div className="min-w-0 flex-1 basis-64">
          <RecordingWaveform />
        </div>
        <BusyButton
          data-testid="recording-bar-stop"
          busy={stop.isPending}
          onClick={() => stop.mutate()}
          className="rounded bg-red-600 px-3 py-1 text-sm text-white"
        >
          {t("timeline.stop")}
        </BusyButton>
      </div>
    </div>
  );
}
