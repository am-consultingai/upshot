import { describe, expect, it } from "vitest";
import {
  formatDuration,
  formatElapsed,
  formatClock,
  formatEventTime,
  formatWhen,
} from "../src/lib/format";
import { en } from "../src/locales/en";
import type { MessageKey } from "../src/locales/en";

const t = (key: MessageKey) => en[key];

describe("formatDuration", () => {
  it("renders 47 minutes", () => {
    expect(formatDuration(47 * 60, t)).toBe("47 min");
  });

  it("renders 3 h 2 min", () => {
    expect(formatDuration(3 * 3600 + 2 * 60, t)).toBe("3 h 2 min");
  });

  it("renders a whole number of hours without minutes", () => {
    expect(formatDuration(2 * 3600, t)).toBe("2 h");
  });

  it("renders less than a minute", () => {
    expect(formatDuration(41, t)).toBe("less than a minute");
    expect(formatDuration(0, t)).toBe("less than a minute");
    expect(formatDuration(null, t)).toBe("less than a minute");
  });
});

describe("formatElapsed", () => {
  it("counts up from the start", () => {
    const start = "2026-08-28T14:00:00+03:00";
    const now = new Date(start).getTime() + 65_000;
    expect(formatElapsed(start, now)).toBe("01:05");
  });

  it("never goes negative", () => {
    const start = "2026-08-28T14:00:00+03:00";
    expect(formatElapsed(start, new Date(start).getTime() - 5000)).toBe("00:00");
  });
});

describe("formatClock", () => {
  it("is zero padded", () => {
    expect(formatClock("2026-08-28T09:05:00Z")).toMatch(/^\d{2}:\d{2}$/);
  });
});

describe("detector event times", () => {
  it("reads as a time today, and carries the date once it is not", () => {
    const now = new Date();
    const earlier = new Date(now.getTime() - 60_000);
    const today = formatEventTime(earlier.toISOString(), "en");
    expect(today).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(today).not.toContain("T");
    // 24-hour, like every other time the app shows
    expect(today).not.toMatch(/AM|PM/);

    const lastWeek = new Date(now.getTime() - 7 * 24 * 3600 * 1000);
    const older = formatEventTime(lastWeek.toISOString(), "en");
    expect(older).toMatch(/[A-Za-z]{3}/); // a month name, not an ISO string
    expect(older).not.toContain("T");
  });

  it("gives back anything it cannot parse, rather than NaN", () => {
    expect(formatEventTime("not a date", "en")).toBe("not a date");
  });
});

describe("when a meeting was", () => {
  const isoAt = (hour: number, minute = 0) => {
    const date = new Date();
    date.setHours(hour, minute, 0, 0);
    return date.toISOString();
  };

  it("names the day and spans the clock", () => {
    const line = formatWhen(isoAt(9, 30), isoAt(10, 15), "en");
    expect(line).toContain("09:30\u201310:15");
    expect(line).toMatch(/[A-Za-z]{4,}/); // a weekday and a month...
    expect(line).not.toMatch(/\d{4}-\d{2}-\d{2}/); // ...not an ISO date
  });

  it("shows the start alone when nothing says when it ended", () => {
    expect(formatWhen(isoAt(9, 30), null, "en")).toContain("09:30");
    expect(formatWhen(isoAt(9, 30), null, "en")).not.toContain("\u2013");
  });

  it("gives back anything it cannot parse, rather than NaN", () => {
    expect(formatWhen("not a date", null, "en")).toBe("not a date");
  });
});
