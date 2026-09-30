import { describe, expect, it } from "vitest";
import { DURATIONS, durationLabel, floorToQuarter, formatPattern, formatTime, quartersOfDay } from "../src/lib/timeFormat";

const at = (h: number, m: number) => new Date(2026, 8, 30, h, m, 9);

describe("times as Windows shows them", () => {
  it("follows the short time pattern", () => {
    expect(formatTime(at(14, 5), { short_date: "", short_time: "HH:mm" })).toBe("14:05");
    expect(formatTime(at(14, 5), { short_date: "", short_time: "h:mm tt" })).toBe("2:05 PM");
    expect(formatTime(at(9, 5), { short_date: "", short_time: "hh:mm:ss" })).toBe("09:05");
  });
  it("formats dates too", () => {
    expect(formatPattern(at(14, 5), "dd/MM/yyyy")).toBe("30/09/2026");
    expect(formatPattern(at(14, 5), "M/d/yy")).toBe("9/30/26");
  });
});

describe("quarter hours", () => {
  it("floors the start to the quarter", () => {
    expect(formatTime(floorToQuarter(at(14, 7)))).toBe("14:00");
    expect(formatTime(floorToQuarter(at(14, 15)))).toBe("14:15");
    expect(formatTime(floorToQuarter(at(14, 59)))).toBe("14:45");
  });
  it("offers every quarter of the day", () => {
    const slots = quartersOfDay(at(14, 7));
    expect(slots).toHaveLength(96);
    expect(formatTime(slots[0])).toBe("00:00");
    expect(formatTime(slots[95])).toBe("23:45");
  });
  it("offers durations in quarters", () => {
    expect(DURATIONS[0]).toBe(15);
    expect(DURATIONS.at(-1)).toBe(480);
    const units = { h: "h", min: "min" };
    expect(durationLabel(45, units)).toBe("45 min");
    expect(durationLabel(60, units)).toBe("1 h");
    expect(durationLabel(90, units)).toBe("1 h 30 min");
  });
});
