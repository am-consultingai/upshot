/**
 * One calendar meeting, one recording; the user settles what the app cannot (D89).
 *
 * Machine B, 2026-10-06: two meetings booked at 14:00, a call that could have been either,
 * and seven recordings of it in the library. Here: the banner asks which meeting a call
 * is, an unsettled recording is marked and asked about, and two recordings of one
 * meeting are merged into the earlier.
 */
import { expect, gotoApp, isoAt, test } from "./fixtures";

const hour = new Date().getHours();
const start = isoAt(0, hour);
const end = isoAt(0, Math.min(hour + 1, 23), hour + 1 > 23 ? 59 : 0);
const BOOKED = [
  { id: "e2e-pricing", title: "Pricing review", start, end, attendees: ["Dana Levi"] },
  { id: "e2e-hiring", title: "Hiring sync", start, end, attendees: ["Noa Cohen"] },
];

test("an_unsettled_recording_is_marked_and_asked_which_meeting_it_was", async ({
  page,
  seedBody,
}) => {
  await seedBody({
    calendar_events: BOOKED,
    meetings: [
      {
        id: "e2e-which",
        title: "Recording",
        state: "RENDERED",
        started_at: isoAt(0, hour, 1),
        proposed: [{ event_id: "e2e-pricing" }, { event_id: "e2e-hiring" }],
      },
    ],
  });
  await gotoApp(page, "/");
  const row = page.getByTestId("meeting-card").filter({ hasText: "Recording" });
  await expect(row.getByTestId("meeting-needs-meeting")).toHaveText("Needs a meeting");

  await row.getByTestId("meeting-link").click();
  const card = page.getByTestId("meeting-calendar");
  await expect(card).toHaveAttribute("data-needs-meeting", "true");
  await expect(card.getByTestId("meeting-calendar-which")).toHaveText("Which meeting was this?");
  const options = card.getByTestId("meeting-calendar-proposal-option");
  await expect(options).toHaveText(["It was: Pricing review", "It was: Hiring sync"]);

  await options.filter({ hasText: "Hiring sync" }).click();
  await expect(page.getByTestId("meeting-calendar")).toHaveCount(0);
  await expect(page.getByTestId("meeting-title")).toContainText("Hiring sync");
  await expect(
    page.getByTestId("meeting-card").first().getByTestId("meeting-needs-meeting"),
  ).toHaveCount(0);
});

test("not_on_my_calendar_settles_it_too", async ({ page, seedBody }) => {
  await seedBody({
    calendar_events: BOOKED,
    meetings: [
      {
        id: "e2e-none",
        title: "Hallway chat",
        state: "RENDERED",
        started_at: isoAt(0, hour, 2),
        proposed: [{ event_id: "e2e-pricing" }, { event_id: "e2e-hiring" }],
      },
    ],
  });
  await gotoApp(page, "/m/e2e-none");
  await page.getByTestId("meeting-calendar-not-on-calendar").click();
  await expect(page.getByTestId("meeting-calendar")).toHaveCount(0);
  await expect(page.getByTestId("meeting-needs-meeting")).toHaveCount(0);
});

test("the_banner_asks_which_of_two_meetings_a_call_is", async ({ page, seedBody, seedMore }) => {
  await seedBody({ calendar_events: BOOKED });
  await gotoApp(page, "/");
  await seedMore({
    detector_events: [
      {
        process: "C:\\Program Files\\Zoom\\bin\\Zoom.exe",
        peak_score: 8,
        outcome: "shadow",
        candidates: [
          { event_id: "e2e-pricing", title: "Pricing review" },
          { event_id: "e2e-hiring", title: "Hiring sync" },
        ],
      },
    ],
  });
  const nudge = page.getByTestId("detection-nudge");
  await expect(nudge).toBeVisible({ timeout: 10_000 });
  await expect(nudge.getByTestId("detection-nudge-text")).toHaveText(
    "A call started. Which meeting is it?",
  );
  const choices = nudge.getByTestId("detection-nudge-choice");
  await expect(choices).toHaveText(["Record: Pricing review", "Record: Hiring sync"]);
  await expect(nudge.getByTestId("detection-nudge-start")).toHaveCount(0);

  await choices.filter({ hasText: "Pricing review" }).click();
  await expect(page.getByTestId("detection-nudge")).toHaveCount(0);
  const recording = page.getByTestId("meeting-card").filter({ hasText: "Pricing review" });
  await expect(recording).toHaveAttribute("data-state", "RECORDING");
  await page.request.post("/api/recording/stop", {
    headers: { "X-CSRF-Token": "e2e-csrf-secret" },
  });
});

test("two_recordings_of_one_meeting_are_merged_into_the_earlier", async ({ page, seedBody }) => {
  await seedBody({
    calendar_events: BOOKED,
    meetings: [
      {
        id: "e2e-part-1",
        title: "Pricing review",
        state: "RENDERED",
        started_at: isoAt(0, hour, 1),
        ended_at: isoAt(0, hour, 3),
        duration_s: 120,
        audio_seconds: 4,
        calendar: { calendar_id: "primary", event_id: "e2e-pricing" },
      },
      {
        id: "e2e-part-2",
        title: "Pricing review",
        state: "RENDERED",
        started_at: isoAt(0, hour, 5),
        ended_at: isoAt(0, hour, 7),
        duration_s: 120,
        audio_seconds: 4,
        calendar: { calendar_id: "primary", event_id: "e2e-pricing" },
      },
    ],
  });
  await gotoApp(page, "/m/e2e-part-2");
  const offer = page.getByTestId("meeting-calendar-merge");
  await expect(offer).toBeVisible();
  await offer.getByTestId("meeting-calendar-merge-button").click();

  await expect(page).toHaveURL(/\/m\/e2e-part-1$/);
  await expect(page.getByTestId("meeting-card")).toHaveCount(1);

  // The later address still finds the meeting.
  await page.goto("/m/e2e-part-2");
  await expect(page).toHaveURL(/\/m\/e2e-part-1$/);
});

test("assign_to_meeting_from_the_library_opens_the_choice", async ({ page, seedBody }) => {
  await seedBody({
    calendar_events: BOOKED,
    meetings: [
      {
        id: "e2e-assign",
        title: "Loose recording",
        state: "RENDERED",
        started_at: isoAt(0, hour, 1),
        calendar: { calendar_id: "primary", event_id: "e2e-pricing" },
      },
    ],
  });
  await gotoApp(page, "/");
  const row = page.getByTestId("meeting-card").filter({ hasText: "Loose recording" });
  await row.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Assign to meeting…" }).click();
  await expect(page).toHaveURL(/\/m\/e2e-assign\?assign=1$/);
  await expect(page.getByTestId("meeting-calendar-options")).toBeVisible();
  const options = page.getByTestId("meeting-calendar-option");
  await expect(options.filter({ hasText: "Pricing review" })).toHaveCount(1);
  await expect(options.filter({ hasText: "Hiring sync" })).toHaveCount(1);
});
