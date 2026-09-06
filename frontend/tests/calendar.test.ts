import { describe, expect, it } from "vitest";
import {
  addDays,
  bucketByDay,
  dayKey,
  daysFor,
  isSameMonth,
  periodLabel,
  placement,
  rangeFor,
  shift,
  startOfWeek,
  weekdayLabels,
} from "../src/lib/calendar";

const at = (iso: string) => new Date(iso);

describe("grid shape", () => {
  it("a week is seven days starting Sunday", () => {
    const days = daysFor("week", at("2026-09-02T10:00:00"));
    expect(days).toHaveLength(7);
    expect(days[0].getDay()).toBe(0);
    expect(dayKey(days[0])).toBe("2026-08-30");
    expect(dayKey(days[6])).toBe("2026-09-05");
  });

  it("a day is just that day", () => {
    expect(daysFor("day", at("2026-09-02T23:30:00")).map(dayKey)).toEqual(["2026-09-02"]);
  });

  it("a month is padded to whole weeks", () => {
    const days = daysFor("month", at("2026-09-15T12:00:00"));
    expect(days.length % 7).toBe(0);
    expect(days[0].getDay()).toBe(0);
    expect(days.some((d) => dayKey(d) === "2026-09-01")).toBe(true);
    expect(days.some((d) => dayKey(d) === "2026-09-30")).toBe(true);
    // leading days belong to the previous month and must be marked as such
    expect(isSameMonth(days[0], at("2026-09-15T12:00:00"))).toBe(false);
  });

  it("covers a month that starts on a Sunday without a blank leading week", () => {
    const days = daysFor("month", at("2026-11-10T12:00:00")); // Nov 2026 starts Sunday
    expect(dayKey(days[0])).toBe("2026-11-01");
  });
});

describe("navigation", () => {
  it("steps by span", () => {
    expect(dayKey(shift("day", at("2026-09-02T00:00:00"), 1))).toBe("2026-09-03");
    expect(dayKey(shift("week", at("2026-09-02T00:00:00"), -1))).toBe("2026-08-26");
    expect(dayKey(shift("month", at("2026-09-15T00:00:00"), 1))).toBe("2026-10-01");
  });

  it("crosses a year boundary", () => {
    expect(dayKey(shift("month", at("2026-12-10T00:00:00"), 1))).toBe("2027-01-01");
    expect(dayKey(shift("month", at("2026-01-10T00:00:00"), -1))).toBe("2025-12-01");
  });

  it("adding days survives a DST change", () => {
    // Adding 24h in milliseconds would land on the same date across a spring-forward.
    const before = new Date(2026, 2, 26, 12, 0, 0);
    expect(dayKey(addDays(before, 1))).toBe("2026-03-27");
    expect(dayKey(addDays(before, 7))).toBe("2026-04-02");
  });
});

describe("query range", () => {
  it("is inclusive of the first day and exclusive of the day after the last", () => {
    expect(rangeFor("week", at("2026-09-02T10:00:00"))).toEqual({
      from: "2026-08-30",
      to: "2026-09-06",
    });
    expect(rangeFor("day", at("2026-09-02T10:00:00"))).toEqual({
      from: "2026-09-02",
      to: "2026-09-03",
    });
  });
});

describe("placing meetings", () => {
  it("groups by local day and orders within it", () => {
    const items = [
      { id: "b", started_at: "2026-09-02T14:00:00+03:00" },
      { id: "a", started_at: "2026-09-02T09:00:00+03:00" },
      { id: "c", started_at: "2026-09-03T09:00:00+03:00" },
    ];
    const buckets = bucketByDay(items);
    expect([...buckets.keys()].sort()).toEqual(["2026-09-02", "2026-09-03"]);
    expect(buckets.get("2026-09-02")!.map((m) => m.id)).toEqual(["a", "b"]);
  });

  it("positions by minutes from midnight", () => {
    const day = at("2026-09-02T00:00:00");
    const start = new Date(2026, 8, 2, 9, 30, 0).toISOString();
    const { startMinute, minutes } = placement({ started_at: start, duration_s: 3600 }, day);
    expect(startMinute).toBe(9 * 60 + 30);
    expect(minutes).toBe(60);
  });

  it("never runs past midnight", () => {
    const day = at("2026-09-02T00:00:00");
    const start = new Date(2026, 8, 2, 23, 30, 0).toISOString();
    const { minutes } = placement({ started_at: start, duration_s: 4 * 3600 }, day);
    expect(minutes).toBe(30);
  });

  it("gives an unfinished recording a default length rather than zero height", () => {
    const day = at("2026-09-02T00:00:00");
    const start = new Date(2026, 8, 2, 10, 0, 0).toISOString();
    expect(placement({ started_at: start, duration_s: null }, day).minutes).toBe(30);
  });
});

describe("locale labels", () => {
  it("weekdays start on Sunday and follow the locale", () => {
    expect(weekdayLabels("en-US")).toHaveLength(7);
    expect(weekdayLabels("en-US")[0]).toMatch(/^Sun/);
    expect(weekdayLabels("he-IL")[0]).not.toMatch(/^Sun/);
  });

  it("period labels come from Intl, not a hand-written table", () => {
    expect(periodLabel("month", at("2026-09-15T00:00:00"), "en-US")).toBe("September 2026");
    expect(periodLabel("week", at("2026-09-02T00:00:00"), "en-US")).toContain("–");
    expect(startOfWeek(at("2026-09-02T00:00:00")).getDay()).toBe(0);
  });
});
