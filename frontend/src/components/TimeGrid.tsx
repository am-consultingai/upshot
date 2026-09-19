import { Link } from "react-router-dom";
import type { CalendarEvent, Meeting } from "../api";
import { useI18n } from "../i18n";
import {
  allDayKeys,
  asGridItem,
  bucketByDay,
  dayKey,
  eventKey,
  isToday,
  placement,
  timedEvents,
} from "../lib/calendar";
import { formatClock } from "../lib/format";
import { timelineLayout } from "../lib/timeline";

/** Tall enough that a 30-minute meeting is legible, short enough that a day fits a screen. */
const PX_PER_MINUTE = 0.9;
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

function tone(state: string): string {
  if (state === "RECORDING") return "bg-warning-quiet border-warning";
  if (state === "FAILED") return "bg-danger-quiet border-danger";
  return "bg-success-quiet border-success";
}

/**
 * The day and week views: hour rows, one column per day, each meeting a block at its
 * real start time with its real duration. Overlapping meetings are packed into columns
 * by `timelineLayout`, the same function the list view uses.
 */
export default function TimeGrid({
  days,
  meetings,
  events = [],
  onEvent,
}: {
  days: Date[];
  meetings: Meeting[];
  /** Google Calendar events behind the recordings. A matched pair draws as one block. */
  events?: CalendarEvent[];
  onEvent?: (event: CalendarEvent) => void;
}) {
  const { t, locale } = useI18n();
  const buckets = bucketByDay(meetings);
  const shown = timedEvents(events, new Set(meetings.map((meeting) => meeting.id)));
  const eventBuckets = bucketByDay(shown.map((event) => ({ ...event, started_at: event.start })));
  const allDay = new Map<string, CalendarEvent[]>();
  for (const event of events.filter((item) => item.all_day)) {
    for (const key of allDayKeys(event)) allDay.set(key, [...(allDay.get(key) ?? []), event]);
  }
  const dayFormat = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric" });

  return (
    <div data-testid="calendar-timegrid" className="overflow-auto">
      <div
        className="grid"
        style={{ gridTemplateColumns: `4rem repeat(${days.length}, minmax(6rem, 1fr))` }}
      >
        <div />
        {days.map((day) => (
          <div
            key={dayKey(day)}
            data-testid="calendar-daycolumn"
            data-day={dayKey(day)}
            className={`border-b border-line-subtle pb-1 text-center text-sm ${
              isToday(day) ? "font-semibold text-accent" : "text-secondary"
            }`}
          >
            {dayFormat.format(day)}
            {(allDay.get(dayKey(day)) ?? []).map((event) => (
              <button
                key={eventKey(event)}
                type="button"
                data-testid="calendar-allday"
                onClick={() => onEvent?.(event)}
                className="mx-1 mt-0.5 block w-[calc(100%-0.5rem)] truncate rounded border border-line px-1 text-start text-xs text-secondary"
              >
                {event.title ?? t("calendar.untitled")}
              </button>
            ))}
          </div>
        ))}

        <div className="relative" style={{ height: 24 * 60 * PX_PER_MINUTE }}>
          {HOURS.map((hour) => (
            <div
              key={hour}
              className="absolute text-xs text-tertiary"
              style={{ top: hour * 60 * PX_PER_MINUTE, insetInlineEnd: "0.5rem" }}
            >
              {String(hour).padStart(2, "0")}:00
            </div>
          ))}
        </div>

        {days.map((day) => {
          const items = buckets.get(dayKey(day)) ?? [];
          const dayEvents = eventBuckets.get(dayKey(day)) ?? [];
          // One layout for both, so an event and an unrelated recording at the same hour
          // sit side by side instead of on top of each other.
          const placed = timelineLayout([...items, ...dayEvents.map(asGridItem)]);
          return (
            <div
              key={dayKey(day)}
              className="relative border-s border-line-subtle"
              style={{ height: 24 * 60 * PX_PER_MINUTE }}
            >
              {HOURS.map((hour) => (
                <div
                  key={hour}
                  className="absolute w-full border-t border-line-subtle"
                  style={{ top: hour * 60 * PX_PER_MINUTE }}
                />
              ))}
              {placed.map((slot) => {
                const event = dayEvents.find((item) => eventKey(item) === slot.id);
                if (event) {
                  const { startMinute, minutes } = placement(
                    { started_at: event.start, duration_s: asGridItem(event).duration_s },
                    day,
                  );
                  const past = new Date(event.end).getTime() < Date.now();
                  return (
                    <button
                      key={slot.id}
                      type="button"
                      data-testid="calendar-gevent"
                      data-event={event.event_id}
                      data-past={past}
                      title={event.title ?? ""}
                      onClick={() => onEvent?.(event)}
                      // An event is not a recording: outlined and quiet, so the recordings
                      // stay what the eye lands on. A past one with nothing recorded is
                      // quieter still — a meeting that happened without Upshot.
                      className={`absolute overflow-hidden rounded border border-dashed border-line bg-canvas px-1 text-start text-xs text-secondary hover:border-accent ${
                        past ? "opacity-60" : ""
                      }`}
                      style={{
                        top: startMinute * PX_PER_MINUTE,
                        height: Math.max(14, minutes * PX_PER_MINUTE),
                        insetInlineStart: `${(slot.column / slot.columns) * 100}%`,
                        width: `${(1 / slot.columns) * 100}%`,
                      }}
                    >
                      <span className="block truncate">{formatClock(event.start)}</span>
                      <span className="block truncate">{event.title ?? t("calendar.untitled")}</span>
                    </button>
                  );
                }
                const meeting = items.find((item) => item.id === slot.id);
                if (!meeting) return null;
                const { startMinute, minutes } = placement(meeting, day);
                return (
                  <Link
                    key={meeting.id}
                    to={`/m/${meeting.id}`}
                    data-testid="calendar-event"
                    data-meeting={meeting.id}
                    title={meeting.title ?? meeting.id}
                    className={`absolute overflow-hidden rounded border-s-4 px-1 text-xs ${tone(meeting.state)}`}
                    style={{
                      top: startMinute * PX_PER_MINUTE,
                      height: Math.max(14, minutes * PX_PER_MINUTE),
                      // Logical properties: the columns mirror under RTL for free.
                      insetInlineStart: `${(slot.column / slot.columns) * 100}%`,
                      width: `${(1 / slot.columns) * 100}%`,
                    }}
                  >
                    <span className="block truncate">{formatClock(meeting.started_at)}</span>
                    <span className="block truncate">
                      {meeting.title ?? t("timeline.recording")}
                    </span>
                  </Link>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
