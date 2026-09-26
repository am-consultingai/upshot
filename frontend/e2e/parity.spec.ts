/**
 * The app beside the redesign mock, screen for screen.
 *
 * Skipped unless UP_SHOTS is set, like shots.spec: it writes files and asserts
 * nothing. Seeds the mock's own library (parity-data.ts) and captures each of the
 * mock's screens at its 1440×900 frame, in light, dark and Hebrew, into
 * artifacts/parity/ — to be read against .ui-research/mocks/shots/.
 *
 *   UP_SHOTS=1 npx playwright test parity
 */
import { gotoApp, test } from "./fixtures";
import { parityBody } from "./parity-data";

const OUT = "../artifacts/parity";

for (const [theme, language] of [
  ["light", "en"],
  ["dark", "en"],
  ["light", "he"],
] as const) {
  test(`parity_${theme}_${language}`, async ({ page, seedBody, request }) => {
    test.skip(!process.env.UP_SHOTS, "set UP_SHOTS=1 to capture screenshots");
    test.setTimeout(120_000);
    await seedBody(parityBody());
    await page.setViewportSize({ width: 1440, height: 900 });
    const name = `${theme}-${language}`;
    const settle = async () => {
      await page.evaluate(
        ([t, l]) => {
          document.documentElement.setAttribute("data-theme", t);
          document.documentElement.lang = l;
          document.documentElement.dir = l === "he" ? "rtl" : "ltr";
        },
        [theme, language],
      );
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      await page.waitForTimeout(700);
    };
    if (language === "he") {
      await gotoApp(page, "/");
      await request.put("/api/settings", {
        headers: { "X-CSRF-Token": "e2e-csrf-secret", Cookie: "up_session=e2e-session-secret; up_csrf=e2e-csrf-secret" },
        data: { values: { "ui.language": "he" } },
      });
    }

    await gotoApp(page, "/");
    await settle();
    await page.screenshot({ path: `${OUT}/library-${name}.png` });

    await page.getByTestId("span-list").click();
    await settle();
    await page.screenshot({ path: `${OUT}/library-list-${name}.png` });
    await page.getByTestId("span-week").click();

    await gotoApp(page, "/m/m-onboarding");
    await settle();
    await page.screenshot({ path: `${OUT}/meeting-${name}.png` });

    await page.getByTestId("meeting-tab-transcript").click();
    await settle();
    await page.screenshot({ path: `${OUT}/transcript-${name}.png` });

    await gotoApp(page, "/actions");
    await settle();
    await page.screenshot({ path: `${OUT}/actions-${name}.png` });

    await gotoApp(page, "/search");
    await settle();
    await page.screenshot({ path: `${OUT}/search-${name}.png` });

    await page.keyboard.press("Control+k");
    await page.getByTestId("palette-input").fill("activation");
    await page.waitForTimeout(900);
    await settle();
    await page.screenshot({ path: `${OUT}/palette-${name}.png` });
  });
}
