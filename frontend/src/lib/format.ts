import type { MessageKey } from "../locales/en";

export type Translate = (key: MessageKey) => string;

/** 47 min · 3 h 2 min · "less than a minute" — never "0 min". */
export function formatDuration(seconds: number | null | undefined, t: Translate): string {
  const total = Math.max(0, Math.round(seconds ?? 0));
  if (total < 60) return t("common.lessThanAMinute");
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} ${t("common.minutes")}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0
    ? `${hours} ${t("common.hours")}`
    : `${hours} ${t("common.hours")} ${rest} ${t("common.minutes")}`;
}

export function formatClock(iso: string): string {
  const date = new Date(iso);
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

export function formatElapsed(startedAt: string, now: number): string {
  const seconds = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** A detector event's time, as someone would say it: "17:00:31", or "18 Sep, 17:00"
 * once it is no longer today. Month and order come from the locale, not a template. */
export function formatEventTime(iso: string, locale: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return iso;
  const today = new Date();
  const sameDay =
    when.getFullYear() === today.getFullYear() &&
    when.getMonth() === today.getMonth() &&
    when.getDate() === today.getDate();
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    // 24-hour, to match every other time in the app (`formatClock`, the elapsed timer)
    // and the convention where it is used. Plain "en" would render "09:32:15 PM".
    hourCycle: "h23",
    ...(sameDay ? { second: "2-digit" } : { day: "numeric", month: "short" }),
  }).format(when);
}

/**
 * The label on a day group: "Today", "Yesterday", or a written date.
 *
 * The list was headed with raw ISO keys — 2026-09-22 — which is the shape the
 * data happens to be stored in, not the way anyone thinks about when a meeting
 * happened. Anything inside the last week is easier to place by name, and the
 * year is only worth the space once it is no longer the current one.
 */
export function formatDayLabel(
  dayKeyValue: string,
  locale: string,
  t: (key: "timeline.today" | "timeline.yesterday") => string,
  today: Date = new Date(),
): string {
  const [year, month, day] = dayKeyValue.split("-").map(Number);
  if (!year || !month || !day) return dayKeyValue;
  const date = new Date(year, month - 1, day);
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((midnight.getTime() - date.getTime()) / 86_400_000);

  if (days === 0) return t("timeline.today");
  if (days === 1) return t("timeline.yesterday");
  return new Intl.DateTimeFormat(locale, {
    weekday: days < 7 ? "long" : undefined,
    day: "numeric",
    month: "short",
    year: date.getFullYear() === today.getFullYear() ? undefined : "numeric",
  }).format(date);
}
