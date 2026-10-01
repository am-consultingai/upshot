import { expect, gotoApp, gotoSettings, isoAt, test } from "./fixtures";

/**
 * Several Google accounts at once (epic z8tj1hb9je, D82): dots, the Calendars switch,
 * hidden-everywhere, the library filter, and the accounts in Settings. Seeded, so no
 * Google account is needed: the seed pins its accounts as connected.
 */

const WORK = { id: "ga_work0001", address: "work@example.com" };
const HOME = { id: "ga_home0001", address: "me@example.com" };

async function twoAccounts(
  seedBody: (body: import("./fixtures").SeedBody) => Promise<unknown>,
  extra: Partial<import("./fixtures").SeedBody> = {},
) {
  await seedBody({
    reset: true,
    calendar_accounts: [WORK, HOME],
    calendar_events: [
      { id: "ev-work", account_id: WORK.id, title: "Work planning", start: isoAt(0, 10), end: isoAt(0, 11) },
      { id: "ev-home", account_id: HOME.id, title: "Dentist call", start: isoAt(0, 14), end: isoAt(0, 15) },
    ],
    meetings: [
      {
        id: "m-work",
        title: "Work planning",
        state: "RENDERED",
        started_at: isoAt(0, 10, 2),
        calendar: { account_id: WORK.id, calendar_id: "primary", event_id: "ev-work" },
      },
      {
        id: "m-home",
        title: "Dentist call",
        state: "RENDERED",
        started_at: isoAt(0, 14, 1),
        calendar: { account_id: HOME.id, calendar_id: "primary", event_id: "ev-home" },
      },
      { id: "m-plain", title: "Hallway chat", state: "RENDERED", started_at: isoAt(0, 16) },
    ],
    ...extra,
  });
}

async function dayView(page: import("@playwright/test").Page) {
  await gotoApp(page, "/");
  await page.getByTestId("span-day").click();
  await expect(page.getByTestId("calendar-timegrid")).toBeVisible();
}

test("two_accounts_show_their_events_with_their_dots", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await dayView(page);
  const work = page.getByTestId("calendar-gevent").filter({ hasText: "Work planning" });
  const home = page.getByTestId("calendar-gevent").filter({ hasText: "Dentist call" });
  await expect(work.locator(`[data-account="${WORK.id}"]`)).toHaveCount(1);
  await expect(home.locator(`[data-account="${HOME.id}"]`)).toHaveCount(1);
  // The sidebar's cards carry the same dots; a recording on no calendar has none.
  const cards = page.getByTestId("meeting-card");
  await expect(cards.filter({ hasText: "Dentist call" }).locator(`[data-account="${HOME.id}"]`)).toHaveCount(1);
  await expect(cards.filter({ hasText: "Hallway chat" }).getByTestId("account-dots")).toHaveCount(0);
});







test("add_another_calendar_starts_a_sign_in", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  let connects = 0;
  await page.route("**/api/calendar/connect", async (route) => {
    connects += 1;
    const status = await (await page.request.get("/api/calendar/status")).json();
    return route.fulfill({
      json: { ...status, state: "connecting", auth_url: "https://accounts.google.com/x", opened: true },
    });
  });
  await gotoSettings(page, "calendar");
  await page.getByTestId("calendar-add").click();
  await expect.poll(() => connects).toBe(1);
});

async function hide(page: import("@playwright/test").Page, id: string, visible = false) {
  await page.getByTestId("library-filter").click();
  const box = page.getByTestId(`library-filter-popover-${id}`).locator("input");
  if (visible) await box.check();
  else await box.uncheck();
  await page.keyboard.press("Escape");
}

test("unticking_a_calendar_in_the_filter_hides_it_everywhere", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await dayView(page);
  await expect(page.getByTestId("meeting-card")).toHaveCount(3);
  await hide(page, HOME.id);

  // The list and the calendar view both lose it...
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Dentist call" })).toHaveCount(0);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Hallway chat" })).toHaveCount(1);
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Dentist call" })).toHaveCount(0);
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Work planning" })).toHaveCount(1);
  await expect(page.getByTestId("library-filter")).toHaveAttribute("aria-expanded", "false");

  // ...and so do search and the meeting's own page.
  await gotoApp(page, "/search?q=Dentist");
  await expect(page.getByTestId("search-result")).toHaveCount(0);
  expect((await page.request.get("/api/meetings/m-home")).status()).toBe(404);

  // Ticked again, it is all back, after a reload too.
  await dayView(page);
  await hide(page, HOME.id, true);
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Dentist call" })).toHaveCount(1);
  await page.reload();
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Dentist call" })).toHaveCount(1);
});

test("a_meeting_on_both_calendars_stays_while_one_is_hidden", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    calendar_accounts: [WORK, HOME],
    calendar_events: [
      { id: "ev-a", account_id: WORK.id, ical_uid: "both@x", title: "Offsite", start: isoAt(0, 12), end: isoAt(0, 13) },
      { id: "ev-b", account_id: HOME.id, ical_uid: "both@x", title: "Offsite", start: isoAt(0, 12), end: isoAt(0, 13) },
    ],
    meetings: [
      {
        id: "m-both",
        title: "Offsite",
        state: "RENDERED",
        started_at: isoAt(0, 12, 1),
        calendar: { account_id: WORK.id, accounts: [WORK.id, HOME.id], calendar_id: "primary", event_id: "ev-a" },
      },
    ],
  });
  await dayView(page);
  const offsite = page.getByTestId("calendar-gevent").filter({ hasText: "Offsite" });
  await expect(offsite).toHaveCount(1); // one meeting, drawn once
  await expect(offsite.locator("[data-account]")).toHaveCount(2); // with both dots
  await hide(page, HOME.id);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Offsite" })).toHaveCount(1);
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Offsite" })).toHaveCount(1);
});

test("the_filter_is_the_only_switch", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await dayView(page);
  // No second switch in the calendar bar...
  await expect(page.getByTestId("calendar-accounts")).toHaveCount(0);
  // ...and none in Settings, which says where it is and still offers Remove.
  await gotoSettings(page, "calendar");
  await expect(page.getByTestId(`calendar-account-${WORK.id}`)).toContainText(WORK.address);
  await expect(page.locator('[data-testid^="calendar-account-visible-"]')).toHaveCount(0);
  await expect(page.getByTestId(`calendar-account-remove-${WORK.id}`)).toBeVisible();
});

test("a_calendar_hidden_in_the_filter_says_so_in_settings", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await gotoApp(page, "/");
  await hide(page, HOME.id);
  await gotoSettings(page, "calendar");
  await expect(page.getByTestId(`calendar-account-status-${HOME.id}`)).toContainText("Hidden");
});

test("removing_a_calendar_asks_first_and_then_it_is_gone", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await gotoSettings(page, "calendar");
  await page.getByTestId(`calendar-account-remove-${WORK.id}`).click();
  await page.getByTestId("confirm-ok").click();
  await expect(page.getByTestId(`calendar-account-${WORK.id}`)).toHaveCount(0);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Work planning" })).toHaveCount(0);
  await page.getByTestId("library-filter").click();
  await expect(page.getByTestId(`library-filter-popover-${WORK.id}`)).toHaveCount(0);
});
