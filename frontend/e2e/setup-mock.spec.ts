/**
 * The first-run setup mock (Setup 0, z8tj1hb03a): walk-throughs that prove the mock
 * holds together, so what the user confirms is a flow that actually works.
 *
 * Needs a bundle with the mock in it, which an ordinary build leaves out, so the specs
 * skip unless asked for:
 *
 *   (cd frontend && VITE_SETUP_MOCK=1 npm run build)
 *   UP_SETUP_MOCK=1 python3 scripts/windows/e2e.py --grep setupmock_
 */
import type { Page } from "@playwright/test";
import { expect, gotoApp, test } from "./fixtures";

async function open(page: Page, query: string): Promise<void> {
  await gotoApp(page, `/setup-mock?panel=0&pace=0.02&${query}`);
  await expect(page.getByTestId("setup-flow")).toBeVisible();
}

test.describe("setupmock_", () => {
  test.skip(!process.env.UP_SETUP_MOCK, "set UP_SETUP_MOCK=1 with a VITE_SETUP_MOCK=1 build");

  test("setupmock_walkthrough: both ready ends on Claude with Codex as fallback", async ({ page, seedBody }) => {
    await seedBody({ reset: true });
    await open(page, "scenario=both-ready");
    await page.getByTestId("setup-next").click();
    await expect(page.getByTestId("setup-step-calendar")).toBeVisible();
    await page.getByTestId("calendar-connect").click();
    await expect(page.getByTestId("setup-step-ai")).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId("ai-tick-claude")).toHaveAttribute("aria-checked", "true");
    await expect(page.getByTestId("ai-both")).toBeVisible();
    await page.getByTestId("setup-next").click();
    await page.getByTestId("setup-next").click();
    await page.getByTestId("setup-next").click();
    await expect(page.getByTestId("done-summaries")).toContainText("Claude");
    await expect(page.getByTestId("done-calendar")).toContainText("dana.levi@example.com");
    await page.getByTestId("setup-finish").click();
    // The mock starts over, as the real flow leaves for the Timeline; the saved result stays readable.
    await expect(page.getByTestId("setup-mock")).toHaveAttribute("data-finished", /"fallback":"codex-subscription".*"capture":"shadow"/);
    await expect(page.getByTestId("setup-step-welcome")).toBeVisible();
  });

  test("setupmock_walkthrough: nothing chosen ends on transcripts only", async ({ page, seedBody }) => {
    await seedBody({ reset: true });
    await open(page, "scenario=fresh&step=ai");
    await expect(page.getByTestId("key-offer")).toBeVisible();
    await page.getByTestId("setup-skip").click();
    await page.getByTestId("setup-next").click();
    await page.getByTestId("setup-next").click();
    await expect(page.getByTestId("done-summaries")).toContainText("Transcripts only");
  });

  test("setupmock_walkthrough: ticking Claude installs and starts sign-in with no other click", async ({
    page,
    seedBody,
  }) => {
    await seedBody({ reset: true });
    await open(page, "scenario=fresh&step=ai");
    await page.getByTestId("ai-tick-claude").click();
    await expect(page.getByTestId("ai-signing-in-claude")).toBeVisible();
    await page.getByTestId("ai-code-claude").fill("abc-123");
    await page.getByTestId("ai-submit-code-claude").click();
    await expect(page.getByTestId("ai-ready-claude")).toBeVisible();
    await expect(page.getByTestId("key-offer")).toHaveCount(0);
  });

  test("setupmock_walkthrough: the speaker test runs by itself", async ({ page, seedBody }) => {
    await seedBody({ reset: true });
    await open(page, "scenario=fresh&step=audio");
    await expect(page.getByTestId("speaker-test")).toHaveAttribute("data-phase", "heard", { timeout: 5000 });
  });

  test("setupmock_walkthrough: reopening mid-setup resumes on the same step", async ({ page, seedBody }) => {
    await seedBody({ reset: true });
    await open(page, "scenario=fresh");
    await page.getByTestId("setup-next").click();
    await page.getByTestId("setup-skip").click();
    await expect(page.getByTestId("setup-step-ai")).toBeVisible();
    await gotoApp(page, "/setup-mock?panel=0&resume=1");
    await expect(page.getByTestId("setup-step-ai")).toBeVisible();
    await expect(page.getByTestId("setup-resumed")).toBeVisible();
  });
});
