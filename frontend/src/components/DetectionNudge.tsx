import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Prompt } from "../api";
import { useI18n } from "../i18n";
import { appName, explainOffer } from "../lib/detection";
import { openMeetingInfo } from "./MeetingInfoDialog";
import Banner from "./Banner";
import Button, { buttonClass } from "./Button";

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
    mutationFn: (picked?: { account_id?: string | null; calendar_id: string; event_id: string }) =>
      api.startRecording(
        picked
          ? {
              account_id: picked.account_id ?? undefined,
              calendar_id: picked.calendar_id,
              event_id: picked.event_id,
            }
          : prompt?.calendar_id && prompt.event_id
            ? {
                account_id: prompt.account_id ?? undefined,
                calendar_id: prompt.calendar_id,
                event_id: prompt.event_id,
              }
            : undefined,
      ),
    onSuccess: (started) => {
      void queryClient.invalidateQueries();
      // A calendar event brings its own details; otherwise ask, alongside the recording.
      if (!(prompt?.calendar_id && prompt.event_id) && !prompt?.candidates?.length)
        openMeetingInfo(started.meeting_id);
    },
  });
  const dismiss = useMutation({
    mutationFn: api.dismissPrompt,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["status"] }),
  });

  if (!prompt) return null;
  const who = appName(prompt.process);
  // What it saw and why it thinks so, in one line: the app, the score and the evidence.
  // Only a detection has any; a calendar meeting's start is the clock's. The line names
  // the app, so the sentence before it does not.
  const why = explainOffer(prompt, t);
  // Meetings booked at the same time that nothing told apart (D89): pick which.
  const choices = prompt.candidates ?? [];
  const text = choices.length
    ? t("detector.nudgeWhich")
    : prompt.title
    ? (prompt.kind === "calendar" ? t("detector.nudgeStarting") : t("detector.nudgeNamed")).replace(
        "{title}",
        prompt.title,
      )
    : `${t("detector.nudge")}${who && !why ? ` — ${who}` : ""}`;

  return (
    <Banner
      data-testid="detection-nudge"
      role="status"
      tone="attention"
      actions={
        <>
          {choices.length ? (
            choices.map((choice) => (
              <Button
                key={`${choice.calendar_id}:${choice.event_id}`}
                data-testid="detection-nudge-choice"
                variant="record"
                busy={start.isPending}
                onClick={() => start.mutate(choice)}
              >
                {t("detector.nudgeRecord").replace("{title}", choice.title || t("calendar.untitled"))}
              </Button>
            ))
          ) : (
            <Button
              data-testid="detection-nudge-start"
              variant="record"
              busy={start.isPending}
              onClick={() => start.mutate(undefined)}
            >
              {t("timeline.start")}
            </Button>
          )}
          {prompt.conference_url && (
            <a
              data-testid="detection-nudge-join"
              href={prompt.conference_url}
              target="_blank"
              rel="noreferrer"
              className={buttonClass("secondary", "sm")}
            >
              {t("detector.nudgeJoin")}
            </a>
          )}
          <Button data-testid="detection-nudge-dismiss" variant="ghost" onClick={() => dismiss.mutate()}>
            {t("detector.nudgeDismiss")}
          </Button>
        </>
      }
    >
      <span className="flex min-w-0 flex-1 basis-64 flex-wrap items-baseline gap-x-3">
        <span className="font-medium text-warning" data-testid="detection-nudge-text">
          {text}
        </span>
        {/*
         * The line is read as it stands, with what it is said first for a screen reader
         * only: an aria-label on a plain span is ignored, so it was never heard.
         */}
        {why && <span className="sr-only">{`${t("detector.why")}: `}</span>}
        {why && (
          <span
            data-testid="detection-nudge-why"
            title={why}
            className="min-w-0 max-w-full truncate text-xs text-secondary"
          >
            {why}
          </span>
        )}
      </span>
    </Banner>
  );
}
