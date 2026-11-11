/**
 * The error window (owner, 2026-11-10): a meeting that failed for a reason Upshot does not
 * recognise is shown in a window of its own. With crash reports on, the report has gone and
 * the window says so; with them off, it shows the report and sends it only on Send.
 *
 * `/api/test/fail-stage` fails the stage through the worker's own path, with a stand-in for
 * Sentry whose reports `/api/test/sent-reports` lists.
 */
import type { APIRequestContext } from "@playwright/test";
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

async function fail(request: APIRequestContext, reports: "on" | "off" | "none") {
  const response = await request.post("/api/test/fail-stage", {
    headers: HEADERS,
    data: { meeting_id: "e2e-tableau", stage: "summarize", reports, message: "Claude Code exited 1: boom" },
  });
  expect(response.status()).toBe(200);
}

async function sent(request: APIRequestContext): Promise<unknown[]> {
  return (await (await request.get("/api/test/sent-reports", { headers: HEADERS })).json()).items;
}

test.beforeEach(async ({ seed }) => {
  await seed([
    {
      id: "e2e-tableau",
      title: "Tableau",
      state: "TRANSCRIBED",
      started_at: isoAt(0, 10),
      language: "en",
      turns: [{ speaker: "ME", at_ms: 0, end_ms: 3000, text: "The dashboard ships Friday." }],
    },
  ]);
});

test("reports_on_the_report_is_sent_and_the_window_says_so", async ({ page, request }) => {
  const before = (await sent(request)).length;
  await fail(request, "on");
  await gotoApp(page, "/");
  const window = page.getByTestId("error-notice");
  await expect(window).toContainText("Upshot could not finish Tableau");
  await expect(window).toContainText("It stopped while writing the summary.");
  await expect(window.getByTestId("error-notice-sent")).toContainText("Reference:");
  await expect(window.getByTestId("error-notice-send")).toHaveCount(0);
  expect((await sent(request)).length).toBe(before + 1);
  await window.getByTestId("error-notice-close").click();
  await expect(page.getByTestId("error-notice")).toHaveCount(0);
});

test("reports_off_nothing_leaves_until_send_is_pressed", async ({ page, request }) => {
  const before = (await sent(request)).length;
  await fail(request, "off");
  await gotoApp(page, "/");
  const window = page.getByTestId("error-notice");
  await expect(window).toContainText("Send a report on this error to help fix it?");
  expect((await sent(request)).length).toBe(before);

  await window.getByTestId("error-notice-preview").click();
  await expect(window.getByTestId("error-notice-report")).toContainText('"where": "stage:summarize"');
  await window.getByTestId("error-notice-send").click();
  await expect(page.getByTestId("error-notice")).toHaveCount(0);
  expect((await sent(request)).length).toBe(before + 1);
});

test("dont_send_sends_nothing_and_the_window_stays_closed", async ({ page, request }) => {
  const before = (await sent(request)).length;
  await fail(request, "off");
  await gotoApp(page, "/");
  await page.getByTestId("error-notice-dont-send").click();
  await expect(page.getByTestId("error-notice")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("app")).toBeVisible();
  await expect(page.getByTestId("error-notice")).toHaveCount(0);
  expect((await sent(request)).length).toBe(before);
});

test("open_meeting_goes_to_it", async ({ page, request }) => {
  await fail(request, "none");
  await gotoApp(page, "/");
  await page.getByTestId("error-notice-open").click();
  await expect(page).toHaveURL(/\/m\/e2e-tableau/);
  await expect(page.getByTestId("error-notice")).toHaveCount(0);
});
