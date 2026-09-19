import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MeetingCalendar } from "../api";
import { useI18n } from "../i18n";
import { formatClock } from "../lib/format";

/**
 * Which calendar event this recording was, and a way to say otherwise.
 *
 * Matching has three answers, and this shows all three honestly: *matched* (the event,
 * its attendees and link), *proposed* (a best guess the user confirms — two back-to-back
 * meetings, say), and *none* (no event fitted; nothing was invented). Whatever the user
 * picks here is final: automatic matching never changes it again.
 */
export default function MeetingCalendarCard({
  meetingId,
  calendar,
}: {
  meetingId: string;
  calendar: MeetingCalendar | null | undefined;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [picking, setPicking] = useState(false);
  const options = useQuery({
    queryKey: ["meeting-calendar", meetingId],
    queryFn: () => api.meetingCalendar(meetingId),
    enabled: picking,
  });
  const choose = useMutation({
    mutationFn: (body: { calendar_id: string; event_id: string } | { none: true }) =>
      api.chooseMeetingEvent(meetingId, body),
    onSuccess: () => {
      setPicking(false);
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar-events"] });
    },
  });

  if (!calendar) return null; // no account connected when this was recorded
  const state = calendar.match?.state ?? "matched";
  const proposed = state === "proposed" ? calendar.candidates?.[0] : undefined;

  return (
    <div
      data-testid="meeting-calendar"
      data-state={state}
      className="mb-4 rounded-lg bg-raised px-4 py-3 text-sm shadow-sm"
    >
      {state === "matched" && (
        <>
          <p>
            <span className="text-tertiary">{t("calendar.fromCalendar")} </span>
            <span className="font-medium" data-testid="meeting-calendar-title">
              {calendar.title ?? t("calendar.untitled")}
            </span>
            {calendar.match?.source === "user" && (
              <span className="text-tertiary"> · {t("calendar.chosenByYou")}</span>
            )}
          </p>
          {(calendar.participants?.length ?? 0) > 0 && (
            <p className="mt-0.5 text-secondary" data-testid="meeting-calendar-people">
              {calendar.participants?.join(", ")}
              {calendar.participants_more
                ? ` ${t("calendar.andMore").replace("{n}", String(calendar.participants_more))}`
                : ""}
            </p>
          )}
        </>
      )}
      {state === "proposed" && proposed && (
        <p>
          {t("calendar.wasThis")}{" "}
          <span className="font-medium" data-testid="meeting-calendar-proposal">
            {proposed.title ?? t("calendar.untitled")}
          </span>{" "}
          <bdi className="text-tertiary">({formatClock(proposed.start)})</bdi>
          {calendar.match?.reason && (
            <span className="block text-xs text-tertiary">{calendar.match.reason}</span>
          )}
        </p>
      )}
      {state === "none" && (
        <p className="text-secondary" data-testid="meeting-calendar-none">
          {calendar.match?.source === "user" ? t("calendar.markedNone") : t("calendar.noMatch")}
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        {proposed && (
          <button
            type="button"
            data-testid="meeting-calendar-confirm"
            onClick={() =>
              choose.mutate({ calendar_id: proposed.calendar_id, event_id: proposed.event_id })
            }
            className="rounded bg-accent px-2 py-0.5 text-xs text-on-accent"
          >
            {t("calendar.yes")}
          </button>
        )}
        <button
          type="button"
          data-testid="meeting-calendar-pick"
          onClick={() => setPicking((open) => !open)}
          className="rounded border border-line px-2 py-0.5 text-xs"
        >
          {state === "matched" ? t("calendar.notThisOne") : t("calendar.chooseEvent")}
        </button>
      </div>

      {picking && (
        <ul className="mt-2 space-y-1" data-testid="meeting-calendar-options">
          {(options.data?.candidates ?? []).map((event) => (
            <li key={`${event.calendar_id}:${event.event_id}`}>
              <button
                type="button"
                data-testid="meeting-calendar-option"
                onClick={() =>
                  choose.mutate({ calendar_id: event.calendar_id, event_id: event.event_id })
                }
                className="w-full rounded px-2 py-1 text-start hover:bg-surface-2"
              >
                <bdi className="text-tertiary">{formatClock(event.start)}</bdi>{" "}
                {event.title ?? t("calendar.untitled")}
              </button>
            </li>
          ))}
          {options.isSuccess && options.data.candidates.length === 0 && (
            <li className="px-2 text-xs text-tertiary">{t("calendar.nothingNearby")}</li>
          )}
          <li>
            <button
              type="button"
              data-testid="meeting-calendar-none-button"
              onClick={() => choose.mutate({ none: true })}
              className="w-full rounded px-2 py-1 text-start text-secondary hover:bg-surface-2"
            >
              {t("calendar.noEvent")}
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}
