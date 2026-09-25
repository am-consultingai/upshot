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
