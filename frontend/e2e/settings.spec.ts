import { expect, gotoSettings, test } from "./fixtures";

/**
 * "Detecting meetings" is the user's choice, and it has to survive being chosen.
 *
 * It did not. `scripts/demo.sh` exported UP_DETECTION__MODE, and the environment is the
 * top configuration layer, so the choice saved to app_config.json and was overridden
 * again on every start. The control worked, read back correctly, and reverted — and
 * nothing on the screen, in the API or in the log said what was doing it.
 */
test("the_detection_mode_is_chosen_saved_and_read_back", async ({ page }) => {
  await gotoSettings(page, "audio");

  const select = page.getByTestId("detection-mode");
  // The shipped default watches and logs; it is never off unless someone says so.
  await expect(select).toHaveValue("shadow");
  // And nothing is holding it down, so the control means what it says.
  await expect(page.getByTestId("setting-pinned")).toHaveCount(0);

  // No Save button anywhere on this screen: the control's own new state is the
  // acknowledgement, and a reload is the only honest test of whether it was kept.
  await select.selectOption("on");
  await page.reload();
  await expect(page.getByTestId("detection-mode")).toHaveValue("on");

  // The detector reads the mode as it runs, with no restart, as the hint promises.
  const status = await page.request.get("/api/status");
  expect((await status.json()).detector.mode).toBe("on");

  await page.getByTestId("detection-mode").selectOption("shadow"); // leave the default as it was
});
