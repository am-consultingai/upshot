import { describe, expect, it } from "vitest";
import {
  asksForMeeting,
  dayItems,
  eventKey,
  pairRecordings,
  recordingDot,
  type AskableMeeting,
} from "../src/lib/calendar";

/** Local times on one day, so day bucketing does not depend on the test machine's zone. */
const at = (hour: number, minute = 0) => new Date(2026, 9, 7, hour, minute).toISOString();

const recording = (over: Partial<AskableMeeting & { title: string | null }> = {}) => ({
  id: "rec",
  title: "Recording",
  state: "RENDERED",
  started_at: at(10, 5),
  duration_s: 50 * 60,
  needs_meeting: true,
  ...over,
});

const event = (id: string, start: string, end: string, over: Record<string, unknown> = {}) => ({
  account_id: "acc",
  calendar_id: "primary",
  event_id: id,
  title: id,
  start,
  end,
  all_day: false,
  meeting_id: null as string | null,
  ...over,
});

describe("asksForMeeting", () => {
  it("asks while the backend says the meeting is not settled", () => {
    expect(asksForMeeting(recording())).toBe(true);
  });

  it("does not ask once the user said it was on no calendar", () => {
    expect(
      asksForMeeting(
        recording({ needs_meeting: false, calendar_match: { state: "none", source: "user" } }),
      ),
    ).toBe(false);
  });

  it("does not ask about a matched recording", () => {
    expect(
      asksForMeeting(
        recording({ needs_meeting: false, calendar_match: { state: "matched", source: "auto" } }),
      ),
    ).toBe(false);
  });

  it("asks about a recording nothing was ever stored on", () => {
    expect(asksForMeeting(recording({ needs_meeting: false, calendar_match: null }))).toBe(true);
  });

  it("never asks about one still recording", () => {
    expect(asksForMeeting(recording({ state: "RECORDING" }))).toBe(false);
  });
});

describe("pairRecordings", () => {
  it("puts an unmatched recording inside the event it overlaps", () => {
    const review = event("review", at(10), at(11));
    const pairs = pairRecordings([recording()], [review]);
    expect(pairs.get(eventKey(review))?.meeting.id).toBe("rec");
  });

  it("leaves a recording that overlaps nothing alone", () => {
    const later = event("later", at(14), at(15));
    expect(pairRecordings([recording()], [later]).size).toBe(0);
  });

  it("takes the event with the largest overlap", () => {
    const early = event("early", at(9, 30), at(10, 15));
    const main = event("main", at(10), at(11));
    const pairs = pairRecordings([recording()], [early, main]);
    expect([...pairs.values()].map((pair) => pair.event.event_id)).toEqual(["main"]);
  });

  it("prefers the event the backend proposed over a larger overlap", () => {
    const early = event("early", at(9, 30), at(10, 15));
    const main = event("main", at(10), at(11));
    const pairs = pairRecordings(
      [recording({ proposed: [{ calendar_id: "primary", event_id: "early" }] })],
      [early, main],
    );
    expect([...pairs.values()].map((pair) => pair.event.event_id)).toEqual(["early"]);
  });

  it("never pairs with an event another recording is matched to", () => {
    const taken = event("taken", at(10), at(11), { meeting_id: "other" });
    expect(pairRecordings([recording()], [taken]).size).toBe(0);
  });

  it("does not pair a matched or settled recording", () => {
    const review = event("review", at(10), at(11));
    const settled = recording({ needs_meeting: false, calendar_match: { state: "none", source: "user" } });
    expect(pairRecordings([settled], [review]).size).toBe(0);
  });

  it("gives an event to one recording only, the earlier one", () => {
    const review = event("review", at(10), at(11));
    const first = recording({ id: "first", started_at: at(10, 1) });
    const second = recording({ id: "second", started_at: at(10, 20) });
    const pairs = pairRecordings([second, first], [review]);
    expect(pairs.size).toBe(1);
    expect(pairs.get(eventKey(review))?.meeting.id).toBe("first");
  });

  it("ignores all-day events", () => {
    const away = event("away", "2026-10-07", "2026-10-08", { all_day: true });
    expect(pairRecordings([recording()], [away]).size).toBe(0);
  });
});

describe("recordingDot", () => {
  it("is live, failed, asking or recorded, in that order", () => {
    expect(recordingDot({ state: "RECORDING", needs_meeting: true })).toBe("live");
    expect(recordingDot({ state: "FAILED", needs_meeting: true })).toBe("failed");
    expect(recordingDot({ state: "RENDERED", needs_meeting: true })).toBe("needs");
    expect(recordingDot({ state: "RENDERED", needs_meeting: false })).toBe("recorded");
  });
});

describe("dayItems", () => {
  const day = new Date(2026, 9, 7);

  it("lists everything on the day in time order, all-day first", () => {
    const items = dayItems(
      day,
      [
        recording({ id: "alone", started_at: at(16), needs_meeting: false, title: "Late call" }),
        recording({ id: "matched", started_at: at(9), needs_meeting: false, state: "FAILED" }),
      ],
      [
        event("standup", at(9), at(9, 15), { meeting_id: "matched" }),
        event("lunch", at(12), at(13)),
        event("away", "2026-10-07", "2026-10-08", { all_day: true }),
      ],
    );
    expect(items.map((item) => [item.title, item.state])).toEqual([
      ["away", "scheduled"],
      ["standup", "failed"],
      ["lunch", "scheduled"],
      ["Late call", "recorded"],
    ]);
  });

  it("leaves out other days", () => {
    const items = dayItems(day, [recording({ started_at: new Date(2026, 9, 8, 10).toISOString() })], []);
    expect(items).toEqual([]);
  });
});
