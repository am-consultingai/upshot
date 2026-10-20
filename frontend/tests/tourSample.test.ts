import { describe, expect, it } from "vitest";
import type { CalendarEvent, Meeting } from "../src/api";
import { daysFor, startOfDay } from "../src/lib/calendar";
import { en } from "../src/locales/en";
import { SAMPLE_MEETING_ID, buildSample, isSampleId, pickOwn, pickSelector } from "../src/tour/sample";

const t = (key: keyof typeof en) => en[key];
const now = new Date(2026, 9, 20, 15, 5); // a Tuesday afternoon
const week = daysFor("week", startOfDay(now));

function meeting(id: string, title: string, at: Date): Meeting {
  return {
    id,
    title,
    state: "RENDERED",
    started_at: at.toISOString(),
    ended_at: null,
    duration_s: 600,
    language: "en",
    summary_language: "en",
    source: "recorder",
    sensitive: 0,
    folder: "",
    error: null,
  };
}

function event(id: string, title: string, at: Date, allDay = false): CalendarEvent {
  return {
    account_id: "a",
    calendar_id: "c",
    event_id: id,
    title,
    start: at.toISOString(),
    end: new Date(at.getTime() + 3_600_000).toISOString(),
    all_day: allDay,
    response: "accepted",
    transparent: false,
    attendees: [],
    attendees_partial: false,
    conference_url: null,
  };
}

const build = (input: { library?: Meeting[]; weekEvents?: CalendarEvent[]; weekMeetings?: Meeting[] }) =>
  buildSample({ now, week, library: [], weekEvents: [], weekMeetings: [], locale: "en", t, ...input });

describe("the tour's sample", () => {
  it("fills an empty week with sample events, the first one the recording the tour opens", () => {
    const sample = build({});
    expect(sample.meetings.map((m) => m.id)).toEqual([SAMPLE_MEETING_ID, "tour-sample-2"]);
    expect(sample.events).toHaveLength(5);
    expect(sample.events.every((e) => isSampleId(e.event_id))).toBe(true);
    expect(sample.events[0].meeting_id).toBe(SAMPLE_MEETING_ID);
    expect(sample.pick).toEqual({ kind: "event", id: sample.events[0].event_id });
    expect(sample.borrowed).toBe(false);
    expect(sample.meetings[0].title).toBe(en["tour.sampleTitle1"]);
  });

  it("adds no events to a week that has the user's own, and points at their next one", () => {
    const later = event("own-later", "Retro", new Date(2026, 9, 23, 16, 0));
    const sooner = event("own-sooner", "Interview: Noa", new Date(2026, 9, 20, 17, 0));
    const earlier = event("own-earlier", "Standup", new Date(2026, 9, 19, 9, 0));
    const sample = build({ weekEvents: [later, sooner, earlier] });
    expect(sample.events).toEqual([]);
    expect(sample.pick).toEqual({ kind: "event", id: "own-sooner" });
    expect(sample.ownInWeek).toBe(3);
  });

  it("borrows the titles of the user's own meetings for its recordings", () => {
    const sample = build({ library: [meeting("m1", "Team standup", new Date(2026, 9, 19, 9, 0))] });
    expect(sample.meetings.map((m) => m.title)).toEqual(["Team standup", en["tour.sampleTitle1"]]);
    expect(sample.borrowed).toBe(true);
  });

  it("counts a recording this week as the user's own, so the week gets no samples", () => {
    const sample = build({ weekMeetings: [meeting("m1", "Team standup", new Date(2026, 9, 19, 9, 0))] });
    expect(sample.events).toEqual([]);
    expect(sample.pick).toEqual({ kind: "meeting", id: "m1" });
  });

  it("records its sample before now: yesterday, in the morning", () => {
    const morning = new Date(2026, 9, 20, 8, 0);
    const sample = buildSample({ now: morning, week, library: [], weekEvents: [], weekMeetings: [], locale: "en", t });
    for (const m of sample.meetings) expect(new Date(m.started_at).getTime()).toBeLessThan(morning.getTime());
  });
});

describe("the calendar stop's pick", () => {
  it("passes over all-day events and falls back to the latest when nothing is ahead", () => {
    const allDay = event("holiday", "Holiday", new Date(2026, 9, 24, 0, 0), true);
    const past = event("past", "Standup", new Date(2026, 9, 19, 9, 0));
    expect(pickOwn([allDay, past], [], now)).toEqual({ kind: "event", id: "past" });
    expect(pickOwn([], [], now)).toBeNull();
  });

  it("selects the block on the grid", () => {
    expect(pickSelector({ kind: "event", id: "x" })).toBe('[data-testid=calendar-timegrid] [data-event="x"]');
    expect(pickSelector({ kind: "meeting", id: "m" })).toBe('[data-testid=calendar-timegrid] [data-meeting="m"]');
    expect(pickSelector(null)).toBeNull();
  });
});
