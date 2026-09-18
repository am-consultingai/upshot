import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import type { Meeting } from "../api";
import { formatDuration, formatClock, formatElapsed } from "../lib/format";
import { useI18n } from "../i18n";
import StateBadge from "./StateBadge";
import BusyButton from "./BusyButton";

const ETA_PER_STATE: Record<string, number> = {
  TRANSCRIBING: 8 * 60,
  SUMMARIZING: 90,
  SUMMARIZED: 20,
  RECORDED: 10 * 60,
};

export function etaSeconds(meeting: Meeting): number | null {
  const base = ETA_PER_STATE[meeting.state];
  if (base === undefined) return null;
  const factor = meeting.state === "TRANSCRIBING" ? (meeting.duration_s ?? 600) / 600 : 1;
  return Math.round(base * factor);
}

export default function MeetingCard({
  meeting,
  onStop,
  stopping = false,
  onDelete,
  deleting = false,
}: {
  meeting: Meeting;
  onStop?: () => void;
  /** Stopping flushes the last segment and files the meeting, which is not instant. */
  stopping?: boolean;
  onDelete?: () => void;
  deleting?: boolean;
}) {
  const { t } = useI18n();
  const [now, setNow] = useState(() => Date.now());
  const recording = meeting.state === "RECORDING";

  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [recording]);

  const eta = etaSeconds(meeting);

  return (
    <article
      data-testid="meeting-card"
      data-meeting-id={meeting.id}
      data-state={meeting.state}
      className={`rounded-lg border p-3 ${
        recording ? "border-danger bg-danger-quiet" : "border-line-subtle bg-raised"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <Link to={`/m/${meeting.id}`} data-testid="meeting-link" className="font-medium">
          {meeting.title ?? meeting.id}
        </Link>
        <StateBadge state={meeting.state} />
      </div>
      <p className="mt-1 text-sm text-secondary">
        <span data-testid="meeting-clock">{formatClock(meeting.started_at)}</span>
        {" · "}
        <span data-testid="meeting-duration">{formatDuration(meeting.duration_s, t)}</span>
      </p>
      {recording && (
        <p className="mt-2 flex items-center gap-3 text-sm">
          <span data-testid="elapsed">
            {t("timeline.elapsed")} {formatElapsed(meeting.started_at, now)}
          </span>
          <BusyButton
            data-testid="stop-recording"
            busy={stopping}
            onClick={onStop}
            className="rounded bg-danger px-2 py-1 text-on-solid"
          >
            {t("timeline.stop")}
          </BusyButton>
        </p>
      )}
      {eta !== null && (
        <p className="mt-2 text-sm text-secondary" data-testid="eta">
          {t("timeline.eta")}: {formatDuration(eta, t)}
        </p>
      )}
      {onDelete && !recording && (
        <BusyButton
          data-testid="delete-meeting"
          busy={deleting}
          // Confirmed here rather than in a dialog component: it removes audio from disk
          // and there is no undo.
          onClick={() => {
            if (window.confirm(t("meeting.deleteConfirm"))) onDelete();
          }}
          className="mt-2 text-sm text-danger underline"
        >
          {t("meeting.delete")}
        </BusyButton>
      )}
    </article>
  );
}
