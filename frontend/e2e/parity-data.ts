/**
 * The redesign mock's own library, as seed data.
 *
 * `.ui-research/mocks/redesign.html` draws one week of one person's meetings: an
 * onboarding review with three action items and a tag, a quarterly check-in, a 1:1
 * whose summary failed, a recording in progress, a vendor call coming up, and a
 * colleague on annual leave. Seeding exactly that is what lets the app be put beside
 * the mock screen for screen — and what gives each piece of the mock (overdue items,
 * the all-day band, "Up next", related meetings, named speakers, chapters) something
 * real to render in the specs that check it.
 *
 * Every date is relative to today, so the week the grid opens on is always this one.
 */
import { dateFromToday, isoAt, minutesFromNow, type SeedBody } from "./fixtures";

const ONBOARDING_SUMMARY = `<p>Activation is down 6% since the new signup flow shipped, and step 3 is where people fall off.</p>
<p>Engineering wants two sprints to rebuild the interstitial; design pushed back on the modal pattern. The room agreed to roll back for new workspaces and instrument before changing anything else.</p>
<h2>Decisions</h2>
<ul>
<li><strong>Roll back the interstitial</strong> for new workspaces only, leaving existing workspaces on the current flow.</li>
<li><strong>Instrument step 3</strong> before any further change — no design work starts until the drop-off is segmented.</li>
</ul>
<p class="next">Ask Dana for a timeline to the spec landing</p>
<h2>Open questions</h2>
<p>Whether the copy change that shipped with the interstitial explains part of the drop, since the two are not measured separately.</p>
<h2>Numbers mentioned</h2>
<ul><li>Activation: down 6% week over week</li><li>Step 3 completion: 71% → 64%</li></ul>`;

const ONBOARDING_TURNS = [
  { speaker: "THEM_1", at_ms: 4_000, end_ms: 18_000, text: "So activation is down six percent since we shipped the new flow. I pulled it this morning and it is not spread across the funnel — it is almost all in one place." },
  { speaker: "ME", at_ms: 19_000, end_ms: 22_500, text: "Is that the whole funnel or just step three?" },
  { speaker: "THEM_1", at_ms: 23_000, end_ms: 40_000, text: "Mostly step three. The interstitial is where people fall off. Everything before it looks the same as it did in August." },
  { speaker: "THEM_1", at_ms: 41_000, end_ms: 57_000, text: "What I cannot tell you is whether it is the interstitial itself or the copy change that went out with it, because we are not measuring them separately." },
  { speaker: "ME", at_ms: 58_000, end_ms: 74_000, text: "Then let us roll it back for new workspaces and instrument before we touch anything else. I do not want to spend two sprints on a rebuild we cannot measure." },
  { speaker: "THEM_2", at_ms: 75_000, end_ms: 92_000, text: "Design will not love that, but I agree. I will pull the drop-off by segment so we have the numbers before Monday." },
  { speaker: "THEM_1", at_ms: 93_000, end_ms: 110_000, text: "I can have the instrumentation spec for step three written by Thursday. It is not a big change — we just never put an event on the dismiss." },
];

