import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { MeetingCalendar } from "../api";
import { useI18n } from "../i18n";
import MeetingCalendarCard from "./MeetingCalendarCard";
import { X } from "lucide-react";
import { Icon } from "./Icon";

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
  ask = null,
}: {
  meetingId: string;
  calendar: MeetingCalendar | null | undefined;
  onClose: () => void;
  /**
   * Opened from the calendar grid's "Not this one" (D94): the recording is not settled,
   * so the card asks which meeting it was, open at the list to pick from. The heading
   * names the recording, since the dialog is not on its page.
   */
  ask?: { heading: string } | null;
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
      className="fixed inset-0 z-[55] flex items-start justify-center bg-scrim-soft px-4 pt-[12vh] backdrop-blur-xs"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="meeting-details-title"
        data-testid="meeting-details"
        className="ma-dialog max-h-[70vh] w-full max-w-120 overflow-y-auto rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <div className="mb-2 flex items-center gap-2">
          <h2 id="meeting-details-title" className="text-md font-semibold tracking-snug">
            {ask ? ask.heading : t("calendar.meetingDetails")}
          </h2>
          <button
            ref={closeRef}
            type="button"
            data-testid="meeting-details-close"
            aria-label={t("calendar.close")}
            onClick={onClose}
            className="ms-auto grid size-7 place-items-center rounded-md text-tertiary hover:bg-a-200 hover:text-primary"
          >
            <Icon icon={X} className="size-3.5" />
          </button>
        </div>
        {ask ? (
          <MeetingCalendarCard meetingId={meetingId} calendar={calendar} bare needsMeeting startPicking />
        ) : calendar ? (
          <MeetingCalendarCard meetingId={meetingId} calendar={calendar} bare />
        ) : (
          <p className="text-sm text-secondary">{t("calendar.noMatch")}</p>
        )}
      </div>
    </div>,
    document.body,
  );
}
