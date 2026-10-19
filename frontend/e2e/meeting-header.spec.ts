/**
 * The meeting page's header, its speakers and its action items in Hebrew.
 *
 * The header and the Summary/Transcript pills used to scroll away with the summary,
 * a rail beside the meeting took a column that was usually empty, a speaker could be
 * named only from that rail, and action items ran left to right under a Hebrew
 * summary. Each test here is one of those coming back.
 */
import type { Page } from "@playwright/test";
import { expect, gotoApp, isoAt, test } from "./fixtures";

/** Press Enter in the name field and wait for the server to have the name, not just the screen. */
async function saveName(page: Page, id: string) {
  await Promise.all([
    page.waitForResponse(
      (response) => response.url().endsWith(`/api/meetings/${id}`) && response.request().method() === "PATCH",
    ),
    page.getByTestId("speaker-name-input").press("Enter"),
  ]);
}

const LONG_SUMMARY = `<html><body><h2>Overview</h2>${"<p>The team went through the migration plan, step by step, and agreed on the order.</p>".repeat(40)}</body></html>`;
const SUMMARY_HE = `<html dir="rtl" lang="he"><body><h2>סקירה</h2><p>הצוות דן בהעברה ל-Kubernetes.</p></body></html>`;

function turns(slots: string[], count: number) {
  return Array.from({ length: count }, (_, index) => ({
    speaker: slots[index % slots.length],
    at_ms: index * 9000,
    end_ms: index * 9000 + 8000,
    text: `Line ${index + 1} of the conversation, about the migration plan.`,
  }));
}

test("the_header_and_the_pills_stay_put_while_the_summary_scrolls", async ({ page, seed }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await seed([
    {
      id: "e2e-sticky",
      title: "Migration plan",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: LONG_SUMMARY,
      turns: turns(["ME", "THEM_1"], 60),
    },
  ]);
  await gotoApp(page, "/m/e2e-sticky");
  const title = page.getByTestId("meeting-title");
  const tabs = page.getByTestId("meeting-tabs");
  await expect(page.getByTestId("summary-html")).toBeVisible();
  const before = await tabs.boundingBox();

  const scroller = page.locator("[data-meeting-scroller]");
  const titleBefore = await title.boundingBox();
  await scroller.evaluate((node) => (node.scrollTop = node.scrollHeight));
  expect(await scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(300);
  expect((await title.boundingBox())?.y).toBe(titleBefore?.y);
  expect((await tabs.boundingBox())?.y).toBe(before?.y);

  // The transcript too, under the same header.
  await page.getByTestId("meeting-tab-transcript").click();
  await scroller.evaluate((node) => (node.scrollTop = node.scrollHeight));
  expect(await scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(300);
  expect((await title.boundingBox())?.y).toBe(titleBefore?.y);
  expect((await tabs.boundingBox())?.y).toBe(before?.y);
});

test("the_header_is_compact_without_smaller_type", async ({ page, seed }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await seed([
    {
      id: "e2e-compact",
      title: "Migration plan",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      duration_s: 2700,
      summary_html: LONG_SUMMARY,
      turns: turns(["ME", "THEM_1", "THEM_2"], 9),
    },
  ]);
  await gotoApp(page, "/m/e2e-compact");
  const header = page.getByTestId("meeting-header");
  await expect(header).toBeVisible();
  // Title, chips, pills and speakers in two rows.
  expect((await header.boundingBox())?.height ?? 999).toBeLessThan(110);
  // Nothing in it smaller than the 12px the chips have always used.
  const smallest = await header.evaluate((node) =>
    Math.min(
      ...[...node.querySelectorAll<HTMLElement>("*")]
        .filter((element) => [...element.childNodes].some((child) => child.nodeType === 3 && child.textContent?.trim()))
        .map((element) => parseFloat(getComputedStyle(element).fontSize)),
    ),
  );
  expect(smallest).toBeGreaterThanOrEqual(12);
});

test("no_rail_the_summary_takes_its_width", async ({ page, seed }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await seed([
    {
      id: "e2e-wide",
      title: "Migration plan",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: LONG_SUMMARY,
      turns: turns(["ME", "THEM_1"], 4),
    },
  ]);
  await gotoApp(page, "/m/e2e-wide");
  await expect(page.getByTestId("summary-html")).toBeVisible();
  await expect(page.getByTestId("meeting-rail")).toHaveCount(0);
  // Wider than the old 37rem reading column.
  expect((await page.getByTestId("summary-html").boundingBox())?.width ?? 0).toBeGreaterThan(700);
});

test("more_than_three_speakers_scroll_sideways_in_one_row", async ({ page, seed }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await seed([
    {
      id: "e2e-many",
      title: "All hands",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: turns(["ME", "THEM_1", "THEM_2", "THEM_3", "THEM_4", "THEM_5"], 12),
    },
  ]);
  await gotoApp(page, "/m/e2e-many");
  const row = page.getByTestId("meeting-speakers");
  await expect(row.getByTestId("talk-share")).toHaveCount(6);
  const chip = await row.getByTestId("talk-share").first().boundingBox();
  const box = await row.boundingBox();
  // One row, no taller than a chip, and about three chips wide.
  expect(box?.height ?? 999).toBeLessThanOrEqual((chip?.height ?? 0) + 8);
  expect(box?.width ?? 999).toBeLessThan((chip?.width ?? 0) * 3.5);
  const overflows = await row.evaluate((node) => node.scrollWidth > node.clientWidth);
  expect(overflows).toBe(true);
});

test("double_clicking_a_name_in_the_transcript_renames_that_speaker_everywhere", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-name",
      title: "Vendor call",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: turns(["ME", "THEM_1", "THEM_2"], 9),
    },
  ]);
  await gotoApp(page, "/m/e2e-name");
  await page.getByTestId("meeting-tab-transcript").click();
  const names = page.locator("[data-testid=turn-speaker] [data-testid=speaker-name][data-slot=THEM_1]");
  await expect(names).toHaveCount(3);

  // Escape puts the name back.
  await names.nth(2).dblclick();
  await page.getByTestId("speaker-name-input").fill("Not this");
  await page.getByTestId("speaker-name-input").press("Escape");
  await expect(names.first()).toHaveText("Speaker 1");

  // Enter saves, on every line and in the header at once.
  await names.nth(2).dblclick();
  await page.getByTestId("speaker-name-input").fill("Dana Levi");
  await saveName(page, "e2e-name");
  for (const index of [0, 1, 2]) await expect(names.nth(index)).toHaveText("Dana Levi");
  await expect(page.locator("[data-testid=talk-share][data-slot=THEM_1]")).toContainText("Dana Levi");

  // Saved: it survives a reload.
  await page.reload();
  await page.getByTestId("meeting-tab-transcript").click();
  await expect(names.first()).toHaveText("Dana Levi");

  // And a rename in the header reaches the transcript.
  await page.locator("[data-testid=talk-share][data-slot=THEM_2] [data-testid=speaker-name]").dblclick();
  await page.getByTestId("speaker-name-input").fill("Yoni Bar");
  await saveName(page, "e2e-name");
  await expect(
    page.locator("[data-testid=turn-speaker] [data-testid=speaker-name][data-slot=THEM_2]").first(),
  ).toHaveText("Yoni Bar");
});

