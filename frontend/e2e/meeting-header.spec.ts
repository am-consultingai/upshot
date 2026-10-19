/**
 * The meeting page's header, its speakers and its action items in Hebrew.
 *
 * The header and the Summary/Transcript pills used to scroll away with the summary,
 * a rail beside the meeting took a column that was usually empty, a speaker could be
 * named only from that rail, and action items ran left to right under a Hebrew
 * summary. Each test here is one of those coming back.
 */
import { expect, gotoApp, isoAt, test } from "./fixtures";

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
  await scroller.evaluate((node) => (node.scrollTop = node.scrollHeight));
  await expect(title).toBeInViewport();
  expect((await tabs.boundingBox())?.y).toBe(before?.y);

  // The transcript too, under the same header.
  await page.getByTestId("meeting-tab-transcript").click();
  await scroller.evaluate((node) => (node.scrollTop = node.scrollHeight));
  await expect(title).toBeInViewport();
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
  await page.getByTestId("speaker-name-input").press("Enter");
  for (const index of [0, 1, 2]) await expect(names.nth(index)).toHaveText("Dana Levi");
  await expect(page.locator("[data-testid=talk-share][data-slot=THEM_1]")).toContainText("Dana Levi");

  // Saved: it survives a reload.
  await page.reload();
  await page.getByTestId("meeting-tab-transcript").click();
  await expect(names.first()).toHaveText("Dana Levi");

  // And a rename in the header reaches the transcript.
  await page.locator("[data-testid=talk-share][data-slot=THEM_2] [data-testid=speaker-name]").dblclick();
  await page.getByTestId("speaker-name-input").fill("Yoni Bar");
  await page.getByTestId("speaker-name-input").press("Enter");
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
  // The interface's words keep their own order inside the mirrored header.
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("0 of 1 done");

  const row = block.getByTestId("action-item").first();
  const tick = await row.getByTestId("action-toggle").boundingBox();
  const what = await row.getByTestId("action-what").boundingBox();
  const owner = await row.getByTestId("action-owner").boundingBox();
  // The tick on the right, the owner on the left, the text right-aligned between them.
  expect(tick?.x ?? 0).toBeGreaterThan(what?.x ?? 0);
  expect(owner?.x ?? 999).toBeLessThan(what?.x ?? 0);
  await expect(row.getByTestId("action-what")).toHaveCSS("direction", "rtl");

  // In the inbox the row follows its own words.
  await gotoApp(page, "/actions");
  const inbox = page.getByTestId("action-item").filter({ hasText: "לשלוח את הסיכום" });
  await expect(inbox.getByTestId("action-what")).toHaveCSS("direction", "rtl");
});
