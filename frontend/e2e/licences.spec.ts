/**
 * Open-source licences (licence epic, S8), on the real backend with the e2e server's
 * fixture list (tests/fixtures/notices.json).
 */
import { expect, gotoApp, test } from "./fixtures";

test("About opens the licences, and an entry's text loads when it is opened", async ({ page }) => {
  await gotoApp(page, "/settings#about");
  await page.getByTestId("open-licences").click();
  await expect(page).toHaveURL(/\/licences$/);
  await expect(page.getByTestId("licences-credits")).toContainText("Independent JPEG Group");

  const entries = page.getByTestId("licence-entry");
  await expect(entries).toHaveCount(3);
  await page.getByTestId("licences-search").fill("lgpl");
  await expect(entries).toHaveCount(1);
  await expect(page.getByTestId("licences-count")).toHaveText("1 component");
  await expect(entries.getByTestId("licence-id")).toHaveText("LGPL-2.1-or-later");

  await entries.locator("summary").click();
  await expect(entries.getByTestId("licence-text")).toContainText("GNU LESSER GENERAL PUBLIC LICENSE");
});
