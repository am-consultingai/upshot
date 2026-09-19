import { NavLink } from "react-router-dom";
import { useEffect, useState } from "react";
import type { Meeting } from "../api";
import { formatDuration, formatClock, formatElapsed } from "../lib/format";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
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

/** A meeting that has finished its pipeline and needs nothing from anyone. */
const SETTLED = new Set(["RENDERED", "DELIVERED"]);

/** Colour only where it means something; everything settled is a quiet dot. */
const DOT: Record<string, string> = {
  RECORDING: "bg-danger",
  FAILED: "bg-danger",
  RENDERED: "bg-success",
  DELIVERED: "bg-success",
};

/**
 * One meeting in the list column.
 *
 * Two lines in about 320px: the title, and beneath it the facts you choose by —
 * time, length, and a dot for state. The previous card was a bordered box three
 * lines tall with a permanently visible red "Delete" link, which fit four
 * meetings on a screen and gave deleting the same weight as opening.
 *
 * Selection is a raised surface rather than a tint, because this row is a thing
 * you have picked up, not a thing that is merely highlighted.
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
      className={`group relative rounded-md ${
        recording ? "bg-danger-quiet" : "hover:bg-surface-2"
      }`}
    >
      <NavLink
        to={`/m/${meeting.id}`}
        data-testid="meeting-link"
        className={({ isActive }) =>
          `block rounded-md px-2 py-2 ${isActive && !recording ? "bg-raised shadow-md" : ""}`
        }
      >
        <div
          data-testid="meeting-name"
          className={`truncate text-sm font-medium tracking-tight ${
            recording ? "text-danger" : "text-primary"
          }`}
        >
          {meeting.title ?? meeting.id}
        </div>

        <div
          className={`mt-0.5 flex items-center gap-1.5 text-xs ${
            recording ? "text-danger" : "text-tertiary"
          }`}
        >
          <span
            data-testid="meeting-state-dot"
            data-state={meeting.state}
            className={`size-1.5 shrink-0 rounded-full ${DOT[meeting.state] ?? "bg-border-strong"}`}
          />
          {recording ? (
            <span data-testid="elapsed" className="tabular-nums">
              {t("state.RECORDING")} · {formatElapsed(meeting.started_at, now)}
            </span>
          ) : (
            <span className="truncate tabular-nums">
              <span data-testid="meeting-clock">{formatClock(meeting.started_at)}</span>
              <span className="mx-1 opacity-50">·</span>
              <span data-testid="meeting-duration">{formatDuration(meeting.duration_s, t)}</span>
              {/*
                * A meeting still working says so in words. The dot alone separates
                * finished from failed by colour, but not "transcribing" from "done" —
                * and the one you want to know about is the one still moving.
                */}
              {!SETTLED.has(meeting.state) && (
                <>
                  <span className="mx-1 opacity-50">·</span>
                  <span data-testid="meeting-state">{t(`state.${meeting.state}` as MessageKey)}</span>
                </>
              )}
              {eta !== null && (
                <>
                  <span className="mx-1 opacity-50">·</span>
                  <span data-testid="eta">{formatDuration(eta, t)}</span>
                </>
              )}
            </span>
          )}
        </div>
      </NavLink>

      {/* Stop is reachable without opening the meeting; it is the only urgent action. */}
      {recording && onStop && (
        <BusyButton
          data-testid="stop-recording"
          busy={stopping}
          onClick={onStop}
          className="absolute end-2 top-2 rounded-md bg-danger px-2 py-0.5 text-2xs font-medium text-on-solid"
        >
          {t("timeline.stop")}
        </BusyButton>
      )}

      {/*
       * Hidden until the row is hovered or focused. A destructive action that is
       * always visible competes with the title for attention every time you scan
       * the list, and it is the one action here that cannot be undone.
       */}
      {onDelete && !recording && (
        <BusyButton
          data-testid="delete-meeting"
          busy={deleting}
          title={t("meeting.delete")}
          aria-label={t("meeting.delete")}
          onClick={() => {
            if (window.confirm(t("meeting.deleteConfirm"))) onDelete();
          }}
          className="absolute end-1.5 top-1.5 rounded p-1 text-tertiary opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3.5 fill-none stroke-current stroke-[1.5]">
            <path d="M3 4.5h10M6.5 4.5V3.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1M4.5 4.5l.6 8a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8" />
          </svg>
        </BusyButton>
      )}
    </article>
  );
}
