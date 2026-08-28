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
