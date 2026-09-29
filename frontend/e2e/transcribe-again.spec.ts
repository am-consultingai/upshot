import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The hidden "Transcribe again as…" (R9): only in the meeting's ⋯ menu. Hebrew first,
 * then the classifier's guesses, then every language under "Other…".
 */

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

test("transcribe_again_from_the_menu_starts_the_job", async ({ page, seed, request }) => {
  await seed([
    {
      id: "again-1",
      title: "Weekly sync",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      language: "he",
      summary_html: "<p>סיכום</p>",
      turns: [{ speaker: "THEM", at_ms: 1000, text: "היי וואן צ'ק" }],
    },
  ]);
  await request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.language": "en" } } });
  await gotoApp(page, "/m/again-1");

  // Nowhere on the page until the menu is opened.
  await expect(page.getByTestId("transcribe-again")).toHaveCount(0);
  await page.getByTestId("overflow-menu-trigger").click();
  await page.locator('[data-item="transcribe-again"]').click();
  const dialog = page.getByTestId("transcribe-again");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("transcribe-again-choice").first()).toHaveAttribute("data-code", "he");

  await dialog.getByTestId("transcribe-again-other").click();
  await dialog.getByTestId("transcribe-again-search").fill("engl");
  await dialog.locator('[data-code="en"]').last().click();
  await expect(dialog).toHaveCount(0);

  await expect
    .poll(async () => {
      const body = await (await request.get("/api/meetings/again-1", { headers: HEADERS })).json();
      return (body.jobs as { stage: string; state: string }[]).some(
        (job) => job.stage === "transcribe" && ["pending", "running", "done", "failed"].includes(job.state),
      );
    })
    .toBe(true);
});
