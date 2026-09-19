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

/**
 * One meeting, as a row rather than a card.
 *
 * It was a bordered box with its own background, three stacked lines and a
 * permanently visible red "Delete" link — which made four meetings fill the
 * screen and gave deleting the same visual weight as opening. A list of meetings
 * is a list, so this reads as one: a hairline between rows, the title carrying
 * the emphasis, everything else stepped down, and the destructive action kept
 * quiet until the row is under the pointer.
 *
 * The exception is a recording in progress, which keeps a filled background and a
 * live pulse. That one is not an item in a list, it is something happening now.
 */
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
      className={`group relative -mx-3 rounded-lg px-3 transition-colors ${
        recording
          ? "border border-danger/40 bg-danger-quiet py-3"
          : "py-2.5 hover:bg-surface-1"
      }`}
    >
      <div className="flex items-baseline gap-3">
        <Link
          to={`/m/${meeting.id}`}
          data-testid="meeting-link"
          className="min-w-0 flex-1 truncate font-medium text-primary hover:text-accent"
        >
          {meeting.title ?? meeting.id}
        </Link>

        {/* Times are tabular so the column lines up down the list. */}
        <span className="shrink-0 font-mono text-xs tabular-nums text-tertiary">
          <span data-testid="meeting-clock">{formatClock(meeting.started_at)}</span>
          <span className="mx-1.5 opacity-50">·</span>
          <span data-testid="meeting-duration">{formatDuration(meeting.duration_s, t)}</span>
        </span>

        <StateBadge state={meeting.state} />

        {/*
         * Reserved space, not a layout shift: the slot is always there and only
         * its contents fade in, so a row does not jump when the pointer crosses
         * it. Visible on keyboard focus too — an action that only exists on hover
         * does not exist for anyone navigating by keyboard.
         */}
        <span className="flex w-6 shrink-0 justify-end">
          {onDelete && !recording && (
            <BusyButton
              data-testid="delete-meeting"
              busy={deleting}
              title={t("meeting.delete")}
              aria-label={t("meeting.delete")}
              // Confirmed here rather than in a dialog component: it removes audio from
              // disk and there is no undo.
              onClick={() => {
                if (window.confirm(t("meeting.deleteConfirm"))) onDelete();
              }}
              className="rounded p-1 text-tertiary opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
            >
              <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 fill-none stroke-current stroke-[1.5]">
                <path d="M3 4.5h10M6.5 4.5V3.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1M4.5 4.5l.6 8a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8" />
              </svg>
            </BusyButton>
          )}
        </span>
      </div>

      {recording && (
        <p className="mt-2 flex items-center gap-3 text-sm">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-danger opacity-60" />
            <span className="relative inline-flex size-2 rounded-full bg-danger" />
          </span>
          <span data-testid="elapsed" className="font-mono tabular-nums text-danger">
            {formatElapsed(meeting.started_at, now)}
          </span>
          <BusyButton
            data-testid="stop-recording"
            busy={stopping}
            onClick={onStop}
            className="ms-auto rounded-md bg-danger px-2.5 py-1 text-xs font-medium text-on-solid"
          >
            {t("timeline.stop")}
          </BusyButton>
        </p>
      )}

      {eta !== null && (
        <p className="mt-1 text-xs text-tertiary" data-testid="eta">
          {t("timeline.eta")}: {formatDuration(eta, t)}
        </p>
      )}
    </article>
  );
}
