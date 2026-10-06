/**
 * About and updates (D87), on the real backend.
 *
 * The e2e server runs from source, so by default it says what is available but never
 * installs; `update_ready` seeds a verified update whose installer starts nothing.
 */
import { expect, gotoApp, reset, test } from "./fixtures";

test.afterEach(async ({ request }) => {
  await reset(request);
});

test("the section says which version this is and that a source run does not update itself", async ({ page }) => {
  await gotoApp(page, "/settings#about");
  await expect(page.getByTestId("settings-section-about")).toBeVisible();
  await expect(page.getByTestId("about-version")).not.toHaveText("…");
  await expect(page.getByTestId("update-status")).toContainText("runs from source");
  await expect(page.getByTestId("update-install")).toHaveCount(0);
});

test("the two switches save at once and survive a reload", async ({ page }) => {
  await gotoApp(page, "/settings#about");
  const auto = page.getByTestId("updates-auto");
  const beta = page.getByTestId("updates-beta");
  await expect(auto).toBeChecked();
  await expect(beta).not.toBeChecked();
  await auto.uncheck();
  await beta.check();
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.updates)
    .toMatchObject({ auto_install: false, channel: "beta" });
  await page.reload();
  await expect(page.getByTestId("updates-auto")).not.toBeChecked();
  await expect(page.getByTestId("updates-beta")).toBeChecked();
});

test("a ready update offers Restart to update, with its notes in the interface language", async ({
  page,
  seedBody,
}) => {
  await seedBody({ update_ready: "9.9.9" });
  await gotoApp(page, "/settings#about");
  await expect(page.getByTestId("update-status")).toHaveAttribute("data-phase", "ready");
  await expect(page.getByTestId("update-status")).toContainText("Version 9.9.9 is ready to install.");
  await expect(page.getByTestId("update-notes")).toContainText("Faster summaries.");
  const install = page.getByTestId("update-install");
  await install.click();
  await expect(install).toContainText("Restarting to update");
});

test("after an update, a toast says so once and opens what's new", async ({ page, seedBody }) => {
  await seedBody({ update_installed: "9.9.9" });
  await gotoApp(page, "/");
  await expect(page.getByText("Updated to 9.9.9")).toBeVisible();
  await page.getByRole("button", { name: "What's new" }).click();
  await expect(page).toHaveURL(/\/settings#about$/);
  await page.reload();
  await expect(page.getByText("Updated to 9.9.9")).toHaveCount(0);
});
