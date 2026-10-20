import type { CalendarEvent, Meeting } from "../api";
import type { MessageKey } from "../locales/en";
import { addDays, dayKey, startOfDay } from "../lib/calendar";

/**
 * The tour's sample: two recordings for the sidebar and, when the user's week is empty,
 * a week of events for the calendar.
 *
 * It lives only in the page's memory, for as long as the tour runs. Nothing here is ever
 * sent to the server, so it never reaches the database, search or the assistant, and
 * removing it is dropping it: no delete call exists that could reach a real meeting.
 * Every id starts with SAMPLE_PREFIX, which is how the page tells a sample from a real
 * meeting, and how the fire at the end finds what to burn.
 */
export const SAMPLE_PREFIX = "tour-sample-";
export const SAMPLE_MEETING_ID = `${SAMPLE_PREFIX}1`;

export function isSampleId(id: string | null | undefined): boolean {
  return typeof id === "string" && id.startsWith(SAMPLE_PREFIX);
}

/** What the sample meeting's page shows: its summary and action items, all in the user's language. */
export interface SampleDetail {
  decisions: string[];
  questions: string[];
  actions: { who: string; what: string; due: string }[];
}

/** The calendar stop's target: one of the user's own events or recordings, or a sample event. */
export interface CalendarPick {
  kind: "event" | "meeting";
  id: string;
}

export interface TourSample {
  /** The sample recordings, for the sidebar. The first is the meeting the tour opens. */
  meetings: Meeting[];
  /** Sample events, only when the user's week is empty; otherwise none. */
  events: CalendarEvent[];
  pick: CalendarPick | null;
  detail: SampleDetail;
  /** Whether the titles came from the user's own meetings. */
  borrowed: boolean;
  /** How much of the user's own the week holds: their events and recordings. */
  ownInWeek: number;
}

type Translate = (key: MessageKey) => string;

const DEFAULT_TITLES: MessageKey[] = [
  "tour.sampleTitle1",
  "tour.sampleTitle2",
  "tour.sampleTitle3",
  "tour.sampleTitle4",
  "tour.sampleTitle5",
];

/** A local time with its offset, as the server writes one, so a day groups as it is lived. */
export function localIso(date: Date): string {
  const pad = (value: number) => String(Math.abs(value)).padStart(2, "0");
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  return (
    `${dayKey(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}:00` +
    `${sign}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`
  );
}

function at(day: Date, hours: number, minutes: number): Date {
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), hours, minutes);
}

function sampleMeeting(id: string, title: string, start: Date, minutes: number, actions: number, locale: string): Meeting {
  return {
    id,
    title,
    state: "RENDERED",
    started_at: localIso(start),
    ended_at: localIso(new Date(start.getTime() + minutes * 60_000)),
    duration_s: minutes * 60,
    language: locale,
    summary_language: locale,
    source: "tour",
    sensitive: 0,
    folder: "",
    error: null,
    actions_total: actions,
    actions_open: actions,
    tags: [],
    calendar_accounts: [],
  };
}

function sampleEvent(n: number, title: string, start: Date, minutes: number, meetingId: string | null): CalendarEvent {
  return {
    account_id: "tour-sample",
    calendar_id: "tour-sample",
    event_id: `${SAMPLE_PREFIX}e${n}`,
    title,
    start: localIso(start),
    end: localIso(new Date(start.getTime() + minutes * 60_000)),
    all_day: false,
    response: "accepted",
    transparent: false,
    attendees: [],
    attendees_partial: false,
    conference_url: null,
    meeting_id: meetingId,
  };
}

/**
 * The user's own event or recording the calendar stop points at: the next one this week,
 * else the latest. Events first: they are what the calendar is for.
 */
export function pickOwn(events: CalendarEvent[], meetings: Meeting[], now: Date): CalendarPick | null {
  const timed = events.filter((event) => !event.all_day);
  const candidates: { pick: CalendarPick; start: number }[] = [
    ...timed.map((event) => ({ pick: { kind: "event" as const, id: event.event_id }, start: Date.parse(event.start) })),
    ...meetings.map((meeting) => ({ pick: { kind: "meeting" as const, id: meeting.id }, start: Date.parse(meeting.started_at) })),
  ].filter((item) => Number.isFinite(item.start));
  if (candidates.length === 0) return null;
  const ahead = candidates.filter((item) => item.start >= now.getTime()).sort((a, b) => a.start - b.start);
  if (ahead.length > 0) return ahead[0].pick;
  return candidates.sort((a, b) => b.start - a.start)[0].pick;
}

