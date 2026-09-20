import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The inbox: what this application was missing.
 *
 * Seven well-written documents and no way to answer "what did I promise this week,
 * and to whom" is a filing cabinet. These specs cover the three things that make it
 * a system instead: reading across meetings, ticking in place, and the tick lasting.
 */

const WEEK = [
  {
    id: "e2e-funnel",
    title: "Onboarding funnel review",
    state: "RENDERED",
    started_at: isoAt(0, 9),
    summary_html: "<h2>Decisions</h2><p>Roll the interstitial back.</p>",
    action_items: [
      { who: "ME", what: "Take the rollback to Monday review", at_ms: 31000 },
      { who: "Dana", what: "Write the instrumentation spec", due: "Thursday" },
    ],
  },
  {
    id: "e2e-standup",
    title: "Product / Eng standup",
    state: "RENDERED",
    started_at: isoAt(1, 11),
    summary_html: "<p>Sprint 34 is on track.</p>",
    action_items: [
      { who: "ME", what: "Unblock the pricing page copy" },
      { who: "Yoni", what: "Finish the billing migration", done: true },
    ],
  },
];

test("the_inbox_reads_across_meetings_with_mine_first", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");

  await expect(page.getByTestId("actions-page")).toBeVisible();
  // Three open: two of mine and one of Dana's. Yoni's is already done.
  await expect(page.getByTestId("actions-open-count")).toContainText("3");

  const groups = page.getByTestId("action-group");
  await expect(groups).toHaveCount(2);
  // Mine first, whatever everyone else is called — Dana would sort before "me".
  await expect(groups.first()).toContainText("Me");
  await expect(groups.nth(1)).toContainText("Dana");

  // Each row says which meeting it came from, so nothing has to be opened to place it.
  const mine = groups.first().getByTestId("action-item");
  await expect(mine).toHaveCount(2);
  await expect(groups.nth(1)).toContainText("Thursday");
  await expect(groups.nth(1).getByTestId("action-meeting")).toHaveText(
    "Onboarding funnel review",
  );
});

test("ticking_an_item_survives_a_reload", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");

  const first = page.getByTestId("action-item").first();
  await first.getByTestId("action-toggle").check();
  await expect(page.getByTestId("actions-open-count")).toContainText("2");

  await page.reload();
  // Ticked items leave the open list rather than lingering greyed out.
  await expect(page.getByTestId("actions-open-count")).toContainText("2");
  await expect(page.getByTestId("action-item")).toHaveCount(2);

  // And they are still there under All, ticked.
  await page.getByTestId("actions-all").click();
  await expect(page.getByTestId("action-item")).toHaveCount(4);
  await expect(page.locator('[data-testid=action-item][data-done=true]')).toHaveCount(2);
});

test("the_meeting_page_and_the_inbox_tick_the_same_row", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/m/e2e-funnel");

  const onPage = page.getByTestId("meeting-actions").getByTestId("action-item");
  await expect(onPage).toHaveCount(2);
  await onPage.first().getByTestId("action-toggle").check();

  await gotoApp(page, "/actions");
  await expect(page.getByTestId("actions-open-count")).toContainText("2");
});

test("the_empty_detail_pane_shows_what_is_open", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/");

  // Half a screen that used to say "Choose a meeting to read it."
  const pane = page.getByTestId("no-meeting-open");
  await expect(pane.getByTestId("empty-open-count")).toContainText("3");
  await expect(pane.getByTestId("action-item").first()).toBeVisible();
});

test("an_empty_inbox_says_so_rather_than_showing_nothing", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: [] });
  await gotoApp(page, "/actions");
  await expect(page.getByTestId("actions-empty")).toBeVisible();
});
