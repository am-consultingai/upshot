/**
 * Times as Windows shows them to this user (GET /api/locale: sShortDate, sShortTime),
 * and the 15-minute steps the meeting-details dialog offers.
 *
 * The patterns are .NET-style — `HH:mm`, `h:mm tt`, `dd/MM/yyyy` — the TypeScript twin of
 * app/locale_formats.py.
 */
export interface LocaleFormats {
  short_date: string;
  short_time: string;
}

export const DEFAULT_FORMATS: LocaleFormats = { short_date: "dd/MM/yyyy", short_time: "HH:mm" };

const TOKEN = /'[^']*'|d{1,4}|M{1,4}|y{1,5}|h{1,2}|H{1,2}|m{1,2}|s{1,2}|t{1,2}/g;
const pad = (n: number, width = 2) => String(n).padStart(width, "0");

export function formatPattern(when: Date, pattern: string): string {
  const hour12 = when.getHours() % 12 || 12;
  return pattern.replace(TOKEN, (token) => {
    if (token.startsWith("'")) return token.slice(1, -1);
    switch (token) {
      case "d": return String(when.getDate());
      case "dd": return pad(when.getDate());
      case "M": return String(when.getMonth() + 1);
      case "MM": return pad(when.getMonth() + 1);
      case "y": return String(when.getFullYear() % 100);
      case "yy": return pad(when.getFullYear() % 100);
      case "yyy": case "yyyy": return String(when.getFullYear());
      case "h": return String(hour12);
      case "hh": return pad(hour12);
      case "H": return String(when.getHours());
      case "HH": return pad(when.getHours());
      case "m": return String(when.getMinutes());
      case "mm": return pad(when.getMinutes());
      case "s": return String(when.getSeconds());
      case "ss": return pad(when.getSeconds());
      case "t": return when.getHours() >= 12 ? "P" : "A";
      case "tt": return when.getHours() >= 12 ? "PM" : "AM";
      default: return token; // names (ddd, MMM) are not used for times
    }
  });
}

/** Hours and minutes only, as Windows writes a time: its short time without seconds. */
export function formatTime(when: Date, formats: LocaleFormats = DEFAULT_FORMATS): string {
  return formatPattern(when, formats.short_time.replace(/[:.]?s{1,2}/g, ""));
}

export const QUARTER_MS = 15 * 60 * 1000;

/** Down to the quarter hour: 14:07 → 14:00, 14:15 stays. */
export function floorToQuarter(when: Date): Date {
  const copy = new Date(when);
  copy.setSeconds(0, 0);
  copy.setMinutes(copy.getMinutes() - (copy.getMinutes() % 15));
  return copy;
}

/** Every quarter hour of the day `when` falls on. */
export function quartersOfDay(when: Date): Date[] {
  const start = new Date(when);
  start.setHours(0, 0, 0, 0);
  return Array.from({ length: 96 }, (_, i) => new Date(start.getTime() + i * QUARTER_MS));
}

/** 15 minutes up to 8 hours, in minutes. */
export const DURATIONS = Array.from({ length: 32 }, (_, i) => (i + 1) * 15);

/** "45 min", "1 h", "1 h 30 min" with the units given. */
export function durationLabel(minutes: number, units: { h: string; min: string }): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} ${units.min}`;
  return rest ? `${hours} ${units.h} ${rest} ${units.min}` : `${hours} ${units.h}`;
}
