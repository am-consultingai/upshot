import { dateFromToday, expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The inbox: what this application was missing.
 *
 * Seven well-written documents and no way to answer "what did I promise this week,
 * and to whom" is a filing cabinet. These specs cover what makes it a system instead:
 * reading across meetings, grouping by when things are due, ticking and snoozing in
 * place, and the tick lasting.
 */

const WEEK = [
  {
    id: "e2e-funnel",
    title: "Onboarding funnel review",
    state: "RENDERED",
    started_at: isoAt(0, 9),
    summary_html: "<h2>Decisions</h2><p>Roll the interstitial back.</p>",
    action_items: [
      {
        who: "ME",
        what: "Take the rollback to Monday review",
        detail: "Design pushed back on the modal pattern",
        at_ms: 31000,
        due_at: dateFromToday(-2),
      },
      { who: "Dana", what: "Write the instrumentation spec", due: "Thursday", due_at: dateFromToday(1) },
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

test("the_inbox_reads_across_meetings_grouped_by_when_it_is_due", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");

  await expect(page.getByTestId("actions-page")).toBeVisible();
  // Mine: two open. Everyone: three open. Done: Yoni's.
  await expect(page.getByTestId("actions-count-mine")).toHaveText("2");
  await expect(page.getByTestId("actions-count-everyone")).toHaveText("3");
  await expect(page.getByTestId("actions-count-done")).toHaveText("1");

  // Mine, by when: the overdue one first, the undated one last, empty groups not drawn.
  const groups = page.getByTestId("action-group");
  await expect(groups).toHaveCount(2);
  await expect(groups.nth(0)).toHaveAttribute("data-bucket-group", "overdue");
  await expect(groups.nth(1)).toHaveAttribute("data-bucket-group", "none");

  // The late one says how late, in red, and carries its why on the second line.
  const late = groups.nth(0).getByTestId("action-item");
  await expect(late.getByTestId("action-due")).toHaveText("2 days late");
  await expect(late.getByTestId("action-due")).toHaveAttribute("data-late", "true");
  await expect(late.getByTestId("action-detail")).toHaveText("Design pushed back on the modal pattern");
  await expect(late.getByTestId("action-meeting")).toHaveText("Onboarding funnel review");

  // Everyone adds Dana's, due tomorrow — this week unless tomorrow is next week.
  await page.getByTestId("actions-tab-everyone").click();
  await expect(page.getByTestId("action-item")).toHaveCount(3);
  await expect(page.locator("[data-testid=action-item]", { hasText: "instrumentation" }).getByTestId("action-due")).toHaveText(
    "Tomorrow",
  );

  // Done is the record.
  await page.getByTestId("actions-tab-done").click();
  await expect(page.getByTestId("action-item")).toHaveCount(1);
  await expect(page.getByTestId("action-item")).toHaveAttribute("data-done", "true");
});

test("ticking_an_item_survives_a_reload", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");

  // Clicked rather than check()ed: a ticked row leaves the open list at once, so there
  // is no checkbox left behind for check() to confirm.
  const first = page.getByTestId("action-item").first();
  await first.getByTestId("action-toggle").click();
  await expect(page.getByTestId("actions-count-mine")).toHaveText("1");

  await page.reload();
  // Ticked items leave the open list rather than lingering greyed out.
  await expect(page.getByTestId("actions-count-mine")).toHaveText("1");
  await expect(page.getByTestId("action-item")).toHaveCount(1);

  // And they are in Done, ticked.
  await page.getByTestId("actions-tab-done").click();
  await expect(page.getByTestId("action-item")).toHaveCount(2);
  await expect(page.locator("[data-testid=action-item][data-done=true]")).toHaveCount(2);
});

test("the_meeting_page_and_the_inbox_tick_the_same_row", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/m/e2e-funnel");

  const onPage = page.getByTestId("meeting-actions").getByTestId("action-item");
  await expect(onPage).toHaveCount(2);
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("0 of 2 done");
  await onPage.first().getByTestId("action-toggle").check();
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("1 of 2 done");

  await gotoApp(page, "/actions");
  await expect(page.getByTestId("actions-count-everyone")).toHaveText("2");
});

test("the_keyboard_moves_ticks_and_snoozes", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");
  await page.getByTestId("actions-tab-everyone").click();
  const rows = page.getByTestId("action-item");
  await expect(rows).toHaveCount(3);

  // The first row starts selected; J moves down, K back up.
  await expect(rows.nth(0)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("j");
  await expect(rows.nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("k");
  await expect(rows.nth(0)).toHaveAttribute("aria-selected", "true");

  // H snoozes the selected one until tomorrow, with a toast that can take it back.
  const snoozed = await rows.nth(0).getByTestId("action-what").textContent();
  await page.keyboard.press("h");
  await expect(page.getByTestId("toast")).toContainText("Snoozed until Tomorrow");
  await expect(rows).toHaveCount(2);
  await expect(page.getByTestId("actions-snoozed")).toContainText("1");
  await page.getByTestId("toast-action").click();
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0).getByTestId("action-what")).toHaveText(snoozed ?? "");

  // X ticks the selected one, and it leaves the open list.
  await page.keyboard.press("x");
  await expect(rows).toHaveCount(2);
  await expect(page.getByTestId("actions-count-done")).toHaveText("2");
});

