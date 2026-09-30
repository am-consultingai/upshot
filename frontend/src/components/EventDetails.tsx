import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type CalendarEvent } from "../api";
import { useI18n } from "../i18n";
import { accountsOf, isHappening } from "../lib/calendar";
import { formatClock } from "../lib/format";
import BusyButton from "./BusyButton";
import { AccountDot, useCalendarAccounts } from "./AccountDots";

/**
 * One calendar event, opened from the calendar view: what it is, when, who was invited
 * and where it happens. While it is on — or about to be — it offers to record it, which
 * is the moment the button is worth having.
 */
export default function EventDetails({
  event,
  onClose,
}: {
  event: CalendarEvent;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const record = useMutation({
    mutationFn: api.startRecording,
    onSuccess: () => {
      void queryClient.invalidateQueries();
      onClose();
    },
  });
  const day = new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(new Date(event.start));

  return (
    <div
      data-testid="event-details"
      role="dialog"
      aria-label={event.title ?? t("calendar.untitled")}
      className="fixed inset-0 z-40 flex items-center justify-center bg-scrim p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-raised p-5 shadow-lg"
        onClick={(click) => click.stopPropagation()}
      >
        <h2 className="display mb-1 text-lg" data-testid="event-title">
          {event.title ?? t("calendar.untitled")}
        </h2>
        <EventAccounts event={event} />
        <p className="text-sm text-secondary" data-testid="event-time">
          {day}
          {!event.all_day && (
            <>
              {" · "}
              <bdi>
                {formatClock(event.start)}–{formatClock(event.end)}
              </bdi>
            </>
          )}
        </p>
        {event.response === "declined" && (
          <p className="mt-1 text-xs text-warning">{t("calendar.declined")}</p>
        )}

        {event.attendees.length > 0 && (
          <div className="mt-3">
            <h3 className="text-xs font-medium text-tertiary">{t("calendar.attendees")}</h3>
            <p className="text-sm" data-testid="event-attendees">
              {event.attendees.join(", ")}
              {event.attendees_partial ? ` ${t("calendar.andOthers")}` : ""}
            </p>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {event.meeting_id ? (
            <Link
              to={`/m/${event.meeting_id}`}
              data-testid="event-recording"
              onClick={onClose}
              className="rounded bg-accent px-2.5 py-1 text-sm text-on-accent"
            >
              {t("calendar.openRecording")}
            </Link>
          ) : (
            isHappening(event) && (
              <BusyButton
                data-testid="event-record"
                busy={record.isPending}
                onClick={() => record.mutate()}
                className="rounded bg-danger px-2.5 py-1 text-sm text-on-solid"
              >
                {t("calendar.recordThis")}
              </BusyButton>
            )
          )}
          {event.conference_url && (
            <a
              data-testid="event-link"
              href={event.conference_url}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-line px-2.5 py-1 text-sm"
            >
              {t("calendar.joinLink")}
            </a>
          )}
          <button
            type="button"
            data-testid="event-close"
            onClick={onClose}
            className="ms-auto text-sm text-secondary underline"
          >
            {t("calendar.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Which calendar the event is on, when more than one is connected (D82). */
function EventAccounts({ event }: { event: CalendarEvent }) {
  const accounts = useCalendarAccounts();
  if (accounts.length < 2) return null;
  const on = new Set(accountsOf(event, accounts));
  return (
    <p className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-tertiary" data-testid="event-accounts">
      {accounts
        .filter((account) => on.has(account.id))
        .map((account) => (
          <span key={account.id} className="inline-flex items-center gap-1">
            <AccountDot account={account} />
            <bdi>{account.address}</bdi>
          </span>
        ))}
    </p>
  );
}
