/**
 * Feedback from inside the app (D87, D1-D3), on the real backend.
 *
 * The e2e server has no DSN: the form shows that it cannot send, and the preview still
 * works. `feedback_available` makes it as if the build carried one, with a sender that
 * goes nowhere. No spec reaches Sentry.
 */
import { expect, gotoApp, isoAt, reset, test } from "./fixtures";

test.afterEach(async ({ request }) => {
  await reset(request);
});

test("a build from source shows the form, the preview, and why it cannot send", async ({ page }) => {
  await gotoApp(page, "/settings#about");
  await page.getByTestId("open-feedback").click();
  const dialog = page.getByTestId("feedback-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId("feedback-unavailable")).toBeVisible();
  await page.getByTestId("feedback-message").fill("The meter froze after a call.");
  await page.getByTestId("feedback-preview").click();
  await expect(page.getByTestId("feedback-preview-json")).toContainText("The meter froze after a call.");
  await expect(page.getByTestId("feedback-preview-json")).not.toContainText('"user"');
  await expect(page.getByTestId("feedback-send")).toBeDisabled();
});

test("feedback is sent with a reference to quote", async ({ page, seedBody }) => {
  await seedBody({ feedback_available: true });
  await gotoApp(page, "/");
  // From the palette too, as any command.
  await page.keyboard.press("Control+K");
  await page.getByRole("option", { name: "Send feedback" }).click();
  await page.getByTestId("feedback-kind-problem").click();
  await page.getByTestId("feedback-message").fill("Export to Word loses the bullets.");
  await page.getByTestId("feedback-email").fill("someone@example.com");
  await page.getByTestId("feedback-send").click();
  await expect(page.getByTestId("feedback-done")).toContainText(/reference FB-[0-9A-F]{6}/);
});

test("a bad email is refused with the server's reason", async ({ page, seedBody }) => {
  await seedBody({ feedback_available: true });
  await gotoApp(page, "/?feedback=1");
  await expect(page.getByTestId("feedback-dialog")).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  await page.getByTestId("feedback-message").fill("hello");
  await page.getByTestId("feedback-email").fill("not an email");
  await page.getByTestId("feedback-send").click();
  await expect(page.getByTestId("feedback-error")).toContainText("email");
});

test("a summary is rated up or down and the choice stays", async ({ page, seed, seedMore }) => {
  await seed([
    {
      id: "e2e-rated",
      title: "Rated meeting",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: "<p>We moved the launch.</p>",
    },
  ]);
  // seedMore: seedBody would clear the library, meeting and all.
  await seedMore({ feedback_available: true });
  await gotoApp(page, "/m/e2e-rated");
  await page.getByTestId("rating-down").click();
  await page.getByTestId("rating-comment").fill("It missed who owns the launch.");
  await expect(page.getByTestId("rating-include")).not.toBeChecked();
  await page.getByTestId("rating-send").click();
  await expect(page.getByTestId("rating-thanks")).toContainText(/FB-[0-9A-F]{6}/);
  await page.reload();
  await expect(page.getByTestId("rating-down")).toHaveAttribute("aria-pressed", "true");
});