test("a_date_picked_for_an_undated_item_moves_it_into_its_group", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");

  const undated = page.locator("[data-bucket-group=none] [data-testid=action-item]");
  await expect(undated).toHaveCount(1);
  await undated.hover();
  await undated.getByTestId("action-add-date").click();
  await expect(page.getByTestId("date-picker")).toBeVisible();
  // Yesterday, which makes it overdue.
  await page.locator(`[data-testid=date-picker-day][data-day="${dateFromToday(-1)}"]`).click();
  await expect(page.getByTestId("date-picker")).toHaveCount(0);
  await expect(page.locator("[data-bucket-group=none]")).toHaveCount(0);
  await expect(page.locator("[data-bucket-group=overdue] [data-testid=action-item]")).toHaveCount(2);

  // It survives a reload: the date is stored, not just drawn.
  await page.reload();
  await expect(page.locator("[data-bucket-group=overdue] [data-testid=action-item]")).toHaveCount(2);
});

test("the_rail_says_who_is_owed_and_what_was_cleared", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/actions");
  await expect(page.getByTestId("actions-sources")).toContainText("Onboarding funnel review");
  await expect(page.getByTestId("actions-waiting-person")).toHaveCount(1);
  await expect(page.getByTestId("actions-waiting-person")).toContainText("Dana");
  // Yoni's, ticked by the seed just now, counts as cleared this week.
  await expect(page.getByTestId("actions-cleared-count")).toHaveText("1");
});

test("the_detail_side_is_the_calendar_when_nothing_is_open", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: WEEK });
  await gotoApp(page, "/");

  // Half a screen that once said "Choose a meeting to read it.", and then held a
  // trimmed copy of the inbox. It is the calendar; the inbox is one click down the rail.
  await expect(page.getByTestId("calendar-controls")).toBeVisible();
  await expect(page.getByTestId("no-meeting-open")).toHaveCount(0);
});

test("an_empty_inbox_says_so_rather_than_showing_nothing", async ({ page, seedBody }) => {
  await seedBody({ reset: true, meetings: [] });
  await gotoApp(page, "/actions");
  await expect(page.getByTestId("actions-empty")).toBeVisible();
});

/**
 * What a meeting still owes, in the list, without opening it.
 *
 * Zero open is deliberately not the same as none recorded: a meeting that has
 * discharged its commitments says "done", and one that never made any says nothing.
 */
test("the_timeline_says_what_each_meeting_still_owes", async ({ page, seedBody }) => {
  await seedBody({
    reset: true,
    meetings: [
      {
        id: "e2e-owing",
        title: "Two still open",
        state: "RENDERED",
        started_at: isoAt(0, 9),
        action_items: [
          { who: "ME", what: "Take the rollback to Monday review" },
          { who: "Dana", what: "Write the instrumentation spec" },
          { who: "Yoni", what: "Send the deck", done: true },
        ],
      },
      {
        id: "e2e-cleared",
        title: "All done",
        state: "RENDERED",
        started_at: isoAt(0, 8),
        action_items: [{ who: "ME", what: "Unblock the pricing page copy", done: true }],
      },
      {
        id: "e2e-silent",
        title: "Nothing was promised",
        state: "RENDERED",
        started_at: isoAt(0, 7),
      },
    ],
  });
  await gotoApp(page, "/");

  const card = (id: string) => page.locator(`[data-testid=meeting-card][data-meeting-id="${id}"]`);

  const owing = card("e2e-owing").getByTestId("meeting-actions-badge");
  await expect(owing).toHaveText("2 items");
  await expect(owing).toHaveAttribute("data-cleared", "false");

  const cleared = card("e2e-cleared").getByTestId("meeting-actions-badge");
  await expect(cleared).toHaveText("done");
  await expect(cleared).toHaveAttribute("data-cleared", "true");
  await expect(cleared).toHaveAttribute("data-open", "0");

  // A meeting that recorded no commitments says nothing about them.
  await expect(card("e2e-silent").getByTestId("meeting-actions-badge")).toHaveCount(0);

  // And ticking the last open one off turns its row to "done" without a reload.
  await gotoApp(page, "/m/e2e-owing");
  const rows = page.getByTestId("action-item");
  await rows.nth(0).getByTestId("action-toggle").click();
  await rows.nth(1).getByTestId("action-toggle").click();
  await expect(card("e2e-owing").getByTestId("meeting-actions-badge")).toHaveAttribute(
    "data-cleared",
    "true",
  );
});
