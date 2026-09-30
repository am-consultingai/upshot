import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Prompt } from "../api";
import { useI18n } from "../i18n";
import { openMeetingInfo } from "./MeetingInfoDialog";
import BusyButton from "./BusyButton";

/** The app's own name for a process, which is all anyone wants to read. */
function appName(process: string | null | undefined): string {
  if (!process) return "";
  const file = process.split("\\").pop() ?? process;
  return file.replace(/\.exe$/i, "");
}

/**
 * Says so, wherever you are, when a meeting is on and is *not* being recorded.
 *
 * It shows the server's offer to record (app/prompts.py, D76), the same one the toasts
 * come from, so it cannot disagree with them or with the recorder: it goes the moment
 * anything records, the meeting's time is up, the call's app lets go of the microphone,
 * or someone presses "Not a meeting" (here, and it is not offered again for that meeting).
 */
export default function DetectionNudge({ prompt }: { prompt: Prompt | null }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: () =>
      api.startRecording(
        prompt?.calendar_id && prompt.event_id
          ? { calendar_id: prompt.calendar_id, event_id: prompt.event_id }
          : undefined,
      ),
    onSuccess: (started) => {
      void queryClient.invalidateQueries();
      // A calendar event brings its own details; otherwise ask, alongside the recording.
      if (!(prompt?.calendar_id && prompt.event_id)) openMeetingInfo(started.meeting_id);
    },
  });
  const dismiss = useMutation({
    mutationFn: api.dismissPrompt,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["status"] }),
  });

  if (!prompt) return null;
  const who = appName(prompt.process);
  const text = prompt.title
    ? (prompt.kind === "calendar" ? t("detector.nudgeStarting") : t("detector.nudgeNamed")).replace(
        "{title}",
        prompt.title,
      )
    : `${t("detector.nudge")}${who ? ` — ${who}` : ""}`;

  return (
    <div data-testid="detection-nudge" role="status" className="border-b border-warning bg-warning-quiet">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-2 text-sm">
        <span className="font-medium text-warning" data-testid="detection-nudge-text">
          {text}
        </span>
        <BusyButton
          data-testid="detection-nudge-start"
          busy={start.isPending}
          onClick={() => start.mutate()}
          className="rounded bg-danger px-3 py-1 text-on-solid"
        >
          {t("timeline.start")}
        </BusyButton>
        {prompt.conference_url && (
          <a
            data-testid="detection-nudge-join"
            href={prompt.conference_url}
            target="_blank"
            rel="noreferrer"
            className="text-warning underline"
          >
            {t("detector.nudgeJoin")}
          </a>
        )}
        <button
          type="button"
          data-testid="detection-nudge-dismiss"
          onClick={() => dismiss.mutate()}
          className="text-warning underline"
        >
          {t("detector.nudgeDismiss")}
        </button>
      </div>
    </div>
  );
}