/** The mock's library, around today. */
export function parityBody(): SeedBody {
  return {
    reset: true,
    calendar_events: [
      {
        id: "ev-appsflyer",
        title: "AppsFlyer — quarterly check-in",
        start: isoAt(3, 15),
        end: isoAt(3, 16),
        attendees: ["Ron Katz", "Dana Levi"],
      },
      {
        id: "ev-onboarding",
        title: "Onboarding funnel review",
        start: isoAt(3, 9, 30),
        end: isoAt(3, 10, 15),
        attendees: ["Dana Levi", "Yoni Bar"],
      },
      {
        id: "ev-interview",
        title: "Interview — PM candidate",
        start: isoAt(3, 16, 30),
        end: isoAt(3, 17, 30),
        attendees: ["Maya Cohen"],
      },
      { id: "ev-design-sync", title: "Design sync", start: isoAt(1, 9), end: isoAt(1, 9, 25), attendees: ["Dana Levi"] },
      {
        id: "ev-vendor",
        title: "Vendor call — Wix",
        start: minutesFromNow(153),
        end: minutesFromNow(213),
        attendees: ["Ron Katz", "Gal Ofer", "Noa Paz"],
      },
      { id: "ev-roadmap", title: "Roadmap review", start: isoAt(-1, 10), end: isoAt(-1, 11), attendees: ["Yoni Bar"] },
      { id: "ev-board", title: "Board prep", start: isoAt(-1, 15), end: isoAt(-1, 16), attendees: ["Ron Katz"] },
      {
        id: "ev-leave",
        title: "Ron — annual leave",
        start: `${dateFromToday(1)}T00:00:00Z`,
        end: `${dateFromToday(2)}T00:00:00Z`,
        all_day: true,
        attendees: ["Ron Katz"],
      },
    ],
    meetings: [
      {
        id: "m-onboarding",
        title: "Onboarding funnel review",
        state: "DELIVERED",
        started_at: isoAt(3, 9, 30),
        duration_s: 42 * 60,
        language: "en",
        tags: ["Growth"],
        calendar: { calendar_id: "primary", event_id: "ev-onboarding" },
        speaker_names: { THEM_1: "Dana Levi", THEM_2: "Yoni Bar" },
        audio_seconds: 112,
        turns: ONBOARDING_TURNS,
        chapters: [
          { title: "The drop-off", start_ms: 0, end_ms: 41_000 },
          { title: "Cost of the rebuild", start_ms: 41_000, end_ms: 75_000 },
          { title: "Rollback decision", start_ms: 75_000, end_ms: 93_000 },
          { title: "Next steps", start_ms: 93_000, end_ms: 112_000 },
        ],
        summary_html: ONBOARDING_SUMMARY,
        action_items: [
          {
            who: "ME",
            what: "Take the rollback decision to the Monday review",
            detail: "Design pushed back on the modal pattern; needs a call before the sprint",
            due: "Monday",
            due_at: dateFromToday(2),
            at_ms: 58_000,
          },
          {
            who: "Dana Levi",
            what: "Write the instrumentation spec for step 3",
            detail: "Done Thursday",
            due: "Thursday",
            due_at: dateFromToday(-1),
            at_ms: 93_000,
            done: true,
          },
          {
            who: "Yoni Bar",
            what: "Pull the last 30 days of drop-off by segment",
            detail: "Blocks the rollback decision",
            due: "before Monday",
            due_at: dateFromToday(-2),
            at_ms: 75_000,
          },
        ],
      },
      {
        id: "m-appsflyer",
        title: "AppsFlyer — quarterly check-in",
        state: "DELIVERED",
        started_at: isoAt(3, 15),
        duration_s: 58 * 60,
        language: "en",
        calendar: { calendar_id: "primary", event_id: "ev-appsflyer" },
        summary_html: "<p>Ron is waiting on the numbers we agreed before the renewal.</p>",
        turns: [
          { speaker: "THEM", at_ms: 1_000, text: "We need the activation numbers before we can talk about the renewal." },
          { speaker: "ME", at_ms: 9_000, text: "I will send the numbers we agreed by Friday." },
        ],
        action_items: [
          {
            who: "ME",
            what: "Send the numbers we agreed",
            detail: "Ron is waiting on these",
            due: "Friday",
            due_at: dateFromToday(2),
          },
          { who: "Ron Katz", what: "Share the renewal terms", due: "next week", due_at: dateFromToday(9) },
        ],
      },
      {
        id: "m-dana",
        title: "1:1 — Dana",
        state: "FAILED",
        started_at: isoAt(3, 13),
        duration_s: 60 * 60,
        jobs: { transcribe: "done", summarize: "failed" },
      },
      {
        id: "m-standup",
        title: "Product / Eng standup",
        state: "DELIVERED",
        started_at: isoAt(3, 11),
        duration_s: 24 * 60,
        summary_html: "<p>Nothing blocking; the release goes out Thursday.</p>",
        action_items: [{ who: "ME", what: "Post the release notes", done: true }],
      },
      {
        id: "m-q4",
        title: "Q4 roadmap working session",
        state: "DELIVERED",
        started_at: isoAt(4, 10),
        duration_s: 72 * 60,
        language: "en",
        calendar: { calendar_id: "primary", event_id: "ev-q4", participants: ["Dana Levi", "Yoni Bar"] },
        summary_html: "<p>The Q4 plan keeps activation as the one company goal.</p>",
        turns: [
          { speaker: "THEM", at_ms: 2_000, text: "Activation stays the goal for the quarter, and the rollback decision feeds straight into it." },
        ],
        action_items: [
          { who: "ME", what: "Circulate the Q4 one-pager", due: "Thursday", due_at: dateFromToday(1) },
          {
            who: "Yoni Bar",
            what: "Pull the last 30 days of drop-off by segment",
            due_at: dateFromToday(-2),
          },
        ],
      },
      {
        id: "m-design",
        title: "Design review — checkout",
        state: "FAILED",
        started_at: isoAt(5, 14),
        duration_s: 45 * 60,
        jobs: { transcribe: "failed" },
      },
      {
        id: "m-exec",
        title: "Exec staff meeting",
        state: "DELIVERED",
        started_at: isoAt(6, 9),
        duration_s: 55 * 60,
        language: "en",
        summary_html: "<p>Activation is the number the board will ask about.</p>",
        turns: [{ speaker: "ME", at_ms: 3_000, text: "I will send the activation numbers to the exec list before the board pack." }],
        action_items: [
          {
            who: "ME",
            what: "Send the activation numbers to the exec list",
            detail: "promised before the board pack",
            due_at: dateFromToday(-4),
          },
          { who: "Dana Levi", what: "Book time with legal about the retention policy" },
        ],
      },
    ],
  };
}
