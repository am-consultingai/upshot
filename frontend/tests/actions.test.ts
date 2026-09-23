import { describe, expect, it } from "vitest";
import { MINE, groupByOwner } from "../src/routes/Actions";
import type { ActionItem } from "../src/api";

function item(over: Partial<ActionItem> & { who: string }): ActionItem {
  return {
    id: 1,
    meeting_id: "m",
    seq: 0,
    what: "do a thing",
    due: null,
    due_at: null,
    detail: null,
    snoozed_until: null,
    source: "model",
    at_ms: null,
    mine: false,
    done: false,
    done_at: null,
    meeting_title: "A meeting",
    meeting_started_at: "2026-09-20T09:00:00Z",
    ...over,
  };
}

describe("groupByOwner", () => {
  it("puts my own items first, whatever everyone else is called", () => {
    /*
     * The regression this exists for: the group was keyed "\u0000me" and sorted with
     * localeCompare, which collates on letters and ignores the control character. My
     * four items sorted exactly where "me" would — between Maya and Yoni.
     */
    const groups = groupByOwner([
      item({ who: "Dana" }),
      item({ who: "Maya" }),
      item({ who: "ME", mine: true }),
      item({ who: "Yoni" }),
    ]);
    expect(groups.map(([owner]) => owner)).toEqual([MINE, "Dana", "Maya", "Yoni"]);
  });

  it("sorts everyone else alphabetically when nothing is mine", () => {
    const groups = groupByOwner([item({ who: "Yoni" }), item({ who: "Dana" })]);
    expect(groups.map(([owner]) => owner)).toEqual(["Dana", "Yoni"]);
  });

  it("collapses every item the server called mine into one group", () => {
    const groups = groupByOwner([
      item({ who: "ME", mine: true }),
      item({ who: "me", mine: true }),
      item({ who: "Dana" }),
    ]);
    expect(groups[0][0]).toBe(MINE);
    expect(groups[0][1]).toHaveLength(2);
    expect(groups).toHaveLength(2);
  });

  it("keeps the order the server sent within a group", () => {
    const groups = groupByOwner([
      item({ who: "Dana", what: "first" }),
      item({ who: "Dana", what: "second" }),
    ]);
    expect(groups[0][1].map((row) => row.what)).toEqual(["first", "second"]);
  });

  it("has no groups for no items", () => {
    expect(groupByOwner([])).toEqual([]);
  });
});
