import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, gotoSettings, isoAt, test, type SeedMeeting } from "./fixtures";

/**
 * The shell's keyboard: the palette, the meeting page's keys, and what teaches them.
 */

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

// The words matched here are English ones; a spec before this one may have left Hebrew on.
test.beforeEach(async ({ request }) => {
  await request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.language": "en" } } });
});

/** A keydown as a Hebrew layout sends it: the physical key's code, a Hebrew letter. */
async function hebrewKey(
  page: import("@playwright/test").Page,
  key: string,
  code: string,
  ctrlKey = false,
): Promise<void> {
  await page.evaluate(
    ([key, code, ctrlKey]) => {
      const target = document.activeElement ?? document.body;
      target.dispatchEvent(new KeyboardEvent("keydown", { key, code, ctrlKey, bubbles: true, cancelable: true }));
    },
    [key, code, ctrlKey] as const,
  );
}

// ------------------------------------------------------------------- hebrew layout

test("ctrl_k_and_g_sequences_work_with_the_keyboard_on_hebrew", async ({ page }) => {
  await gotoApp(page, "/");
  // On Hebrew, K types "ל": matching event.key missed it and the palette never opened.
  await hebrewKey(page, "ל", "KeyK", true);
  await expect(page.getByTestId("command-palette")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("command-palette")).toHaveCount(0);

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await hebrewKey(page, "ע", "KeyG");
  await hebrewKey(page, "ש", "KeyA");
  await expect(page.getByTestId("actions-page")).toBeVisible();
});

// ------------------------------------------------------------------- palette

test("every_palette_row_carries_an_icon", async ({ page }) => {
  await gotoApp(page, "/");
  await page.keyboard.press("Control+k");
  const items = page.getByTestId("palette-item");
  await expect(items.first()).toBeVisible();
  const count = await items.count();
  await expect(page.locator("[data-testid=palette-item] [data-testid=palette-icon]")).toHaveCount(count);
});

test("an_alias_match_says_which_word_found_it", async ({ page }) => {
  await gotoApp(page, "/");
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("theme");
  // Nothing called "theme" exists: Light, Dark and Match Windows answer to it.
  await expect(page.getByTestId("palette-alias").first()).toHaveText("(theme)");
  await expect(page.getByTestId("palette-item").first()).toContainText(/Light|Dark|Match Windows/);
});

test("the_detection_mode_can_be_switched_from_the_palette", async ({ page }) => {
  await gotoApp(page, "/");
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("record meetings automatically");
  await expect(page.getByTestId("palette-item").first()).toContainText("Record meetings automatically");
  await page.keyboard.press("Enter");
  await expect
    .poll(async () => (await (await page.request.get("/api/status")).json()).detector.mode)
    .toBe("on");

  // And back, the same way; the mode in force is marked.
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("detect and notify");
  await page.keyboard.press("Enter");
  await expect
    .poll(async () => (await (await page.request.get("/api/status")).json()).detector.mode)
    .toBe("shadow");
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("detect and notify");
  await expect(page.getByTestId("palette-item").first()).toContainText("current");
});

test("the_list_shows_a_cut_off_row_to_say_there_is_more", async ({ page }) => {
  await gotoApp(page, "/");
  await page.keyboard.press("Control+k");
  const list = page.locator("#palette-list");
  await expect(page.getByTestId("palette-item").first()).toBeVisible();
  const cut = await list.evaluate((box) => {
    const bottom = box.getBoundingClientRect().bottom;
    const rows = [...box.querySelectorAll("[data-testid=palette-item]")].map((row) => row.getBoundingClientRect());
    return {
      more: box.scrollHeight > box.clientHeight,
      whole: rows.filter((row) => row.bottom <= bottom).length,
      straddling: rows.some((row) => row.top < bottom && row.bottom > bottom),
    };
  });
  expect(cut.more).toBe(true);
  expect(cut.straddling).toBe(true);
  expect(cut.whole).toBeLessThanOrEqual(4);
});

test.describe("on a touch screen", () => {
  test.use({ hasTouch: true, isMobile: true });

  test("key_hints_are_hidden_where_there_is_no_keyboard", async ({ page }) => {
    await gotoApp(page, "/");
    await page.evaluate(() => window.dispatchEvent(new Event("upshot:palette")));
    await expect(page.getByTestId("palette-item").first()).toBeVisible();
    await page.getByTestId("palette-input").fill("settings");
    const hint = page.getByTestId("palette-item").first().locator("kbd");
    await expect(hint).toHaveCount(1);
    await expect(hint).toBeHidden();
  });
});

// ------------------------------------------------------------------- meeting keys

const THREE_LINES: SeedMeeting = {
  id: "e2e-keys",
  title: "Keyboard",
  state: "RENDERED",
  started_at: isoAt(0, 10),
  turns: [
    { speaker: "ME", at_ms: 1000, text: "first line" },
    { speaker: "THEM", at_ms: 3000, text: "second line" },
    { speaker: "ME", at_ms: 5000, text: "third line" },
  ],
  audio_seconds: 8,
};

const paused = (page: import("@playwright/test").Page) =>
  page.getByTestId("audio").evaluate((el) => (el as HTMLAudioElement).paused);

test("space_plays_and_pauses_from_either_pill", async ({ page, seed }) => {
  await seed([THREE_LINES]);
  await gotoApp(page, "/m/e2e-keys");
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  // From the summary, Space opens the transcript and its transport, and plays.
  await page.keyboard.press("Space");
  await expect(page.getByTestId("meeting-tab-transcript")).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => paused(page)).toBe(false);

  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("Space");
  await expect.poll(() => paused(page)).toBe(true);
});

