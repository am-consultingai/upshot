import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import BusyButton from "./BusyButton";

/** What the detector publishes when it decides, while only watching, that this is a meeting. */
export interface Detection {
  process?: string;
  score?: number;
  state?: string;
  /** The calendar meeting on now, when there is one: its name beats the app's. */
  event?: string | null;
}

/** The app's own name for a process, which is all anyone wants to read. */
function appName(process: string | undefined): string {
  if (!process) return "";
  const file = process.split("\\").pop() ?? process;
  return file.replace(/\.exe$/i, "");
}

/**
 * Says so, wherever you are, when a meeting was detected and is *not* being recorded.
 *
 * In detect-and-log mode the detector reaches a verdict and then does nothing visible
 * unless the Detector page happens to be open — which is the one screen you are not
 * looking at during a meeting. The decision is worth a sentence and a button while the
 * meeting is still happening; afterwards it is only a row in a table.
 */
export default function DetectionNudge({
  detection,
  onDismiss,
}: {
  detection: Detection | null;
  onDismiss: () => void;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: api.startRecording,
    onSuccess: () => {
      queryClient.invalidateQueries();
      onDismiss();
    },
  });

  if (!detection) return null;
  const who = appName(detection.process);

  return (
    <div data-testid="detection-nudge" role="status" className="border-b border-warning bg-warning-quiet">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-2 text-sm">
        <span className="font-medium text-warning" data-testid="detection-nudge-text">
          {detection.state === "upcoming"
            ? t("detector.nudgeStarting").replace("{title}", detection.event || t("calendar.untitled"))
            : detection.event
              ? t("detector.nudgeNamed").replace("{title}", detection.event)
              : `${t("detector.nudge")}${who ? ` — ${who}` : ""}${
                  typeof detection.score === "number" ? ` (${detection.score})` : ""
                }`}
        </span>
        <BusyButton
          data-testid="detection-nudge-start"
          busy={start.isPending}
          onClick={() => start.mutate()}
          className="rounded bg-danger px-3 py-1 text-on-solid"
        >
          {t("timeline.start")}
        </BusyButton>
        <button
          type="button"
          data-testid="detection-nudge-dismiss"
          onClick={onDismiss}
          className="text-warning underline"
        >
          {t("detector.nudgeDismiss")}
        </button>
      </div>
    </div>
  );
}
