import { Link } from "react-router-dom";
import type { Meeting } from "../api";
import { useI18n } from "../i18n";
import { bucketByDay, dayKey, isToday, placement } from "../lib/calendar";
import { formatClock } from "../lib/format";
import { timelineLayout } from "../lib/timeline";

/** Tall enough that a 30-minute meeting is legible, short enough that a day fits a screen. */
const PX_PER_MINUTE = 0.9;
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

function tone(state: string): string {
  if (state === "RECORDING") return "bg-amber-100 border-amber-400";
  if (state === "FAILED") return "bg-red-100 border-red-400";
  return "bg-emerald-50 border-emerald-400";
}

/**
 * The day and week views: hour rows, one column per day, each meeting a block at its
 * real start time with its real duration. Overlapping meetings are packed into columns
 * by `timelineLayout`, the same function the list view uses.
 */
export default function TimeGrid({ days, meetings }: { days: Date[]; meetings: Meeting[] }) {
  const { t, locale } = useI18n();
  const buckets = bucketByDay(meetings);
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
            className={`border-b border-neutral-200 pb-1 text-center text-sm ${
              isToday(day) ? "font-semibold text-emerald-700" : "text-neutral-600"
            }`}
          >
            {dayFormat.format(day)}
          </div>
        ))}

        <div className="relative" style={{ height: 24 * 60 * PX_PER_MINUTE }}>
          {HOURS.map((hour) => (
            <div
              key={hour}
              className="absolute text-xs text-neutral-400"
              style={{ top: hour * 60 * PX_PER_MINUTE, insetInlineEnd: "0.5rem" }}
            >
              {String(hour).padStart(2, "0")}:00
            </div>
          ))}
        </div>

        {days.map((day) => {
          const items = buckets.get(dayKey(day)) ?? [];
          const placed = timelineLayout(items);
          return (
            <div
              key={dayKey(day)}
              className="relative border-s border-neutral-200"
              style={{ height: 24 * 60 * PX_PER_MINUTE }}
            >
              {HOURS.map((hour) => (
                <div
                  key={hour}
                  className="absolute w-full border-t border-neutral-100"
                  style={{ top: hour * 60 * PX_PER_MINUTE }}
                />
              ))}
              {placed.map((slot) => {
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
