/**
 * Accessibility, checked by a machine on every main screen, in every look the app has.
 *
 * axe-core runs the WCAG 2.x A and AA rules — contrast, names, roles, landmarks —
 * against the page as rendered, so a token change that greys a label below 4.5:1 or
 * a button that loses its name fails here rather than in someone's screen reader. It
 * runs in light, in dark and in Hebrew, because each is a different set of colours or
 * a different set of strings, and a pass in one says nothing about the others.
 *
 * Only serious and critical findings fail the spec. Moderate ones (a heading level
 * skipped, say) are worth knowing but are not what stops someone using the app.
 */
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, test } from "./fixtures";
import { parityBody } from "./parity-data";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

const LOOKS = [
  { name: "light", values: { "ui.theme": "light", "ui.language": "en" } },
  { name: "dark", values: { "ui.theme": "dark", "ui.language": "en" } },
  { name: "hebrew", values: { "ui.theme": "light", "ui.language": "he" } },
] as const;

/** Each screen, and what on it says it has finished drawing. */
const SCREENS = [
  { path: "/", ready: "calendar-timegrid" },
  { path: "/m/m-onboarding", ready: "summary-html" },
  { path: "/actions", ready: "action-group" },
  { path: "/search?q=activation", ready: "search-result" },
  { path: "/transcriptions", ready: "transcriptions-page" },
  { path: "/settings", ready: "settings-nav" },
] as const;

async function violations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  return results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .flatMap((violation) =>
      violation.nodes.map(
        (node) => `${violation.id} (${violation.impact}): ${node.target.join(" ")} — ${node.failureSummary ?? ""}`,
      ),
    );
}

for (const look of LOOKS) {
  test.describe(`a11y in ${look.name}`, () => {
    test.beforeEach(async ({ seedBody, request }) => {
      await seedBody(parityBody());
      await request.put("/api/settings", {
        headers: HEADERS,
        data: { values: { ...look.values, "detection.decided": true, "llm.provider": "fake" } },
      });
    });

    for (const screen of SCREENS) {
      test(`${screen.path} has no serious or critical violations`, async ({ page }) => {
        // Motion off, so a check never lands half way through a fade.
        await page.emulateMedia({ reducedMotion: "reduce" });
        await gotoApp(page, screen.path);
        await expect(page.getByTestId(screen.ready).first()).toBeVisible();
        expect(await violations(page)).toEqual([]);
      });
    }

    test("the empty library has no serious or critical violations", async ({ page, seedBody, request }) => {
      await seedBody({ meetings: [] });
      await request.put("/api/settings", { headers: HEADERS, data: { values: look.values } });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await gotoApp(page, "/");
      await expect(page.getByTestId("timeline-empty")).toBeVisible();
      expect(await violations(page)).toEqual([]);
    });
  });
}
