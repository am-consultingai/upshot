import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MeetingCalendar } from "../api";
import { useI18n } from "../i18n";
import { formatClock } from "../lib/format";

/**
 * Everything this recording knows about the meeting it was: which calendar event it
 * belongs to, when that event ran, who was in it, the link people joined it on, what the
 * organizer wrote, and the files they attached.
 *
 * It used to be two cards stacked on each other — the match, then the invitation — while
 * a third fact, the joining link, was reachable only from a *second block in the calendar
 * view*, drawn beside the meeting's own. One meeting, three places, and a calendar that
 * showed it twice. This is the one card; the calendar draws one block and opens it.
 *
 * Two sources, deliberately. The **snapshot** was taken when the recording was matched
 * and survives the event being edited or deleted. The **invitation** is read from Google
 * while this card is open, so it is current and much richer — and it is the only thing in
 * this application that carries an email address, which it carries no further than here:
 * never to the database, never into a prompt, never into a log line.
 *
 * Matching has three answers and this shows all three honestly: *matched*, *proposed* (a
 * best guess the user confirms) and *none* (nothing fitted; nothing was invented).
 * Whatever the user picks here is final — automatic matching never changes it again.
 */
export default function MeetingCalendarCard({
  meetingId,
  calendar,
  bare = false,
}: {
  meetingId: string;
  calendar: MeetingCalendar | null | undefined;
  /** Inside the details dialog: no frame of its own, the title shown. */
  bare?: boolean;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [picking, setPicking] = useState(false);
  const options = useQuery({
    queryKey: ["meeting-calendar", meetingId],
    queryFn: () => api.meetingCalendar(meetingId),
    enabled: picking,
  });
  const state = calendar?.match?.state ?? "matched";
  const isMatched = Boolean(calendar) && state === "matched";
  // Only asked for once there is an event to ask about; an unmatched recording has no
  // invitation to read, and a request that always answers "unmatched" is a request
  // nobody needed to make.
  const invited = useQuery({
    queryKey: ["meeting-invite", meetingId],
    queryFn: () => api.meetingInvite(meetingId),
    // The event can change between visits, and nothing here is cached on our side.
    staleTime: 30_000,
    retry: false,
    enabled: isMatched,
  });
  const choose = useMutation({
    mutationFn: (body: { calendar_id: string; event_id: string } | { none: true }) =>
      api.chooseMeetingEvent(meetingId, body),
    onSuccess: () => {
      setPicking(false);
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
      void queryClient.invalidateQueries({ queryKey: ["meeting-invite", meetingId] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar-events"] });
    },
  });

  if (!calendar) return null; // no account connected when this was recorded
  const proposed = state === "proposed" ? calendar.candidates?.[0] : undefined;

  const invite = invited.data?.available ? (invited.data.invite ?? null) : null;
  /*
   * Silence is the right answer for the ordinary cases: a manually started recording has
   * no invitation, and this build may have no calendar at all. Neither is a fault and
   * neither is news, and a warning printed on every healthy meeting teaches the reader to
   * skip the line — which is expensive on the day it says something.
   */
  const unreadable =
    invited.data && !invited.data.available && !["unmatched", "no_connection"].includes(
      invited.data.code ?? "",
    )
      ? invited.data.reason
      : null;

  const title = invite?.title ?? calendar.title ?? null;
  const people = invite?.people ?? [];
  const names = calendar.participants ?? [];

  return (
    <div
      data-testid="meeting-calendar"
      data-state={state}
      className={bare ? "text-sm" : "mb-5 rounded-lg px-3 py-2 text-sm shadow-[var(--shadow-ring-subtle)]"}
    >
      {state === "matched" && (
        <>
          {/* The title is the page title; repeating it here is what made this a
              panel. What is left is the attendees and the escape hatch. */}
          <p className={bare ? "text-secondary" : "sr-only"} data-testid="meeting-calendar-title">
            {title ?? t("calendar.untitled")}
          </p>
          {calendar.match?.source === "user" && (
            <p className="text-xs text-tertiary">{t("calendar.chosenByYou")}</p>
          )}

          {/* The date and the attendee count moved to the chip row under the title;
              repeating them here is what made this a panel instead of a strip. */}
          {/*
           * The joining link, on the card rather than on a block of its own in the
           * calendar. A recurring meeting's link is how you get back into the room the
           * moment the meeting is still on, and how you recognise which room it was
           * afterwards.
           */}
          {invite?.html_link && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {invite?.html_link && (
                <a
                  data-testid="invite-open"
                  href={invite.html_link}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-secondary underline"
                >
                  {t("invite.openInCalendar")}
                </a>
              )}
            </div>
          )}

          {/*
           * Who was there. From the invitation when it can be read — with the address
           * beside the name, because "which Dana" is a question a name alone cannot
           * answer and replying to one of them is the next thing anyone does. From the
           * snapshot otherwise, which is names only, because names only is all that was
           * ever stored.
           */}
          {people.length > 0 ? (
            <div className="mt-2">
              <h3 className="text-xs font-medium text-tertiary">{t("calendar.attendees")}</h3>
              <ul data-testid="meeting-calendar-people" className="mt-0.5 space-y-0.5">
                {people.map((person) => (
                  <li
                    key={person.email || person.name}
                    data-testid="meeting-person"
                    data-organizer={person.organizer ? "true" : undefined}
                    className="flex flex-wrap items-baseline gap-x-1.5"
                  >
                    <span className={person.declined ? "text-tertiary line-through" : ""}>
                      {person.name}
                    </span>
                    {person.email && (
                      <bdi data-testid="meeting-person-email" className="text-xs text-tertiary">
                        {person.email}
                      </bdi>
                    )}
                    {person.organizer && (
                      <span className="text-2xs text-tertiary">{t("invite.organizer")}</span>
                    )}
                    {person.self && (
                      <span className="text-2xs text-tertiary">{t("invite.you")}</span>
                    )}
                    {person.optional && (
                      <span className="text-2xs text-tertiary">{t("invite.optional")}</span>
                    )}
                    {person.declined && (
                      <span className="text-2xs text-tertiary">{t("invite.declinedBy")}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            names.length > 0 && (
              <p className="mt-0.5 text-secondary" data-testid="meeting-calendar-people">
                {names.join(", ")}
                {calendar.participants_more
                  ? ` ${t("calendar.andMore").replace("{n}", String(calendar.participants_more))}`
                  : ""}
              </p>
            )
          )}

          {invite?.location && (
            <div className="mt-2">
              <h3 className="text-xs font-medium text-tertiary">{t("invite.where")}</h3>
              <p className="text-secondary" data-testid="invite-location">
                {invite.location}
              </p>
            </div>
          )}

          {invite?.agenda && (
            <div className="mt-2">
              <h3 className="text-xs font-medium text-tertiary">{t("invite.agenda")}</h3>
              {/* The organizer's own text, with its line breaks kept and its links
                  already written out beside their labels. Rendered as text, never as
                  their HTML. */}
              <p
                data-testid="invite-agenda"
                className="mt-0.5 max-w-prose whitespace-pre-wrap text-secondary"
              >
                {invite.agenda}
              </p>
            </div>
          )}

          {(invite?.attachments.length ?? 0) > 0 && (
            <div className="mt-2">
              <h3 className="text-xs font-medium text-tertiary">{t("invite.attachments")}</h3>
              <ul data-testid="invite-attachments" className="mt-0.5 space-y-0.5">
                {(invite?.attachments ?? []).map((file) => (
                  <li key={file.url}>
                    <a
                      href={file.url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline"
                      data-testid="invite-attachment"
                    >
                      {file.title || file.url}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(invite?.links.length ?? 0) > 0 && (
            <div className="mt-2">
              <h3 className="text-xs font-medium text-tertiary">{t("invite.links")}</h3>
              <ul data-testid="invite-links" className="mt-0.5 space-y-0.5">
                {(invite?.links ?? []).map((href) => (
                  <li key={href} className="truncate">
                    <a href={href} target="_blank" rel="noreferrer" className="underline">
                      {href}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {unreadable && (
            <p data-testid="invite-unavailable" className="mt-2 text-xs text-tertiary">
              {t("invite.unavailable")} {unreadable}
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
                className="w-full rounded px-2 py-1 text-start hover:bg-a-200 active:bg-a-300"
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
              className="w-full rounded px-2 py-1 text-start text-secondary hover:bg-a-200 active:bg-a-300"
            >
              {t("calendar.noEvent")}
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}
