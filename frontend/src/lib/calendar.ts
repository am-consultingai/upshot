/**
 * Date arithmetic for the calendar view. Pure functions only — no React, no fetching —
 * so the awkward parts (month boundaries, DST, week start) are covered by fast unit
 * tests rather than by clicking around.
 *
 * Everything works in the viewer's local timezone. Meetings are stored as ISO strings
 * with an offset, so `new Date(iso)` already lands on the right local instant.
 */

/** `list` is the month as an agenda: the same period as `month`, read as rows. */
export type CalendarSpan = "day" | "week" | "month" | "list";

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
  if (span === "list") {
    const first = startOfMonth(anchor);
    const count = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0).getDate();
    return Array.from({ length: count }, (_, index) => addDays(first, index));
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
  // Month and list are the same period, read two ways.
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

/**
 * The ISO-8601 week number of the week that holds `date`.
 *
 * Weeks here start on Sunday, ISO's on Monday, so the Sunday that opens a row would
 * otherwise carry the previous week's number. The row is numbered by its Monday,
 * which is how an Israeli or American calendar that shows ISO weeks labels it.
 */
export function isoWeek(date: Date): number {
  const monday = addDays(startOfWeek(date), 1);
  const thursday = addDays(monday, 3);
  const firstThursday = new Date(thursday.getFullYear(), 0, 4);
  const firstMonday = addDays(firstThursday, -((firstThursday.getDay() + 6) % 7));
  return 1 + Math.round((startOfDay(monday).getTime() - firstMonday.getTime()) / (7 * 86_400_000));
}

/**
 * The period as two parts: the words that matter, and the year in quieter type.
 *
 * "September 2026", "Sep – Oct 2026" for a week across a month boundary, a whole
 * date for a day. Split rather than one string so the bar can set the month bold
 * and the year grey, as the mock does.
 */
export function periodParts(
  span: CalendarSpan,
  anchor: Date,
  locale: string,
): { main: string; year: string } {
  const year = String(anchor.getFullYear());
  if (span === "day") {
    return {
      main: new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "long" }).format(anchor),
      year,
    };
  }
  if (span === "week") {
    const first = startOfWeek(anchor);
    const last = addDays(first, 6);
    if (first.getMonth() !== last.getMonth()) {
      const short = new Intl.DateTimeFormat(locale, { month: "short" });
      return { main: `${short.format(first)} – ${short.format(last)}`, year: String(last.getFullYear()) };
    }
    return { main: new Intl.DateTimeFormat(locale, { month: "long" }).format(first), year: String(first.getFullYear()) };
  }
  return { main: new Intl.DateTimeFormat(locale, { month: "long" }).format(anchor), year };
}

/**
 * The zone the hour gutter is in, as people write it.
 *
 * Intl only knows abbreviations CLDR ships, and for Israel that is none: it prints
 * "GMT+3", where everyone who lives there says IDT. The zones this app is actually
 * used in get their spoken names; anything else falls back to what Intl says.
 */
const SPOKEN_ZONES: Record<string, Record<number, string>> = {
  "Asia/Jerusalem": { 120: "IST", 180: "IDT" },
  "Europe/London": { 0: "GMT", 60: "BST" },
};

export function zoneLabel(at: Date = new Date(), locale = "en"): string {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const spoken = SPOKEN_ZONES[zone]?.[-at.getTimezoneOffset()];
  if (spoken) return spoken;
  return (
    new Intl.DateTimeFormat(locale, { timeZoneName: "short" })
      .formatToParts(at)
      .find((part) => part.type === "timeZoneName")?.value ?? ""
  );
}

export function periodLabel(span: CalendarSpan, anchor: Date, locale: string): string {
  if (span === "day") {
    return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(anchor);
  }
  if (span === "month" || span === "list") {
    return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(anchor);
  }
  const first = startOfWeek(anchor);
  const last = addDays(first, 6);
  const format = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" });
  return `${format.format(first)} – ${format.format(last)}`;
}

/** The fields of a calendar event the grids need. */
export interface GridEvent {
  calendar_id: string;
  event_id: string;
  start: string;
  end: string;
  all_day: boolean;
  meeting_id?: string | null;
}

/** A stable key for an event, usable as a React key and a grid item id. */
export function eventKey(event: { calendar_id: string; event_id: string }): string {
  return `event:${event.calendar_id}:${event.event_id}`;
}

/**
 * The timed events a grid draws.
 *
 * All of them, including the ones that were recorded: a meeting in the calendar keeps its
 * name and its slot whether or not a recording exists. What the recording changes is how
 * the block is marked, not which block is drawn — see `recordedIds` for the other half.
 */
export function timedEvents<T extends GridEvent>(events: T[]): T[] {
  return events.filter((event) => !event.all_day);
}

/**
 * Recordings already represented by an event on the grid.
 *
 * A recording starts when someone pressed record and stops when the call ended, so drawing
 * it as well as its event would put the same meeting on the grid twice, at two different
 * times, under two names. The event wins: it is what was scheduled, and the recording is a
 * fact about it. Recordings with no event of their own still draw as themselves.
 */
export function recordedIds<T extends GridEvent>(events: T[]): Set<string> {
  const ids = new Set<string>();
  for (const event of events) if (event.meeting_id) ids.add(event.meeting_id);
  return ids;
}

/** The local days an all-day event covers. Its end date is exclusive, as Google sends it. */
export function allDayKeys(event: GridEvent): string[] {
  const keys: string[] = [];
  const first = event.start.slice(0, 10);
  const last = event.end.slice(0, 10);
  for (let day = new Date(`${first}T00:00:00`); dayKey(day) < last; day = addDays(day, 1)) {
    keys.push(dayKey(day));
    if (keys.length > 366) break;
  }
  return keys;
}

/** An event as a grid item: an id, a start and a length, like a recording. */
export function asGridItem(event: GridEvent): { id: string; started_at: string; duration_s: number } {
  return {
    id: eventKey(event),
    started_at: event.start,
    duration_s: Math.max(60, (new Date(event.end).getTime() - new Date(event.start).getTime()) / 1000),
  };
}

/** Whether an event is on now, or starts within the next ten minutes. */
export function isHappening(event: GridEvent, now: Date = new Date()): boolean {
  const start = new Date(event.start).getTime();
  const end = new Date(event.end).getTime();
  return !event.all_day && now.getTime() >= start - 10 * 60_000 && now.getTime() < end;
}
