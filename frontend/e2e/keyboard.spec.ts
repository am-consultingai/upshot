import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The keyboard model for a list beside a detail pane.
 *
 * Both j/k and arrows, unmodified: cmdk ships vim bindings on by default, Linear
 * documents "arrow/J-K", Gmail is j and k. Moving the selection swaps what the
 * detail shows; Enter hands focus to the detail rather than navigating to a thing
 * that is already on screen; Escape gives focus back to the list.
 */

/*
 * Seeded so the list reads kb-1, kb-2, kb-3 from top to bottom. The list is
 * newest first, so the *first* row is the most recent meeting — which is why
 * these timestamps count downwards.
 */
const THREE = [
  { id: "kb-1", title: "First meeting", state: "RENDERED", started_at: isoAt(0, 30) },
  { id: "kb-2", title: "Second meeting", state: "RENDERED", started_at: isoAt(0, 20) },
  { id: "kb-3", title: "Third meeting", state: "RENDERED", started_at: isoAt(0, 10) },
];

test("j_and_k_move_through_the_list", async ({ page, seed }) => {
  await seed(THREE);
  await gotoApp(page, "/m/kb-1");

  await page.getByTestId("meeting-list").focus();
  await page.keyboard.press("j");
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "kb-2");

  await page.keyboard.press("j");
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "kb-3");

  await page.keyboard.press("k");
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "kb-2");
});

test("arrows_do_the_same_thing", async ({ page, seed }) => {
  await seed(THREE);
  await gotoApp(page, "/m/kb-1");

  await page.getByTestId("meeting-list").focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "kb-2");
  await page.keyboard.press("ArrowUp");
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "kb-1");
});

test("it_stops_at_the_ends_rather_than_wrapping", async ({ page, seed }) => {
  await seed(THREE);
  await gotoApp(page, "/m/kb-1");
  await page.getByTestId("meeting-list").focus();

  // Wrapping a list that is ordered by time would jump a month in one keystroke.
  await page.keyboard.press("k");
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "kb-1");
});

test("enter_hands_focus_to_the_detail_and_escape_gives_it_back", async ({ page, seed }) => {
  await seed(THREE);
  await gotoApp(page, "/m/kb-1");

  const list = page.getByTestId("meeting-list");
  await list.focus();
  await page.keyboard.press("Enter");

  // The meeting was already showing; the only thing left to do with it is read it.
  expect(
    await page.evaluate(() => document.activeElement?.hasAttribute("data-detail-pane")),
  ).toBe(true);

  await page.keyboard.press("Escape");
  await expect(list).toBeFocused();
});

/**
 * Selection and focus are different states and WAI-ARIA requires them to look
 * different: the row stays selected while you read the transcript, but the ring
 * belongs to whatever actually has focus.
 */
test("the_selected_row_is_marked_whether_or_not_the_list_has_focus", async ({ page, seed }) => {
  await seed(THREE);
  await gotoApp(page, "/m/kb-2");

  const selected = page.locator('[data-testid="meeting-card"][aria-selected="true"]');
  await expect(selected).toHaveCount(1);
  await expect(selected).toHaveAttribute("data-meeting-id", "kb-2");

  await page.evaluate(() =>
    document.querySelector<HTMLElement>("[data-detail-pane]")?.focus(),
  );
  await expect(selected).toHaveCount(1);
});
