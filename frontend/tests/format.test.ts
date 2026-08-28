import { describe, expect, it } from "vitest";
import { formatDuration, formatElapsed, formatClock } from "../src/lib/format";
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
