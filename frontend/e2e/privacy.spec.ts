/**
 * Crash reports (D87), on the real backend: asked once in setup, or once by a notice for
 * an install set up before the question existed, and changeable in Settings, Privacy.
 *
 * The e2e server runs from source, with no DSN, so by default nothing can be sent and
 * nothing is asked; `reports_available` makes it as if the build carried one, with a
 * sender that goes nowhere. No spec here ever reaches Sentry.
 */
import { expect, gotoApp, reset, test } from "./fixtures";

test.afterEach(async ({ page, request }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await reset(request);
});

async function consent(page: import("@playwright/test").Page): Promise<string> {
  return (await (await page.request.get("/api/diagnostics")).json()).consent;
}

test("setup asks once, just before the sound check, with nothing chosen for the user", async ({
  page,
  seedBody,
}) => {
  await seedBody({ setup_done: false, reports_available: true });
  // Neither CLI on this machine, whatever is really installed on it (as welcome.spec.ts).
  await page.route("**/api/llm/status", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.providers = body.providers.map((p: { needs: string }) =>
      p.needs === "cli" ? { ...p, ready: false, signed_in: null, account: undefined } : p,
    );
    await route.fulfill({ response, json: body });
  });
  await gotoApp(page, "/");
  await page.getByTestId("setup-next").click();
  await page.getByTestId("setup-skip").click(); // calendar
  await page.getByTestId("setup-skip").click(); // services
  await expect(page.getByTestId("setup-step-key")).toBeVisible();
  await page.getByTestId("setup-skip").click(); // key
  await expect(page.getByTestId("setup-step-reports")).toBeVisible();
  await expect(page.getByTestId("setup-crumb-reports")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("setup-step-reports")).toContainText("Never in a report");
  expect(await consent(page)).toBe("unset");
  await expect(page.getByTestId("reports-yes")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("reports-no").click();
  expect(await consent(page)).toBe("unset");
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-audio")).toBeVisible();
  await expect.poll(() => consent(page)).toBe("off");
});

test("a build that cannot send asks nothing and says so in Settings", async ({ page }) => {
  await gotoApp(page, "/settings#privacy");
  await expect(page.getByTestId("reports-notice")).toHaveCount(0);
  await expect(page.getByTestId("crash-reports-unavailable")).toBeVisible();
  await expect(page.getByTestId("crash-report-none")).toBeVisible();
});

test("an install set up before the question is asked once by a notice", async ({ page, seedBody }) => {
  await seedBody({ reports_available: true });
  await gotoApp(page, "/");
  await expect(page.getByTestId("reports-notice")).toBeVisible();
  await page.getByTestId("reports-notice-yes").click();
  await expect(page.getByTestId("reports-notice")).toHaveCount(0);
  await expect.poll(() => consent(page)).toBe("on");
  await page.reload();
  await expect(page.getByTestId("reports-notice")).toHaveCount(0);
});

test("Settings changes the answer and shows exactly what was last sent", async ({ page, seedBody }) => {
  await seedBody({ reports_available: true, crash_report: true });
  await gotoApp(page, "/settings#privacy");
  const toggle = page.getByTestId("crash-reports");
  await expect(toggle).toBeChecked();
  await page.getByTestId("crash-report-show").click();
  const shown = page.getByTestId("crash-report-json");
  await expect(shown).toContainText("seeded for a spec");
  await expect(shown).toContainText('"release": "upshot@');
  await toggle.uncheck();
  await expect.poll(() => consent(page)).toBe("off");
});

test("an error in the interface reaches the report as names and positions only", async ({ page, seedBody }) => {
  await seedBody({ reports_available: true });
  await gotoApp(page, "/settings#privacy");
  await page.getByTestId("crash-reports").check();
  await expect.poll(() => consent(page)).toBe("on");
  // What the page posts when it catches an error: a title and a meeting's address in it.
  await page.evaluate(async () => {
    const csrf = document.cookie.match(/up_csrf=([^;]+)/)?.[1] ?? "";
    await fetch("/api/diagnostics/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
      body: JSON.stringify({
        kind: "render",
        message: "cannot render פגישת תקציב",
        stack:
          "TypeError: x is undefined\n" +
          "    at Summary (http://127.0.0.1/assets/index-AbC.js:12:34)\n" +
          "    at http://127.0.0.1/meeting/2026-10-05_1944_0bac74_budget:1:2",
      }),
    });
  });
  await page.reload();
  await page.getByTestId("crash-report-show").click();
  const shown = page.getByTestId("crash-report-json");
  await expect(shown).toContainText('"platform": "javascript"');
  await expect(shown).toContainText("app:///assets/index-AbC.js");
  await expect(shown).not.toContainText("פגישת");
  await expect(shown).not.toContainText("budget");
});