test("j_and_k_step_between_lines_and_typing_is_not_a_shortcut", async ({ page, seed }) => {
  await seed([THREE_LINES]);
  await gotoApp(page, "/m/e2e-keys");
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const turns = page.getByTestId("transcript-turn");

  await page.keyboard.press("j");
  await expect(turns.nth(0)).toHaveAttribute("data-speaking", "true");
  await page.keyboard.press("j");
  await expect(turns.nth(1)).toHaveAttribute("data-speaking", "true");
  await page.keyboard.press("k");
  await expect(turns.nth(0)).toHaveAttribute("data-speaking", "true");

  // In the find box, j is a letter.
  await page.getByTestId("audio").evaluate((el) => (el as HTMLAudioElement).pause());
  const input = page.getByTestId("transcript-find-input");
  await input.click();
  await input.pressSequentially("j");
  await expect(input).toHaveValue("j");
  await expect(turns.nth(0)).toHaveAttribute("data-speaking", "true");
});

test("slash_finds_in_the_transcript_on_a_meeting", async ({ page, seed }) => {
  await seed([THREE_LINES]);
  await gotoApp(page, "/m/e2e-keys");
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("/");
  // Not the Search screen, which is what / means everywhere else.
  await expect(page.getByTestId("transcript-find-input")).toBeFocused();
  await expect(page).toHaveURL(/\/m\/e2e-keys$/);
  // Ctrl+F still does the same.
  await page.getByTestId("meeting-tab-summary").click();
  await page.keyboard.press("Control+f");
  await expect(page.getByTestId("transcript-find-input")).toBeFocused();
});

test("the_palette_teaches_the_meeting_keys", async ({ page, seed }) => {
  await seed([THREE_LINES]);
  await gotoApp(page, "/m/e2e-keys");
  await page.keyboard.press("Control+k");
  const group = page.locator("[data-testid=palette-group][data-group='palette.meeting']");
  await expect(group.getByTestId("palette-item")).toHaveCount(4);
  await expect(group.locator("kbd")).toHaveText(["Space", "J", "K", "/"]);
});

// ------------------------------------------------------------------- following playback

test("the_transcript_follows_playback_until_the_reader_scrolls_away", async ({ page, seed }) => {
  const turns = Array.from({ length: 60 }, (_, index) => ({
    speaker: index % 2 ? "THEM" : "ME",
    at_ms: index * 1000 + 500,
    text: `line number ${index} of a long meeting, with enough words to take up some room`,
  }));
  await seed([{ id: "e2e-follow", title: "Long", state: "RENDERED", started_at: isoAt(0, 10), turns, audio_seconds: 62 }]);
  await gotoApp(page, "/m/e2e-follow");
  await page.getByTestId("meeting-tab-transcript").click();
  await page.getByTestId("play-pause").click();
  await expect.poll(() => paused(page)).toBe(false);

  const audio = page.getByTestId("audio");
  const blocks = page.getByTestId("transcript-block");
  await audio.evaluate((el) => ((el as HTMLAudioElement).currentTime = 50.6));
  await expect(blocks.nth(50)).toBeInViewport();

  // The reader scrolls: playback stops pulling the page.
  await blocks.nth(50).hover();
  await page.mouse.wheel(0, -200);
  await audio.evaluate((el) => ((el as HTMLAudioElement).currentTime = 5.6));
  await page.waitForTimeout(800);
  await expect(blocks.nth(5)).not.toBeInViewport();

  // A click on a line hands it back.
  await page.getByTestId("transcript-turn").nth(48).click();
  await audio.evaluate((el) => ((el as HTMLAudioElement).currentTime = 5.6));
  await expect(blocks.nth(5)).toBeInViewport();
});

// ------------------------------------------------------------------- settings

test("each_settings_section_says_what_it_is_for", async ({ page }) => {
  await gotoSettings(page, "audio");
  await expect(page.getByTestId("settings-purpose")).not.toBeEmpty();
  await page.getByTestId("settings-section-prompt").click();
  await expect(page.getByTestId("settings-purpose")).toContainText("instructions");
});

test("storage_shows_the_folder_its_size_and_that_recordings_stay", async ({ page }) => {
  await gotoSettings(page, "storage");
  await expect(page.getByTestId("storage-folder")).not.toBeEmpty();
  await expect(page.getByTestId("storage-size")).not.toBeEmpty();
  await expect(page.getByTestId("settings-page")).toContainText("Recordings are kept until you delete them.");
});

test("the_prompt_editor_counts_and_shows_what_changed", async ({ page }) => {
  await gotoSettings(page, "prompt");
  const box = page.getByTestId("prompt-text");
  await expect(box).toBeVisible();
  const before = await box.inputValue();
  await expect(page.getByTestId("prompt-count")).toContainText(`${before.length.toLocaleString("en")} characters`);

  await box.fill(`${before}\nAlways end with the risks.`);
  const added = page.locator("[data-testid=prompt-diff-line][data-kind=add]");
  await expect(added.filter({ hasText: "Always end with the risks." })).toHaveCount(1);
  // Unchanged lines far from the edit are folded away rather than repeated.
  expect(await page.getByTestId("prompt-diff-line").count()).toBeLessThan(before.split("\n").length);
});
