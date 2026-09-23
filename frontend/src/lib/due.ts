/**
 * When an action item is due, as a bucket the inbox can group by.
 *
 * Built on `due_at`, the calendar date the server resolves from what was said — never
 * on `due`, which is the words themselves. The last attempt at this grouped on
 * `Date.parse(due)`, which is NaN for "Thursday", so every item fell into "No date"
 * while still showing a "Thursday" chip. The resolving now happens once, server-side,
 * against the meeting's own date, in both languages.
 *
 * Pure, and `today` is a parameter, so the edges — Saturday night, the first of the
 * month — are unit-tested rather than discovered.
 */
import { addDays, dayKey, startOfDay, startOfWeek } from "./calendar";

export type DueBucket = "overdue" | "week" | "later" | "none";

export interface Dated {
  due_at: string | null;
  done: boolean;
  snoozed_until?: string | null;
}

/** Whole days from `today` to the due date; negative once it has passed. */
export function daysUntil(dueAt: string, today: Date = new Date()): number {
  const due = new Date(`${dueAt}T00:00:00`);
  return Math.round((due.getTime() - startOfDay(today).getTime()) / 86_400_000);
}

export function dueBucket(item: Dated, today: Date = new Date()): DueBucket {
  if (!item.due_at) return "none";
  const days = daysUntil(item.due_at, today);
  if (days < 0) return "overdue";
  // The rest of this week, Sunday to Saturday — the week the calendar draws.
  const weekEnd = dayKey(addDays(startOfWeek(today), 6));
  return item.due_at <= weekEnd ? "week" : "later";
}

/** Snoozed until a day that has not come yet: out of the inbox until then. */
export function isSnoozed(item: Dated, today: Date = new Date()): boolean {
  return Boolean(item.snoozed_until && item.snoozed_until > dayKey(today));
}

/** Open items by bucket, snoozed ones left out: the rail's three counts. */
export function dueCounts(items: Dated[], today: Date = new Date()): Record<DueBucket, number> {
  const counts: Record<DueBucket, number> = { overdue: 0, week: 0, later: 0, none: 0 };
  for (const item of items) {
    if (item.done || isSnoozed(item, today)) continue;
    counts[dueBucket(item, today)] += 1;
  }
  return counts;
}

/**
 * The chip on a row: "2 days late", "Today", "Thu", "24 Sep".
 *
 * A weekday inside the next week, because "Thu" is how a date that close is spoken;
 * a date beyond that, because "Thu" three weeks out is ambiguous.
 */
export function dueLabel(
  dueAt: string,
  locale: string,
  words: { late: (days: number) => string; today: string; tomorrow: string },
  today: Date = new Date(),
): string {
  const days = daysUntil(dueAt, today);
  if (days < 0) return words.late(-days);
  if (days === 0) return words.today;
  if (days === 1) return words.tomorrow;
  const date = new Date(`${dueAt}T00:00:00`);
  if (days < 7) return new Intl.DateTimeFormat(locale, { weekday: "short" }).format(date);
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" }).format(date);
}

/** The next occurrence of a weekday after today (0 = Sunday), for "snooze until Monday". */
export function nextWeekday(weekday: number, today: Date = new Date()): string {
  const base = startOfDay(today);
  const ahead = (weekday - base.getDay() + 7) % 7 || 7;
  return dayKey(addDays(base, ahead));
}
