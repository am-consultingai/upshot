/**
 * Stopping a meeting's transcription, the same from every place that shows one running:
 * the meeting page, the meeting's row in the sidebar, and Settings → Transcription. A
 * waiting one stops at once; a running one is asked first, since the work so far is lost.
 * The meeting and its audio stay, and so does a transcript an earlier run made.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, reason } from "../api";
import { confirmDialog } from "../components/ConfirmDialog";
import { toast } from "../components/Toaster";
import { useI18n } from "../i18n";
import { ACTIVE_KEY } from "./activeJobs";

/** What Stop stops, as the server's ``TRANSCRIPT_STAGES``: the transcribe stage alone. */
export const TRANSCRIPT_STAGES = new Set(["transcribe"]);

export function useStopTranscription(meetingId: string) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => api.stopMeetingTranscription(meetingId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ACTIVE_KEY, exact: true });
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
    },
    onError: (error) => toast({ title: t("stopTranscription.failed"), sub: reason(error), tone: "danger" }),
  });
  /** Ask first when it is running; a waiting one has nothing to lose. */
  const ask = async (title: string, running: boolean) => {
    if (running) {
      const yes = await confirmDialog({
        title: t("stopTranscription.title"),
        body: t("stopTranscription.body").replace("{title}", title),
        confirm: t("stopTranscription.confirm"),
        cancel: t("activeJobs.keepRunning"),
      });
      if (!yes) return;
    }
    mutation.mutate();
  };
  return { ask, pending: mutation.isPending, requested: mutation.isSuccess, reset: mutation.reset };
}