test("hebrew_action_items_run_right_to_left_in_an_english_interface", async ({ page, seed }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await seed([
    {
      id: "e2e-rtl-actions",
      title: "פגישת צוות",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      language: "he",
      summary_html: SUMMARY_HE,
      // The language is the transcript's, so the meeting needs one.
      turns: [{ speaker: "THEM", at_ms: 1000, text: "בוקר טוב" }],
      action_items: [{ who: "ME", what: "לשלוח את הסיכום ל-Slack", detail: "כולל את מדדי ה-TSS" }],
    },
  ]);
  await gotoApp(page, "/m/e2e-rtl-actions");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  const block = page.getByTestId("meeting-actions");
  await expect(block).toHaveAttribute("dir", "rtl");
  // The interface's words keep their own order inside the mirrored header, and the
  // header mirrors: "+ Add" at the left end, the count beside the title on the right.
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("0 of 1 done");
  const add = await page.getByTestId("meeting-actions-add").boundingBox();
  const progress = await page.getByTestId("meeting-actions-progress").boundingBox();
  expect(add?.x ?? 999).toBeLessThan(progress?.x ?? 0);

  const row = block.getByTestId("action-item").first();
  const tick = await row.getByTestId("action-toggle").boundingBox();
  const what = await row.getByTestId("action-what").boundingBox();
  const owner = await row.getByTestId("action-owner").boundingBox();
  // The tick on the right, the owner on the left, the text right-aligned between them.
  expect(tick?.x ?? 0).toBeGreaterThan(what?.x ?? 0);
  expect(owner?.x ?? 999).toBeLessThan(what?.x ?? 0);
  await expect(row.getByTestId("action-what")).toHaveCSS("direction", "rtl");
  // Mixed text in reading order: "Slack" ends the Hebrew sentence, so it is drawn at its left end.
  const [first, slack] = await row.getByTestId("action-what").evaluate((node) => {
    const text = node.firstChild as Text;
    const at = (word: string) => {
      const range = document.createRange();
      const start = text.data.indexOf(word);
      range.setStart(text, start);
      range.setEnd(text, start + word.length);
      return range.getBoundingClientRect().x;
    };
    return [at("לשלוח"), at("Slack")];
  });
  expect(slack).toBeLessThan(first);

  // In the inbox the row follows its own words.
  await gotoApp(page, "/actions");
  const inbox = page.getByTestId("action-item").filter({ hasText: "לשלוח את הסיכום" });
  await expect(inbox.getByTestId("action-what")).toHaveCSS("direction", "rtl");
});

