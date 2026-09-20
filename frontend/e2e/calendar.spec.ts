import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * Calendar 3 (z8tj1h8jrj): the calendar view with Google Calendar events behind the
 * recordings. Seeded into the cache, so these run without an account connected.
 */

async function calendarView(page: import("@playwright/test").Page, span: "day" | "week") {
  await gotoApp(page, "/");
  await page.getByTestId("view-calendar").click();
  await page.getByTestId(`span-${span}`).click();
  await expect(page.getByTestId("calendar-timegrid")).toBeVisible();
}

test("an_event_renders_and_opens", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    calendar_events: [
      {
        id: "e2e-evt",
        title: "Design review",
        start: isoAt(0, 10),
        end: isoAt(0, 11),
        attendees: ["Dana Levi", "יוסי כהן"],
      },
    ],
  });
  await calendarView(page, "day");

  const event = page.getByTestId("calendar-gevent").filter({ hasText: "Design review" });
  await expect(event).toBeVisible();
  await event.click();
  await expect(page.getByTestId("event-title")).toHaveText("Design review");
  await expect(page.getByTestId("event-attendees")).toContainText("Dana Levi");
  await page.getByTestId("event-close").click();
  await expect(page.getByTestId("event-details")).toHaveCount(0);
});

test("a_recorded_meeting_keeps_its_calendar_slot_and_is_flagged", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    meetings: [
      {
        id: "e2e-matched",
        // The recording began late and ran short, as recordings do.
        title: "Design review",
        state: "RENDERED",
        started_at: isoAt(0, 10, 12),
        calendar: { calendar_id: "primary", event_id: "e2e-evt2" },
      },
    ],
    calendar_events: [
      { id: "e2e-evt2", title: "Design review", start: isoAt(0, 10), end: isoAt(0, 11) },
      { id: "e2e-alone", title: "Unrecorded call", start: isoAt(0, 14), end: isoAt(0, 15) },
    ],
  });
  await calendarView(page, "day");

  // One block for the meeting, drawn at the event's time under the event's name...
  const recorded = page.getByTestId("calendar-gevent").filter({ hasText: "Design review" });
  await expect(recorded).toHaveCount(1);
  await expect(recorded).toHaveAttribute("data-recorded", "true");
  await expect(recorded.getByTestId("calendar-recorded-flag")).toBeVisible();
  // ...and the recording does not draw a second block of its own.
  await expect(page.getByTestId("calendar-event")).toHaveCount(0);

  // The unrecorded event is still there, and still unflagged.
  const plain = page.getByTestId("calendar-gevent").filter({ hasText: "Unrecorded call" });
  await expect(plain).toHaveAttribute("data-recorded", "false");

  // Clicking the flagged block opens the recording.
  await recorded.click();
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "e2e-matched");
});

test("an_event_on_now_offers_to_record_it", async ({ page, seedBody }) => {
  const now = new Date();
  const start = new Date(now.getTime() - 5 * 60_000).toISOString();
  const end = new Date(now.getTime() + 25 * 60_000).toISOString();
  await seedBody({ reset: true, calendar_events: [{ id: "e2e-now", title: "Standup", start, end }] });
  await calendarView(page, "day");

  await page.getByTestId("calendar-gevent").filter({ hasText: "Standup" }).click();
  await page.getByTestId("event-record").click();
  await expect(page.getByTestId("recording-bar")).toBeVisible();
  await page.getByTestId("recording-bar-stop").click();
});
