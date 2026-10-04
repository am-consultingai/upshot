/**
 * The Terms of Service screen (D83), on the real backend.
 *
 * Every other spec runs with the Terms accepted — the e2e server starts that way and every
 * seed reset puts it back — so this asks for them pending first, and hands them back.
 */
import { expect, gotoApp, reset, test } from "./fixtures";

test.afterEach(async ({ request }) => {
  await reset(request);
});

test("unaccepted Terms come before everything, and accepting opens the app", async ({ page, seedBody }) => {
  await seedBody({ terms_pending: true });
  await gotoApp(page, "/settings");
  await expect(page).toHaveURL(/\/terms$/);
  await expect(page.getByTestId("terms-text")).toContainText("Terms of Service");
  await expect(page.getByTestId("terms-text").locator("section#plans")).toBeVisible();

  const accept = page.getByTestId("terms-accept");
  await expect(accept).toBeDisabled();
  await page.getByTestId("terms-agree").check();
  await accept.click();

  await expect(page).not.toHaveURL(/\/terms/);
  await expect(page.getByTestId("terms")).toHaveCount(0);
});

test("accepted Terms can still be read, without the accept controls", async ({ page }) => {
  await gotoApp(page, "/terms");
  await expect(page.getByTestId("terms-text")).toBeVisible();
  await expect(page.getByTestId("terms-accept")).toHaveCount(0);
});
