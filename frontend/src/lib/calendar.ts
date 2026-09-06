/**
 * Date arithmetic for the calendar view. Pure functions only — no React, no fetching —
 * so the awkward parts (month boundaries, DST, week start) are covered by fast unit
 * tests rather than by clicking around.
 *
 * Everything works in the viewer's local timezone. Meetings are stored as ISO strings
 * with an offset, so `new Date(iso)` already lands on the right local instant.
 */

export type CalendarSpan = "day" | "week" | "month";

/** Sunday. Correct for both he-IL and en-US, the two locales this app ships. */
const WEEK_START = 0;

export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date: Date, days: number): Date {
  // Via the day-of-month rather than milliseconds: adding 24h across a DST boundary
  // lands on the wrong day.
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

export function startOfWeek(date: Date): Date {
  const day = startOfDay(date);
  return addDays(day, -((day.getDay() - WEEK_START + 7) % 7));
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

/** A local YYYY-MM-DD key. `toISOString` would shift the date in western timezones. */
export function dayKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** The days a span covers. Month is padded to whole weeks, as a calendar grid must be. */
export function daysFor(span: CalendarSpan, anchor: Date): Date[] {
  if (span === "day") return [startOfDay(anchor)];
  if (span === "week") {
    const first = startOfWeek(anchor);
    return Array.from({ length: 7 }, (_, index) => addDays(first, index));
  }
  const first = startOfWeek(startOfMonth(anchor));
  const monthEnd = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  const days: Date[] = [];
  for (let cursor = first; cursor <= monthEnd || days.length % 7 !== 0; cursor = addDays(cursor, 1)) {
    days.push(cursor);
    if (days.length > 42) break; // a month never needs more than six weeks
  }
  return days;
}

/** Inclusive `from`, exclusive `to`, as the meetings endpoint expects. */
export function rangeFor(span: CalendarSpan, anchor: Date): { from: string; to: string } {
  const days = daysFor(span, anchor);
  return { from: dayKey(days[0]), to: dayKey(addDays(days[days.length - 1], 1)) };
}

export function shift(span: CalendarSpan, anchor: Date, delta: number): Date {
  if (span === "day") return addDays(anchor, delta);
  if (span === "week") return addDays(anchor, delta * 7);
  return new Date(anchor.getFullYear(), anchor.getMonth() + delta, 1);
}

export function isToday(date: Date): boolean {
  return dayKey(date) === dayKey(new Date());
}

export function isSameMonth(date: Date, anchor: Date): boolean {
  return date.getMonth() === anchor.getMonth() && date.getFullYear() === anchor.getFullYear();
}

/** Group meetings by the local day they start on. */
export function bucketByDay<T extends { started_at: string }>(items: T[]): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const key = dayKey(new Date(item.started_at));
    const bucket = out.get(key);
    if (bucket) bucket.push(item);
    else out.set(key, [item]);
  }
  for (const bucket of out.values()) {
    bucket.sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
  }
  return out;
}

export const DEFAULT_DURATION_S = 30 * 60;

/** Minutes from local midnight, and the meeting's length, for positioning in a grid. */
export function placement(
  meeting: { started_at: string; duration_s: number | null },
  day: Date,
): { startMinute: number; minutes: number } {
  const start = new Date(meeting.started_at);
  const midnight = startOfDay(day).getTime();
  const startMinute = Math.max(0, (start.getTime() - midnight) / 60000);
  const minutes = Math.max(1, (meeting.duration_s ?? DEFAULT_DURATION_S) / 60);
  // Never run past midnight: a grid cell for one day cannot show tomorrow.
  return { startMinute, minutes: Math.min(minutes, 24 * 60 - startMinute) };
}

/** Locale-correct labels, so Hebrew needs no translation table of its own. */
export function weekdayLabels(locale: string, short = true): string[] {
  const format = new Intl.DateTimeFormat(locale, { weekday: short ? "short" : "long" });
  const sunday = startOfWeek(new Date());
  return Array.from({ length: 7 }, (_, index) => format.format(addDays(sunday, index)));
}

export function periodLabel(span: CalendarSpan, anchor: Date, locale: string): string {
  if (span === "day") {
    return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(anchor);
  }
  if (span === "month") {
    return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(anchor);
  }
  const first = startOfWeek(anchor);
  const last = addDays(first, 6);
  const format = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" });
  return `${format.format(first)} – ${format.format(last)}`;
}
