import { Link } from "react-router-dom";
import type { CalendarEvent, Meeting } from "../api";
import { useI18n } from "../i18n";
import { allDayKeys, dayKey, isToday, recordedIds, timedEvents } from "../lib/calendar";
import { formatClock, formatShortDate } from "../lib/format";
import { chipWhen, kindOf, type ChipKind } from "./TimeGrid";

interface Row {
  key: string;
  start: string;
  title: string;
  kind: ChipKind;
  when: string;
  meetingId?: string | null;
  event?: CalendarEvent;
  items?: number;
}

const DOT: Record<ChipKind, string> = {
  recorded: "bg-accent",
  scheduled: "bg-border-strong",
  live: "bg-danger ma-pulse",
  failed: "bg-danger",
};

/**
 * The List view: the month as an agenda, a day to a group, only the days that have
 * something in them.
 *
 * The grid answers "when am I free"; this answers "what happened in September" —
 * which is the question a library of recordings is usually opened with, and one a
 * week grid makes you page through four times to answer. Same items, same colours
 * and the same second line as the grid's chips, so switching views never changes
 * what a meeting looks like.
 */
export default function AgendaList({
  days,
  meetings,
  events = [],
  onEvent,
}: {
  days: Date[];
  meetings: Meeting[];
  events?: CalendarEvent[];
  onEvent?: (event: CalendarEvent) => void;
}) {
  const { t, locale } = useI18n();
  const words = { recording: t("timeline.recordingShort"), failed: t("timeline.failedShort") };
  const recorded = recordedIds(events);
  const byId = new Map(meetings.map((meeting) => [meeting.id, meeting]));
  const rows = new Map<string, Row[]>();
  const add = (row: Row) => {
    const key = dayKey(new Date(row.start));
    rows.set(key, [...(rows.get(key) ?? []), row]);
  };

  for (const event of timedEvents(events)) {
    const meeting = event.meeting_id ? byId.get(event.meeting_id) : undefined;
    const kind = event.meeting_id ? kindOf(meeting) : "scheduled";
    const minutes = (new Date(event.end).getTime() - new Date(event.start).getTime()) / 60_000;
    add({
      key: `event:${event.calendar_id}:${event.event_id}`,
      start: event.start,
      title: event.title ?? t("calendar.untitled"),
      kind,
      when: chipWhen(kind, event.start, minutes, words),
      meetingId: event.meeting_id,
      event,
      items: meeting?.actions_open,
    });
  }
  for (const meeting of meetings) {
    if (recorded.has(meeting.id)) continue;
    const kind = kindOf(meeting);
    add({
      key: meeting.id,
      start: meeting.started_at,
      title: meeting.title ?? t("timeline.recording"),
      kind,
      when: chipWhen(
        kind,
        meeting.started_at,
        meeting.duration_s && meeting.duration_s >= 60 ? meeting.duration_s / 60 : null,
        words,
      ),
      meetingId: meeting.id,
      items: meeting.actions_open,
    });
  }
  const allDay = new Map<string, CalendarEvent[]>();
  for (const event of events.filter((item) => item.all_day)) {
    for (const key of allDayKeys(event)) allDay.set(key, [...(allDay.get(key) ?? []), event]);
  }

  const shown = days.filter((day) => rows.has(dayKey(day)) || allDay.has(dayKey(day)));

  return (
    <div data-testid="calendar-agenda" className="h-full overflow-y-auto px-6 py-4">
      {shown.length === 0 && (
        <p data-testid="calendar-agenda-empty" className="py-16 text-center text-sm text-tertiary">
          {t("timeline.agendaEmpty")}
        </p>
      )}
      {shown.map((day) => {
        const key = dayKey(day);
        const list = (rows.get(key) ?? []).sort(
          (a, b) => new Date(a.start).getTime() - new Date(b.start).getTime(),
        );
        return (
          <section key={key} data-testid="agenda-day" data-day={key} className="mb-5 max-w-3xl">
            <h2
              className={`mb-1 flex items-center gap-2 border-b border-line-subtle pb-1.5 text-2xs font-medium uppercase tracking-wide ${
                isToday(day) ? "text-accent" : "text-tertiary"
              }`}
            >
              {formatShortDate(day, locale)}
              {isToday(day) && <span className="normal-case tracking-normal">· {t("timeline.today")}</span>}
            </h2>
            {(allDay.get(key) ?? []).map((event) => (
              <button
                key={`${event.calendar_id}:${event.event_id}`}
                type="button"
                onClick={() => onEvent?.(event)}
                className="mb-1 me-1 inline-block rounded-xs bg-warning-quiet px-1.5 py-0.5 text-2xs text-warning"
              >
                {event.title ?? t("calendar.untitled")}
              </button>
            ))}
            <ul>
              {list.map((row) => {
                const body = (
                  <>
                    <span className="w-28 shrink-0 font-mono text-2xs text-tertiary tabular-nums">
                      {row.kind === "scheduled" || row.kind === "recorded" ? row.when : formatClock(row.start)}
                    </span>
                    <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${DOT[row.kind]}`} />
                    <span
                      className={`min-w-0 flex-1 truncate text-sm ${
                        row.kind === "scheduled" ? "text-secondary" : row.kind === "live" ? "font-medium text-danger" : "text-primary"
                      }`}
                    >
                      {row.title}
                    </span>
                    {(row.kind === "live" || row.kind === "failed") && (
                      <span className="shrink-0 font-mono text-2xs text-danger">{row.when.split(" · ").pop()}</span>
                    )}
                    {row.items ? (
                      <span className="shrink-0 font-mono text-2xs text-tertiary tabular-nums">
                        {t("sidebar.items").replace("{n}", String(row.items))}
                      </span>
                    ) : null}
                  </>
                );
                const className =
                  "flex h-9 w-full items-center gap-3 rounded-md px-2 text-start hover:bg-a-200 active:bg-a-300";
                return (
                  <li key={row.key} data-testid="agenda-row" data-kind={row.kind}>
                    {row.meetingId ? (
                      <Link to={`/m/${row.meetingId}`} className={className}>
                        {body}
                      </Link>
                    ) : (
                      <button type="button" onClick={() => row.event && onEvent?.(row.event)} className={className}>
                        {body}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
