import { useEffect, useRef } from "react";
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
  recordedIds,
  timedEvents,
} from "../lib/calendar";
import { formatClock } from "../lib/format";
import { timelineLayout } from "../lib/timeline";

/** Tall enough that a 30-minute meeting is legible, short enough that a day fits a screen. */
const PX_PER_MINUTE = 0.9;
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

/**
 * Where the grid opens when the period has nothing in it.
 *
 * The week used to open at 00:00 every time, so the entire working day was below the
 * fold and the first act on every visit was scrolling past nine empty hours. A
 * calendar that does not open on your day is not a calendar you use twice.
 */
const DEFAULT_HOUR = 8;
/** A little air above the first meeting, so it does not sit flush against the header. */
const LEAD_MINUTES = 30;

/** Minutes past midnight of the earliest thing drawn, across every day shown. */
export function firstMinute(
  starts: string[],
  fallbackHour: number = DEFAULT_HOUR,
): number {
  let earliest: number | null = null;
  for (const start of starts) {
    const at = new Date(start);
    if (Number.isNaN(at.getTime())) continue;
    const minute = at.getHours() * 60 + at.getMinutes();
    if (earliest === null || minute < earliest) earliest = minute;
  }
  if (earliest === null) return fallbackHour * 60;
  return Math.max(0, earliest - LEAD_MINUTES);
}

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
  // The event keeps its name and its slot; a recording of it only marks the block. A
  // recording with no event of its own still draws as itself.
  const recorded = recordedIds(events);
  const buckets = bucketByDay(meetings.filter((meeting) => !recorded.has(meeting.id)));
  const shown = timedEvents(events);
  const eventBuckets = bucketByDay(shown.map((event) => ({ ...event, started_at: event.start })));
  const allDay = new Map<string, CalendarEvent[]>();
  for (const event of events.filter((item) => item.all_day)) {
    for (const key of allDayKeys(event)) allDay.set(key, [...(allDay.get(key) ?? []), event]);
  }
  const dayFormat = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric" });

  /*
   * Open on the day, not on midnight.
   *
   * Scrolled rather than clipped: the small hours still exist and can be scrolled
   * back to, which matters for a meeting that genuinely ran at 07:00 or 22:00. The
   * key is the set of days, so moving to another week re-aims at that week's first
   * meeting instead of keeping the previous one's scroll.
   */
  const scroller = useRef<HTMLDivElement | null>(null);
  const spanKey = days.map(dayKey).join(",");
  const openAt = firstMinute([
    ...meetings.map((meeting) => meeting.started_at),
    ...shown.map((event) => event.start),
  ]);
  /*
   * Aimed once per period, and only once the period has something in it.
   *
   * Both the meetings and the events arrive asynchronously, so the first render of a
   * week is always empty — computing the scroll position then pinned it to the 08:00
   * fallback and never corrected itself, which put a 09:30 meeting back below the
   * fold on a screen built to stop exactly that. Waiting for the first non-empty
   * render fixes it; keying the guard on the period rather than on the position means
   * scrolling by hand afterwards is not yanked back, and moving to another week aims
   * again.
   */
  const aimedAt = useRef<string | null>(null);
  const empty = meetings.length === 0 && shown.length === 0;
  useEffect(() => {
    const node = scroller.current;
    if (!node || aimedAt.current === spanKey) return;
    // An empty week still opens on the working day, but does not count as aimed: the
    // data may yet arrive.
    node.scrollTop = openAt * PX_PER_MINUTE;
    if (!empty) aimedAt.current = spanKey;
  }, [spanKey, openAt, empty]);

  return (
    <div
      ref={scroller}
      data-testid="calendar-timegrid"
      data-open-minute={openAt}
      className="max-h-[calc(100vh-11rem)] overflow-auto"
    >
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
                  const wasRecorded = Boolean(event.meeting_id);
                  const body = (
                    <>
                      <span className="block truncate">{formatClock(event.start)}</span>
                      <span className="block truncate">
                        {event.title ?? t("calendar.untitled")}
                      </span>
                      {wasRecorded && (
                        // A folded corner, drawn in the recording colour: the slot is the
                        // calendar's, the mark says Upshot has the meeting itself.
                        <span
                          data-testid="calendar-recorded-flag"
                          aria-hidden="true"
                          className="absolute top-0 size-0 border-4 border-success border-b-transparent"
                          style={{ insetInlineEnd: 0, borderInlineStartColor: "transparent" }}
                        />
                      )}
                    </>
                  );
                  const shape = {
                    top: startMinute * PX_PER_MINUTE,
                    height: Math.max(14, minutes * PX_PER_MINUTE),
                    insetInlineStart: `${(slot.column / slot.columns) * 100}%`,
                    width: `${(1 / slot.columns) * 100}%`,
                  };
                  if (wasRecorded) {
                    return (
                      <Link
                        key={slot.id}
                        to={`/m/${event.meeting_id}`}
                        data-testid="calendar-gevent"
                        data-event={event.event_id}
                        data-recorded="true"
                        data-meeting={event.meeting_id}
                        title={`${event.title ?? ""} — ${t("calendar.recorded")}`}
                        className="absolute overflow-hidden rounded border border-success bg-success-quiet px-1 text-xs"
                        style={shape}
                      >
                        {body}
                      </Link>
                    );
                  }
                  return (
                    <button
                      key={slot.id}
                      type="button"
                      data-testid="calendar-gevent"
                      data-event={event.event_id}
                      data-recorded="false"
                      data-past={past}
                      title={event.title ?? ""}
                      onClick={() => onEvent?.(event)}
                      // An event is not a recording: outlined and quiet, so the recordings
                      // stay what the eye lands on. A past one with nothing recorded is
                      // quieter still — a meeting that happened without Upshot.
                      className={`absolute overflow-hidden rounded border border-dashed border-line bg-canvas px-1 text-start text-xs text-secondary hover:border-accent ${
                        past ? "opacity-60" : ""
                      }`}
                      style={shape}
                    >
                      {body}
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
