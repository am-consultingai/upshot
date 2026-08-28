import { describe, expect, it } from "vitest";
import { groupByDay, timelineLayout } from "../src/lib/timeline";

const at = (hhmm: string, minutes: number, id: string) => ({
  id,
  started_at: `2026-08-28T${hhmm}:00+03:00`,
  duration_s: minutes * 60,
});

describe("timelineLayout", () => {
  it("gives overlapping meetings non-overlapping columns", () => {
    const placed = timelineLayout([at("10:00", 60, "a"), at("10:30", 60, "b")]);
    const columns = placed.map((item) => item.column);
    expect(new Set(columns).size).toBe(2);
    expect(placed.every((item) => item.columns === 2)).toBe(true);
  });

  it("reuses a column once the earlier meeting has ended", () => {
    const placed = timelineLayout([at("10:00", 30, "a"), at("11:00", 30, "b")]);
    expect(placed.map((item) => item.column)).toEqual([0, 0]);
    expect(placed.every((item) => item.columns === 1)).toBe(true);
  });

  it("handles three-way overlap", () => {
    const placed = timelineLayout([
      at("10:00", 90, "a"),
      at("10:15", 60, "b"),
      at("10:30", 30, "c"),
    ]);
    expect(placed.map((item) => item.column)).toEqual([0, 1, 2]);
    expect(placed.every((item) => item.columns === 3)).toBe(true);
  });

  it("is deterministic regardless of input order", () => {
    const forwards = timelineLayout([at("10:00", 60, "a"), at("10:30", 60, "b")]);
    const backwards = timelineLayout([at("10:30", 60, "b"), at("10:00", 60, "a")]);
    expect(backwards.map((i) => [i.id, i.column])).toEqual(forwards.map((i) => [i.id, i.column]));
  });
});

describe("groupByDay", () => {
  it("groups and orders newest day first", () => {
    const days = groupByDay([
      { started_at: "2026-08-26T10:00:00+03:00" },
      { started_at: "2026-08-28T10:00:00+03:00" },
      { started_at: "2026-08-28T12:00:00+03:00" },
    ]);
    expect(days.map(([day, items]) => [day, items.length])).toEqual([
      ["2026-08-28", 2],
      ["2026-08-26", 1],
    ]);
  });
});
