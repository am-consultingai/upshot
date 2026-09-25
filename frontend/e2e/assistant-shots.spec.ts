import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * The assistant panel in both languages and both themes, for review (plan step 5).
 * Skipped unless UP_SHOTS is set, like shots.spec: it writes files, it proves nothing.
 *
 *   UP_SHOTS=1 npx playwright test assistant-shots
 */

const OUT = "../artifacts/assistant";
const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

for (const [theme, language] of [
  ["light", "en"],
  ["dark", "en"],
  ["light", "he"],
  ["dark", "he"],
] as const) {
  test(`assistant_shot_${theme}_${language}`, async ({ page, seed, request }) => {
    test.skip(!process.env.UP_SHOTS, "set UP_SHOTS=1 to capture screenshots");
    await seed([
      {
        id: "shot-1",
        title: language === "he" ? "סקירת תקציב רבעון 4" : "Q4 budget review",
        state: "RENDERED",
        started_at: isoAt(0, 10),
        turns: [
          {
            speaker: "THEM",
            at_ms: 4000,
            text: language === "he" ? "תקציב השיווק מקוצץ בעשרה אחוזים." : "The marketing budget is cut by ten percent.",
          },
        ],
      },
    ]);
    await request.put("/api/settings", {
      headers: HEADERS,
      data: { values: { "ui.language": language, "ui.theme": theme } },
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoApp(page, "/m/shot-1");
    const manual = page.getByTestId("capture-manual");
    if (await manual.isVisible()) await manual.click();
    await page.keyboard.press("Control+j");
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/empty-${theme}-${language}.png` });
    await page.getByTestId("assistant-input").fill(language === "he" ? "מה נאמר על 'תקציב'" : "What was said about the 'budget'");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("assistant-answer-footer")).toBeVisible();
    await page.getByTestId("assistant-citation").hover();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/answer-${theme}-${language}.png` });
  });
}
