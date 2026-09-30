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

test("hiding_an_account_hides_its_events_and_its_recordings_everywhere", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await dayView(page);
  await page.getByTestId("calendar-accounts").click();
  const popover = page.getByTestId("calendar-accounts-popover");
  await expect(popover).toBeVisible();
  await popover.getByTestId(`calendar-accounts-popover-${HOME.id}`).locator("input").uncheck();

  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Dentist call" })).toHaveCount(0);
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Work planning" })).toHaveCount(1);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Dentist call" })).toHaveCount(0);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Hallway chat" })).toHaveCount(1);
  await page.keyboard.press("Escape");

  // Search finds nothing of it, and its page is not there.
  await gotoApp(page, "/search?q=Dentist");
  await expect(page.getByTestId("search-result")).toHaveCount(0);
  const answer = await page.request.get("/api/meetings/m-home");
  expect(answer.status()).toBe(404);

  // Shown again, it is all back.
  await dayView(page);
  await page.getByTestId("calendar-accounts").click();
  await page.getByTestId(`calendar-accounts-popover-${HOME.id}`).locator("input").check();
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Dentist call" })).toHaveCount(1);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Dentist call" })).toHaveCount(1);
});

test("a_meeting_on_both_accounts_stays_while_one_is_hidden", async ({ page, seedBody }) => {
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
  await page.getByTestId("calendar-accounts").click();
  await page.getByTestId(`calendar-accounts-popover-${HOME.id}`).locator("input").uncheck();
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Offsite" })).toHaveCount(1);
});

test("the_calendars_button_appears_only_with_two_accounts", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    calendar_accounts: [WORK],
    calendar_events: [{ id: "ev", title: "Solo", start: isoAt(0, 10), end: isoAt(0, 11) }],
  });
  await dayView(page);
  await expect(page.getByTestId("calendar-accounts")).toHaveCount(0);
  await expect(page.getByTestId("account-dots")).toHaveCount(0);
});

test("shortcuts_pause_while_the_popover_is_open", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await dayView(page);
  await page.getByTestId("calendar-accounts").click();
  await page.keyboard.press("m");
  await expect(page.getByTestId("span-day")).toHaveAttribute("aria-pressed", "true");
});

test("the_library_filter_narrows_the_list_and_survives_a_reload", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await gotoApp(page, "/");
  const cards = page.getByTestId("meeting-card");
  await expect(cards).toHaveCount(3);
  await page.getByTestId("library-filter").click();
  const popover = page.getByTestId("library-filter-popover");
  await popover.getByTestId(`library-filter-popover-${WORK.id}`).locator("input").uncheck();
  await popover.getByTestId("library-filter-popover-none").locator("input").uncheck();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Dentist call");
  await page.keyboard.press("Escape");

  await page.reload();
  await expect(page.getByTestId("meeting-card")).toHaveCount(1);
  // The filter is the list's only: the calendar still shows both accounts' events.
  await page.getByTestId("span-day").click();
  await expect(page.getByTestId("calendar-gevent")).toHaveCount(2);
});

test("settings_lists_the_accounts_hides_and_removes", async ({ page, seedBody }) => {
  await twoAccounts(seedBody);
  await gotoSettings(page, "calendar");
  await expect(page.getByTestId(`calendar-account-${WORK.id}`)).toContainText(WORK.address);
  await expect(page.getByTestId(`calendar-account-${HOME.id}`)).toContainText(HOME.address);
  await expect(page.getByTestId("calendar-add")).toBeVisible();

  // Hide from Settings: the same switch as the calendar's.
  await page.getByTestId(`calendar-account-visible-${HOME.id}`).uncheck();
  await expect(page.getByTestId(`calendar-account-status-${HOME.id}`)).toContainText("Hidden");
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Dentist call" })).toHaveCount(0);
  await page.getByTestId(`calendar-account-visible-${HOME.id}`).check();

  // Remove asks first, then the account is not listed; its recording is hidden.
  await page.getByTestId(`calendar-account-remove-${WORK.id}`).click();
  await page.getByTestId("confirm-ok").click();
  await expect(page.getByTestId(`calendar-account-${WORK.id}`)).toHaveCount(0);
  await expect(page.getByTestId("meeting-card").filter({ hasText: "Work planning" })).toHaveCount(0);
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
