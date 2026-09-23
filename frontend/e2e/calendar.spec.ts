import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * Calendar 3 (z8tj1h8jrj): the calendar view with Google Calendar events behind the
 * recordings. Seeded into the cache, so these run without an account connected.
 */

async function calendarView(page: import("@playwright/test").Page, span: "day" | "week") {
  await gotoApp(page, "/");
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

/**
 * A recording made before the calendar was connected still lands on its event.
 *
 * It carries no snapshot, and nothing ever went back to give it one — so it drew a second
 * block of its own beside its own event: the same meeting twice, at two times, under two
 * names, one of which held the joining link and the other the transcript. The matcher
 * that runs at record time is asked again when the page is read, and only a confident
 * verdict is used.
 */
test("an_unmatched_recording_is_still_drawn_as_one_block", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    meetings: [
      {
        // No `calendar`: nothing was ever stored on this recording.
        id: "e2e-late",
        title: "Recording of the design review",
        state: "RENDERED",
        started_at: isoAt(0, 10, 12),
      },
    ],
    calendar_events: [
      {
        id: "e2e-late-evt",
        title: "Design review",
        start: isoAt(0, 10),
        end: isoAt(0, 11),
        attendees: ["Dana Levi", "Yoni Bar"],
      },
    ],
  });
  await calendarView(page, "day");

  // One block, the calendar's, flagged as recorded — and no block of the recording's own.
  const block = page.getByTestId("calendar-gevent").filter({ hasText: "Design review" });
  await expect(block).toHaveCount(1);
  await expect(block).toHaveAttribute("data-recorded", "true");
  await expect(page.getByTestId("calendar-event")).toHaveCount(0);

  // And it opens the recording, which now knows what meeting it was.
  await block.click();
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "e2e-late");
  // The invitation lives behind the people chip now, not in a strip on the page.
  await page.getByTestId("chip-people").click();
  await expect(page.getByTestId("meeting-calendar-title")).toHaveText("Design review");
  await expect(page.getByTestId("meeting-calendar-people")).toContainText("Dana Levi");
});

/**
 * The joining link belongs on the meeting, not on a second block in the calendar.
 *
 * The event and the recording are one thing now, so clicking the calendar opens the
 * recording — and everything that was only reachable from the event has to be on the page
 * that opens, or it is not reachable at all.
 */
test("the_meeting_page_carries_the_event_and_its_link", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    meetings: [
      {
        id: "e2e-details",
        title: "Quarterly check-in",
        state: "RENDERED",
        started_at: isoAt(0, 15),
        calendar: {
          calendar_id: "primary",
          event_id: "e2e-details-evt",
          participants: ["Ron Katz", "Dana Levi"],
        },
      },
    ],
    calendar_events: [
      {
        id: "e2e-details-evt",
        title: "Quarterly check-in",
        start: isoAt(0, 15),
        end: isoAt(0, 16),
        attendees: ["Ron Katz", "Dana Levi"],
      },
    ],
  });
  await gotoApp(page, "/m/e2e-details");

  // No strip on the page for a settled match: the mock has none, and the rail already
  // says who was in the room. The invitation opens from the people chip.
  await expect(page.getByTestId("meeting-calendar")).toHaveCount(0);
  await expect(page.getByTestId("chip-people")).toHaveText("2 people");
  await page.getByTestId("chip-people").click();
  const card = page.getByTestId("meeting-details").getByTestId("meeting-calendar");
  await expect(card).toHaveAttribute("data-state", "matched");
  await expect(card.getByTestId("meeting-calendar-people")).toContainText("Ron Katz");
  // Escape closes it and the "wrong event" escape hatch is inside it.
  await expect(card.getByTestId("meeting-calendar-pick")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("meeting-details")).toHaveCount(0);
  // The chip is the date; the hour is its tooltip.
  await expect(page.getByTestId("chip-when")).toHaveAttribute("title", /15:00/);
  // Joining moved out of the card and into the meeting's top bar, beside the other
  // things you can do to this meeting. The link is still one click from the title.
  const join = page.getByTestId("meeting-join-bar");
  await expect(join).toHaveAttribute("href", "https://meet.google.com/seed");
});