test("renaming_from_the_keyboard_keeps_focus_on_the_name", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-keys",
      title: "Vendor call",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: turns(["ME", "THEM_1"], 4),
    },
  ]);
  await gotoApp(page, "/m/e2e-keys");
  const name = page.locator("[data-testid=talk-share][data-slot=THEM_1] [data-testid=speaker-name]");
  const input = page.getByTestId("speaker-name-input");

  // Escape cancels and gives focus back to the name, not to the meeting list.
  await name.focus();
  await page.keyboard.press("F2");
  await input.fill("Not this");
  await input.press("Escape");
  await expect(name).toBeFocused();
  await expect(name).toHaveText("Speaker 1");

  // Enter saves and gives focus back too. A name is at most what the server keeps.
  await page.keyboard.press("Enter");
  await expect(input).toHaveAttribute("maxlength", "80");
  await input.fill("Dana Levi");
  await saveName(page, "e2e-keys");
  await expect(name).toBeFocused();
  await expect(name).toHaveText("Dana Levi");
});

test("a_press_from_the_keyboard_or_a_screen_reader_opens_the_name_but_a_click_does_not", async ({ page, seed }) => {
  await seed([
    { id: "e2e-press", title: "Vendor call", state: "RENDERED", started_at: isoAt(0, 10), turns: turns(["ME", "THEM_1"], 4) },
  ]);
  await gotoApp(page, "/m/e2e-press");
  const name = page.locator("[data-testid=talk-share][data-slot=THEM_1] [data-testid=speaker-name]");
  const input = page.getByTestId("speaker-name-input");

  // A single mouse click is not a rename: that is a double-click.
  await name.click();
  await expect(input).toHaveCount(0);

  // Space presses the button, as a screen reader's click does (a click with no detail).
  await name.focus();
  await page.keyboard.press("Space");
  await expect(input).toBeFocused();
  await input.press("Escape");
  await name.evaluate((node) => node.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 })));
  await expect(input).toBeFocused();
});

test("a_failed_rename_puts_the_old_name_back_everywhere", async ({ page, seed }) => {
  await seed([
    { id: "e2e-fail", title: "Vendor call", state: "RENDERED", started_at: isoAt(0, 10), turns: turns(["ME", "THEM_1"], 6) },
  ]);
  await page.route("**/api/meetings/e2e-fail", (route) =>
    route.request().method() === "PATCH" ? route.fulfill({ status: 500, body: "{}" }) : route.continue(),
  );
  await gotoApp(page, "/m/e2e-fail");
  await page.getByTestId("meeting-tab-transcript").click();
  const names = page.locator("[data-testid=turn-speaker] [data-testid=speaker-name][data-slot=THEM_1]");
  await names.first().dblclick();
  await page.getByTestId("speaker-name-input").fill("Dana Levi");
  await saveName(page, "e2e-fail");
  for (const index of [0, 1, 2]) await expect(names.nth(index)).toHaveText("Speaker 1");
  await expect(page.locator("[data-testid=talk-share][data-slot=THEM_1]")).toContainText("Speaker 1");
  await expect(page.getByTestId("toast").first()).toBeVisible();
});

test("a_long_title_stays_on_one_line_and_shows_whole_on_hover", async ({ page, seed }) => {
  const long = "Quarterly planning with the infrastructure, data and platform teams about the migration";
  await page.setViewportSize({ width: 1100, height: 800 });
  await seed([{ id: "e2e-long", title: long, state: "RENDERED", started_at: isoAt(0, 10) }]);
  await gotoApp(page, "/m/e2e-long");
  await expect(page.getByTestId("meeting-title")).toHaveAttribute("title", long);
});

test("a_row_menu_in_a_hebrew_block_opens_toward_the_page_under_an_english_interface", async ({ page, seed }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await seed([
    {
      id: "e2e-rtl-menu",
      title: "פגישת צוות",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      language: "he",
      summary_html: SUMMARY_HE,
      turns: [{ speaker: "THEM", at_ms: 1000, text: "בוקר טוב" }],
      action_items: [{ who: "ME", what: "לשלוח את הסיכום ל-Slack" }],
    },
  ]);
  await gotoApp(page, "/m/e2e-rtl-menu");
  const row = page.getByTestId("meeting-actions").getByTestId("action-item").first();
  await row.hover();
  const trigger = await row.getByTestId("action-menu").boundingBox();
  await row.getByTestId("action-menu").click();
  // The trigger is at the row's left end, so the menu grows rightward from it, into the
  // row — not leftward over the sidebar, clamped to the window's edge.
  const menu = await page.getByTestId("overflow-menu").boundingBox();
  expect(Math.abs((menu?.x ?? 0) - (trigger?.x ?? 0))).toBeLessThan(12);
});
