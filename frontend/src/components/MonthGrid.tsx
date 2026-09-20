import { Link } from "react-router-dom";
import type { CalendarEvent, Meeting } from "../api";
import { useI18n } from "../i18n";
import {
  allDayKeys,
  bucketByDay,
  dayKey,
  isSameMonth,
  isToday,
  recordedIds,
  timedEvents,
  weekdayLabels,
} from "../lib/calendar";
import { formatClock } from "../lib/format";

/** Beyond this a cell stops being readable, so the rest collapse into a count. */
const MAX_CHIPS = 3;

/**
 * The month view: a whole month at a glance, each day listing its recordings. This is
 * the view for finding something from weeks ago, which is why it favours density over
 * showing duration.
 */
export default function MonthGrid({
  days,
  anchor,
  meetings,
  events = [],
}: {
  days: Date[];
  anchor: Date;
  meetings: Meeting[];
  /** Calendar events add density — a dot each — while recordings keep the chips. */
  events?: CalendarEvent[];
}) {
  const { t, locale } = useI18n();
  // As in the time grid: an event that was recorded stays the event, and the recording
  // does not draw a second chip beside it.
  const recorded = recordedIds(events);
  const buckets = bucketByDay(meetings.filter((meeting) => !recorded.has(meeting.id)));
  const eventsByDay = bucketByDay(timedEvents(events).map((e) => ({ ...e, started_at: e.start })));
  const eventCount = new Map<string, number>();
  for (const event of timedEvents(events)) {
    const key = dayKey(new Date(event.start));
    eventCount.set(key, (eventCount.get(key) ?? 0) + 1);
  }
  for (const event of events.filter((item) => item.all_day)) {
    for (const key of allDayKeys(event)) eventCount.set(key, (eventCount.get(key) ?? 0) + 1);
  }

  return (
    <div data-testid="calendar-monthgrid">
      <div className="grid grid-cols-7 border-b border-line-subtle pb-1">
        {weekdayLabels(locale).map((label) => (
          <div key={label} className="text-center text-sm text-tertiary">
            {label}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((day) => {
          const items = buckets.get(dayKey(day)) ?? [];
          const outside = !isSameMonth(day, anchor);
          return (
            <div
              key={dayKey(day)}
              data-testid="calendar-daycell"
              data-day={dayKey(day)}
              data-count={items.length}
              className={`min-h-24 border-b border-s border-line-subtle p-1 ${
                outside ? "bg-surface-1 text-tertiary" : ""
              }`}
            >
              <div
                className={`mb-1 text-xs ${
                  isToday(day)
                    ? "inline-block rounded-full bg-accent px-1.5 text-on-accent"
                    : "text-tertiary"
                }`}
              >
                {day.getDate()}
              </div>
              {items.slice(0, MAX_CHIPS).map((meeting) => (
                <Link
                  key={meeting.id}
                  to={`/m/${meeting.id}`}
                  data-testid="calendar-event"
                  data-meeting={meeting.id}
                  title={meeting.title ?? meeting.id}
                  className={`mb-0.5 block truncate rounded border-s-4 px-1 text-xs ${
                    meeting.state === "RECORDING"
                      ? "border-warning bg-warning-quiet"
                      : meeting.state === "FAILED"
                        ? "border-danger bg-danger-quiet"
                        : "border-success bg-success-quiet"
                  }`}
                >
                  {formatClock(meeting.started_at)} {meeting.title ?? t("timeline.recording")}
                </Link>
              ))}
              {(eventsByDay.get(dayKey(day)) ?? [])
                .filter((event) => event.meeting_id)
                .slice(0, MAX_CHIPS)
                .map((event) => (
                  <Link
                    key={`${event.calendar_id}:${event.event_id}`}
                    to={`/m/${event.meeting_id}`}
                    data-testid="calendar-event"
                    data-recorded="true"
                    data-meeting={event.meeting_id}
                    title={`${event.title ?? ""} — ${t("calendar.recorded")}`}
                    className="mb-0.5 block truncate rounded border-s-4 border-success bg-success-quiet px-1 text-xs"
                  >
                    {formatClock(event.start)} {event.title ?? t("calendar.untitled")}
                  </Link>
                ))}
              {(eventCount.get(dayKey(day)) ?? 0) > 0 && (
                <div
                  data-testid="calendar-gevent-dots"
                  data-count={eventCount.get(dayKey(day))}
                  title={t("calendar.eventsThisDay")}
                  className="mb-0.5 flex flex-wrap gap-0.5"
                >
                  {Array.from({ length: Math.min(6, eventCount.get(dayKey(day)) ?? 0) }, (_, i) => (
                    <span key={i} className="size-1.5 rounded-full bg-line" />
                  ))}
                </div>
              )}
              {items.length > MAX_CHIPS && (
                <span data-testid="calendar-more" className="text-xs text-tertiary">
                  +{items.length - MAX_CHIPS}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