/**
 * Builds the sample once, when the tour starts.
 *
 * - Sidebar: always two sample recordings, titled after the user's own meetings when there
 *   are any (theirs first, then their week's events), else after the defaults.
 * - Calendar: sample events only when the week has nothing of the user's — no calendar
 *   connected, or nothing booked and nothing recorded. Otherwise none, and the tour points
 *   at one of theirs.
 */
export function buildSample(input: {
  now: Date;
  /** The days of the week the calendar shows. */
  week: Date[];
  /** The user's recordings, the whole library. */
  library: Meeting[];
  /** The user's events and recordings in this week. */
  weekEvents: CalendarEvent[];
  weekMeetings: Meeting[];
  locale: string;
  t: Translate;
}): TourSample {
  const { now, week, library, weekEvents, weekMeetings, locale, t } = input;
  const own = [
    ...library.map((meeting) => meeting.title),
    ...weekEvents.map((event) => event.title),
  ].filter((title): title is string => typeof title === "string" && title.trim() !== "");
  const pool = [...new Set(own.map((title) => title.trim()))];
  const titles = [...pool, ...DEFAULT_TITLES.map(t).filter((title) => !pool.includes(title))];

  // Recorded before now: today after noon, yesterday before it.
  const base = now.getHours() >= 12 ? startOfDay(now) : addDays(startOfDay(now), -1);
  const first = at(base, 10, 0);
  const second = at(base, 11, 30);
  const meetings = [
    sampleMeeting(SAMPLE_MEETING_ID, titles[0], first, 42, 3, locale),
    sampleMeeting(`${SAMPLE_PREFIX}2`, titles[1], second, 25, 1, locale),
  ];

  const ownInWeek = weekEvents.length + weekMeetings.length;
  let events: CalendarEvent[] = [];
  let pick = pickOwn(weekEvents, weekMeetings, now);
  if (ownInWeek === 0) {
    const inWeek = week.some((day) => dayKey(day) === dayKey(base));
    // The other three on working days of the week, away from the recordings' day.
    const others = week.filter((day) => dayKey(day) !== dayKey(base) && day.getDay() !== 0 && day.getDay() !== 6);
    const spare = others.length >= 3 ? others : week.filter((day) => dayKey(day) !== dayKey(base));
    const slots: [number, number, number][] = [
      [9, 30, 30],
      [14, 0, 60],
      [16, 30, 60],
    ];
    events = [
      ...(inWeek
        ? [
            sampleEvent(1, titles[0], first, 45, meetings[0].id),
            sampleEvent(2, titles[1], second, 30, meetings[1].id),
          ]
        : []),
      ...spare.slice(0, 3).map((day, index) => {
        const [hours, minutes, length] = slots[index];
        return sampleEvent(index + 3, titles[index + 2], at(day, hours, minutes), length, null);
      }),
    ];
    pick = events.length > 0 ? { kind: "event", id: events[0].event_id } : null;
  }

  return {
    meetings,
    events,
    pick,
    detail: {
      decisions: [t("tour.sampleDecision1"), t("tour.sampleDecision2")],
      questions: [t("tour.sampleQuestion1"), t("tour.sampleQuestion2")],
      actions: [
        { who: "Dana", what: t("tour.sampleAction1"), due: dayKey(addDays(now, 3)) },
        { who: "Yossi", what: t("tour.sampleAction2"), due: dayKey(addDays(now, 2)) },
        { who: t("tour.sampleYou"), what: t("tour.sampleAction3"), due: dayKey(addDays(now, 1)) },
      ],
    },
    borrowed: pool.length > 0,
    ownInWeek,
  };
}

/** Selector for the calendar stop: the picked block on the grid. */
export function pickSelector(pick: CalendarPick | null): string | null {
  if (!pick) return null;
  const id = pick.id.replace(/["\\]/g, "\\$&");
  return pick.kind === "event"
    ? `[data-testid=calendar-timegrid] [data-event="${id}"]`
    : `[data-testid=calendar-timegrid] [data-meeting="${id}"]`;
}
