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
  const format = new Intl.DateTimeFormat(locale, {
    weekday: days < 7 ? "long" : undefined,
    day: "numeric",
    month: "short",
    year: date.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
  /*
   * "Sunday 20 Sep", not "Sunday, Sep 20". English Intl puts the month first and a
   * comma after the weekday, which reads as a sentence; a list header is a label, and
   * day-before-month is how the rest of this app — and everyone using it in Israel —
   * writes a date. Hebrew keeps what Intl gives it, which is already that order.
   */
  if (!locale.startsWith("en")) return format.format(date);
  const parts = Object.fromEntries(format.formatToParts(date).map((part) => [part.type, part.value]));
  return [parts.weekday, parts.day, parts.month, parts.year].filter(Boolean).join(" ");
}

/** "2.1 GB", "340 MB", "12 KB" — the data folder's size, for the sidebar's footer. */
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || bytes <= 0) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const shown = value >= 10 || unit === 0 ? Math.round(value).toString() : value.toFixed(1);
  return `${shown} ${units[unit]}`;
}

/**
 * When a meeting was, in one line: "Sunday, 20 September · 09:30–10:15".
 *
 * The meeting page used to show the start alone, which answers "when did this begin"
 * and not "was this the hour I am thinking of". Month, weekday and order come from the
 * locale; the clock does not, so it matches every other time in the app.
 */
export function formatWhen(start: string, end: string | null | undefined, locale: string): string {
  const from = new Date(start);
  if (Number.isNaN(from.getTime())) return start;
  const today = new Date();
  const day = new Intl.DateTimeFormat(locale, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: from.getFullYear() === today.getFullYear() ? undefined : "numeric",
  }).format(from);
  const to = end ? new Date(end) : null;
  const clock =
    to && !Number.isNaN(to.getTime())
      ? `${formatClock(start)}–${formatClock(end as string)}`
      : formatClock(start);
  return `${day} · ${clock}`;
}


/** A transcript offset in seconds as `mm:ss`, or `h:mm:ss` once it passes an hour. */
export function formatOffset(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const sec = whole % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}


/**
 * A duration for a dense row: `58m`, `1h 12m`, or nothing at all under a minute.
 *
 * `formatDuration` says "less than a minute", which on the seeded library was
 * printed on all seven rows in both languages — the most repeated string in the
 * application and the one carrying the least. A row that has nothing to say about
 * length should say nothing.
 */
export function formatDurationShort(seconds: number | null | undefined): string | null {
  const total = Math.max(0, Math.round(seconds ?? 0));
  if (total < 60) return null;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

/**
 * "Sun 20 Sep" — a date as a chip or a heading says it.
 *
 * Built from parts for English because Intl's own "Sun, Sep 20" puts the month
 * first and a comma after the weekday; everything else in this app writes the day
 * before the month. Hebrew keeps Intl's own order, which already is.
 */
export function formatShortDate(when: Date | string, locale: string, weekday = true): string {
  const date = typeof when === "string" ? new Date(when) : when;
  const format = new Intl.DateTimeFormat(locale, {
    weekday: weekday ? "short" : undefined,
    day: "numeric",
    month: "short",
  });
  if (!locale.startsWith("en")) return format.format(date);
  const parts = Object.fromEntries(format.formatToParts(date).map((part) => [part.type, part.value]));
  return [parts.weekday, parts.day, parts.month].filter(Boolean).join(" ");
}
