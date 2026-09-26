import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { toast } from "../components/Toaster";
import { useI18n } from "../i18n";

/**
 * Start and stop, for every control that offers them: the sidebar button, the
 * recording bar, the rail, the palette and Ctrl+R.
 *
 * One definition because the copies had drifted into the worst failure a recorder can
 * have: a Stop that did nothing. Each copy dropped its error on the floor, so a stop the
 * server refused, or a request that never arrived, left the button looking pressed and
 * the meeting still recording, with nothing on screen to say so. A failure now says what
 * happened and how else to stop.
 */
export function useRecordingControls() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const failed = (key: "recording.stopFailed" | "recording.startFailed") => (error: Error) =>
    toast({ title: t(key), sub: error.message, tone: "danger" });
  const start = useMutation({
    mutationFn: () => api.startRecording(),
    onSuccess: () => queryClient.invalidateQueries(),
    onError: failed("recording.startFailed"),
  });
  const stop = useMutation({
    mutationFn: api.stopRecording,
    onSuccess: () => queryClient.invalidateQueries(),
    onError: failed("recording.stopFailed"),
  });
  return { start, stop };
}
