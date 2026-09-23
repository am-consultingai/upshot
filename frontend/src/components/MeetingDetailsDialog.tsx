import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { MeetingCalendar } from "../api";
import { useI18n } from "../i18n";
import MeetingCalendarCard from "./MeetingCalendarCard";

/**
 * The invitation behind a recording, and the way to say it is the wrong one.
 *
 * This was a strip on the page itself, between the title and the action items,
 * holding the attendee names and a "Not this event" button — a second copy of the
 * rail's "In the room", on every meeting, for an escape hatch needed on a few. The
 * mock has no strip. What it held now opens from the people chip and from the `⋯`
 * menu: everything the invitation says, and the correction, one click away rather
 * than always in the way.
 */
export default function MeetingDetailsDialog({
  meetingId,
  calendar,
  onClose,
}: {
  meetingId: string;
  calendar: MeetingCalendar | null | undefined;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const restore = document.activeElement;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("[role=menu]")) {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (restore instanceof HTMLElement) restore.focus();
    };
  }, [onClose]);

  return createPortal(
    <div
      data-testid="meeting-details-backdrop"
      className="fixed inset-0 z-[55] flex items-start justify-center bg-scrim-soft px-4 pt-[12vh] backdrop-blur-[4px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="meeting-details-title"
        data-testid="meeting-details"
        className="ma-dialog max-h-[70vh] w-full max-w-[480px] overflow-y-auto rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <div className="mb-2 flex items-center gap-2">
          <h2 id="meeting-details-title" className="text-md font-semibold tracking-snug">
            {t("calendar.meetingDetails")}
          </h2>
          <button
            ref={closeRef}
            type="button"
            data-testid="meeting-details-close"
            aria-label={t("calendar.close")}
            onClick={onClose}
            className="ms-auto grid size-7 place-items-center rounded-md text-tertiary hover:bg-a-200 hover:text-primary"
          >
            <svg viewBox="0 0 16 16" className="size-3.5 fill-none stroke-current stroke-[1.8]">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
        {calendar ? (
          <MeetingCalendarCard meetingId={meetingId} calendar={calendar} bare />
        ) : (
          <p className="text-sm text-secondary">{t("calendar.noMatch")}</p>
        )}
      </div>
    </div>,
    document.body,
  );
}
