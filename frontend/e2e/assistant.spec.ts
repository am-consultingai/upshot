import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The assistant (D61, D62), end to end: the panel, the chat route, the CLI and the MCP
 * tool server. The CLI is tests/fixtures/fake_claude.py, which makes a real MCP call to
 * this server and streams recorded stream-json; the model is the only thing faked.
 */

const BUDGET = [
  {
    id: "as-1",
    title: "Q4 budget review",
    state: "RENDERED",
    started_at: isoAt(0, 30),
    turns: [{ speaker: "THEM", at_ms: 4000, text: "The marketing budget is cut by ten percent." }],
  },
];

test("ctrl_j_opens_the_assistant_and_a_question_is_answered_from_a_real_search", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await expect(page.getByTestId("assistant-panel")).toHaveCount(0);

  await page.keyboard.press("Control+j");
  const panel = page.getByTestId("assistant-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("assistant-input")).toBeFocused();
  await expect(page.getByTestId("assistant-empty")).toBeVisible();

  await page.getByTestId("assistant-input").fill("Which meetings mention the budget");
  await page.keyboard.press("Enter");

  const tool = page.getByTestId("assistant-tool");
  await expect(tool).toHaveAttribute("data-state", "output-available");
  await expect(tool).toContainText("budget");
  await expect(page.getByTestId("assistant-answer")).toContainText("Q4 budget review");
  await expect(page.getByTestId("assistant-send")).toBeVisible();
});

test("a_follow_up_continues_the_conversation_and_new_chat_starts_over", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  const input = page.getByTestId("assistant-input");
  await input.fill("Which meetings mention the budget");
  await input.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("Q4 budget review");

  await input.fill("NOTOOL and the first one?");
  await input.press("Enter");
  await expect(page.getByTestId("assistant-answer").nth(1)).toContainText("Continuing.");

  await page.getByTestId("assistant-new").click();
  await expect(page.getByTestId("assistant-empty")).toBeVisible();
  await input.fill("NOTOOL hello");
  await input.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("without searching");
  await expect(page.getByTestId("assistant-answer")).not.toContainText("Continuing.");
});

test("a_signed_out_cli_is_said_in_words", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await page.getByTestId("assistant-input").fill("SIGNED-OUT anything");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-error")).toContainText("not signed in");
});

test("stop_ends_the_answer_and_keeps_what_arrived", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await page.getByTestId("assistant-input").fill("NOTOOL SLOW tell me a long story");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("I can");
  await page.getByTestId("assistant-stop").click();
  await expect(page.getByTestId("assistant-send")).toBeVisible();
  const kept = await page.getByTestId("assistant-answer").textContent();
  expect(kept?.length ?? 0).toBeGreaterThan(0);
});

test("escape_closes_the_panel_and_gives_focus_back", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/m/as-1");
  await page.getByTestId("meeting-list").focus();
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-input")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("assistant-panel")).toHaveCount(0);
  await expect(page.getByTestId("meeting-list")).toBeFocused();
  // And Ctrl+J toggles it, from anywhere, in any keyboard layout (it is matched on the key's position).
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-panel")).toBeVisible();
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-panel")).toHaveCount(0);
});

/* Plan step 2: search reaches summaries, and a Hebrew word inside its prefixed form. */
const SEARCHABLE = [
  {
    id: "se-1",
    title: "Pricing sync",
    state: "RENDERED",
    started_at: isoAt(0, 11),
    summary_html: "<p>We agreed the Berlin launch moves to January.</p>",
    turns: [{ speaker: "THEM", at_ms: 7000, text: "דיברנו בתקציב של הרבעון הבא" }],
  },
];

test("search_finds_a_word_only_the_summary_holds", async ({ page, seed }) => {
  await seed(SEARCHABLE);
  await gotoApp(page, "/search");
  await page.getByTestId("search-input").fill("Berlin");
  await page.getByTestId("search-input").press("Enter");
  const group = page.getByTestId("search-group");
  await expect(group).toHaveCount(1);
  await expect(group).toHaveAttribute("data-kind", "summary");
  await expect(page.getByTestId("hit-kind")).toHaveAttribute("data-kind", "summary");
});

test("a_hebrew_word_is_found_inside_its_prefixed_form_by_search_and_by_the_assistant", async ({ page, seed }) => {
  await seed(SEARCHABLE);
  await gotoApp(page, "/search");
  await page.getByTestId("search-input").fill("תקציב");
  await page.getByTestId("search-input").press("Enter");
  await expect(page.getByTestId("search-group")).toHaveAttribute("data-kind", "transcript");

  await page.keyboard.press("Control+j");
  await page.getByTestId("assistant-input").fill("מה נאמר על 'תקציב'");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("Pricing sync");
});

