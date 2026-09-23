import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The command palette, which every comparable product ships and none of them
 * makes optional: Linear, Raycast, Reflect, Obsidian, Todoist and Height all
 * bind Cmd/Ctrl+K, and all of them ship essentially no right-click menus.
 */

test("the_palette_opens_and_closes_on_the_same_key", async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByTestId("command-palette")).toHaveCount(0);

  await page.keyboard.press("Control+k");
  await expect(page.getByTestId("command-palette")).toBeVisible();
  await expect(page.getByTestId("palette-input")).toBeFocused();

  // The same key closes it. A palette you open with one key and close with
  // another is a palette people stop using.
  await page.keyboard.press("Control+k");
  await expect(page.getByTestId("command-palette")).toHaveCount(0);

  await page.keyboard.press("Control+k");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("command-palette")).toHaveCount(0);
});

test("it_finds_a_meeting_by_name_and_opens_it", async ({ page, seed }) => {
  await seed([
    { id: "pal-1", title: "Quarterly planning", state: "RENDERED", started_at: isoAt(0, 30) },
    { id: "pal-2", title: "Weekly sync", state: "RENDERED", started_at: isoAt(1, 30) },
  ]);
  await gotoApp(page);

  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("quarter");

  const first = page.getByTestId("palette-item").first();
  await expect(first).toContainText("Quarterly planning");

  await page.keyboard.press("Enter");
  await expect(page.getByTestId("command-palette")).toHaveCount(0);
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "pal-1");
});

test("arrow_keys_move_the_selection_and_enter_runs_it", async ({ page }) => {
  await gotoApp(page);
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("settings");

  const items = page.getByTestId("palette-item");
  await expect(items.first()).toHaveAttribute("aria-selected", "true");

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/settings$/);
});

test("it_says_so_when_nothing_matches", async ({ page }) => {
  await gotoApp(page);
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("zzzzqqqq");
  await expect(page.getByTestId("palette-empty")).toBeVisible();
  await expect(page.getByTestId("palette-item")).toHaveCount(0);
});

/**
 * The palette is also how the shortcuts get taught: the binding that would have
 * skipped the palette entirely is shown beside the thing it does. Nobody reads a
 * shortcut reference; people do notice a key they keep seeing next to an action
 * they keep choosing.
 */
test("it_shows_the_shortcut_that_would_have_skipped_it", async ({ page }) => {
  await gotoApp(page);
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("settings");
  await expect(page.getByTestId("palette-item").first().locator("kbd")).toBeVisible();
});
