import { expect, gotoSettings, test } from "./fixtures";

// "CPU acceleration" (asr.cpu_fast): greedy decoding on the CPU. The GPU always keeps
// full quality; the switch says so, and what it costs, in its tooltip.
test("cpu_acceleration_switch_saves_and_explains_itself", async ({ page }) => {
  await gotoSettings(page, "audio");
  const toggle = page.getByTestId("cpu-fast");
  await expect(toggle).toBeVisible();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByText("CPU acceleration").first()).toBeVisible();
  // Explained in the row, as every setting is, whether or not tooltips are on.
  await expect(page.getByTestId("cpu-fast-hint")).toContainText("about 30%");

  await toggle.hover();
  await expect(page.getByRole("tooltip").filter({ hasText: "about 30%" })).toBeVisible();

  await toggle.check();
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.asr.cpu_fast)
    .toBe(true);

  await page.reload();
  await expect(page.getByTestId("cpu-fast")).toBeChecked();
  await page.getByTestId("cpu-fast").uncheck();
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.asr.cpu_fast)
    .toBe(false);
});