/* Plan step 3: checked citations, drawn as chips that open the moment. */
test("a_citation_is_a_chip_that_opens_the_meeting_at_the_moment", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await page.getByTestId("assistant-input").fill("Which meetings mention the budget");
  await page.keyboard.press("Enter");
  // The fake cites 500 ms inside the line and adds a citation to a meeting that does
  // not exist: one chip, snapped to the line, and no trace of the invented one.
  const chip = page.getByTestId("assistant-citation");
  await expect(chip).toHaveCount(1);
  await expect(chip).toHaveText("1");
  await expect(chip).toHaveAttribute("data-at-ms", "4000");
  await chip.hover();
  await expect(page.getByTestId("assistant-citation-card")).toContainText("The marketing budget is cut by ten percent");
  await expect(page.getByTestId("assistant-answer")).not.toContainText("[[");
  await chip.click();
  await expect(page).toHaveURL(/\/m\/as-1\?at=4000/);
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "as-1");
  // The panel stays open beside the meeting it points into.
  await expect(page.getByTestId("assistant-panel")).toBeVisible();
});

test("the_question_carries_the_screen_it_was_asked_from", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/m/as-1");
  await page.keyboard.press("Control+j");
  const request = page.waitForRequest("**/api/assistant/chat");
  await page.getByTestId("assistant-input").fill("NOTOOL what is this meeting about?");
  await page.keyboard.press("Enter");
  const body = (await request).postDataJSON();
  expect(body.context).toEqual({ route: "/m/as-1", meeting_id: "as-1", scope: "meeting" });
});

/* Plan step 4: saved conversations. */
test("a_reload_keeps_the_conversation_and_the_follow_up_resumes_it", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await page.getByTestId("assistant-input").fill("Which meetings mention the budget");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("Q4 budget review");

  await page.reload();
  await expect(page.getByTestId("app")).toBeVisible();
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-question")).toContainText("Which meetings mention the budget");
  await expect(page.getByTestId("assistant-answer")).toContainText("Q4 budget review");
  await expect(page.getByTestId("assistant-citation")).toHaveCount(1);
  await expect(page.getByTestId("assistant-tool")).toHaveAttribute("data-state", "output-available");

  await page.getByTestId("assistant-input").fill("NOTOOL and the first one?");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-answer").nth(1)).toContainText("Continuing.");
});

test("history_lists_reopens_renames_and_deletes_conversations", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  const input = page.getByTestId("assistant-input");
  await input.fill("NOTOOL first conversation");
  await input.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("without searching");
  await page.getByTestId("assistant-new").click();
  await input.fill("NOTOOL second conversation");
  await input.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("without searching");

  await page.getByTestId("assistant-history").click();
  const rows = page.getByTestId("assistant-session");
  await expect(rows).toHaveCount(2);
  await expect(page.getByTestId("assistant-history-list")).toContainText("Today");
  await expect(rows.first()).toContainText("NOTOOL second conversation");

  // Reopen the first: its own messages, not the second's.
  await rows.nth(1).getByTestId("assistant-session-open").click();
  await expect(page.getByTestId("assistant-question")).toHaveText("NOTOOL first conversation");

  // Rename it.
  await page.getByTestId("assistant-history").click();
  await rows.nth(1).hover();
  await rows.nth(1).getByTestId("assistant-session-rename").click();
  await page.getByTestId("assistant-rename-input").fill("Renamed chat");
  await page.getByTestId("assistant-rename-input").press("Enter");
  await expect(rows.nth(1)).toContainText("Renamed chat");

  // Delete it; the one being shown, so the panel moves to a new chat.
  await rows.nth(1).hover();
  await rows.nth(1).getByTestId("assistant-session-delete").click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(rows).toHaveCount(1);
  await page.getByTestId("assistant-history").click();
  await expect(page.getByTestId("assistant-empty")).toBeVisible();
});

test("an_answer_stopped_part_way_is_kept", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await page.getByTestId("assistant-input").fill("NOTOOL SLOW tell me a long story");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toContainText("I can");
  await page.getByTestId("assistant-stop").click();
  await expect(page.getByTestId("assistant-send")).toBeVisible();
  await page.waitForTimeout(500);
  await page.reload();
  await expect(page.getByTestId("app")).toBeVisible();
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-answer")).toContainText("I can");
});
