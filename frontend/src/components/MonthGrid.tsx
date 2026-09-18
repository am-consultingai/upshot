import { Link } from "react-router-dom";
import type { Meeting } from "../api";
import { useI18n } from "../i18n";
import { bucketByDay, dayKey, isSameMonth, isToday, weekdayLabels } from "../lib/calendar";
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
}: {
  days: Date[];
  anchor: Date;
  meetings: Meeting[];
}) {
  const { t, locale } = useI18n();
  const buckets = bucketByDay(meetings);

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
