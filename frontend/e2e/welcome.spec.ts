/**
 * First-run setup (ClickUp z8tj1had06, z8tj1haczh).
 *
 * Every other spec runs with setup done — the e2e server starts that way and every seed
 * reset puts it back — so these ask for it undone first, and hand it back afterwards.
 * Download is never pressed: it would reach Hugging Face for gigabytes. What is checked
 * is what the screen says before it, which is the part a stranger reads.
 */
import { expect, gotoApp, gotoSettings, reset, test } from "./fixtures";

test.beforeEach(async ({ seedBody }) => {
  await seedBody({ setup_done: false });
});

test.afterEach(async ({ request }) => {
  // Setup done again.
  await reset(request);
});

test("a_new_install_opens_on_setup_and_done_lands_in_the_library", async ({ page }) => {
  await gotoApp(page, "/");
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByTestId("welcome-page")).toBeVisible();
  // The screen has the window to itself: nothing assumes a finished setup.
  await expect(page.getByTestId("rail")).toHaveCount(0);

  // 1. The one model (D60), and what the download is, before it is pressed. No language
  // is asked: the model transcribes Hebrew and English alike.
  await expect(page.getByTestId("meeting-language")).toHaveCount(0);
  await expect(page.getByTestId("model-name")).toContainText("ivrit-ai/whisper-large-v3-ct2");
  await expect(page.getByTestId("model-size")).toContainText("GB");
  await expect(page.getByTestId("model-free")).toContainText("free");
  await expect(page.getByTestId("model-download")).toBeVisible();

  // 2. Both meters are there, and the synthetic tone moves the microphone's.
  await expect(page.getByTestId("mic-meter-me")).toBeVisible();
  await expect(page.getByTestId("mic-meter-them")).toBeVisible();
  await expect
    .poll(
      async () =>
        Number(await page.getByTestId("mic-meter-me").getByRole("meter").getAttribute("data-lit")),
      { timeout: 10_000 },
    )
    .toBeGreaterThan(0);

  // 3. Where it will run, and why.
  await expect(page.getByTestId("device-plan")).toBeVisible();
  await expect(page.getByTestId("device-reason")).not.toBeEmpty();

  await page.getByTestId("welcome-done").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("rail")).toBeVisible();

  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.config.setup.done).toBe(true);

  // And it stays done.
  await page.reload();
  await expect(page.getByTestId("rail")).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
});

test("a_slow_device_list_is_not_reported_as_no_microphone", async ({ page }) => {
  // Machine B, job 012: the list took seconds, and the screen said "No microphone found"
  // the whole time, on a laptop with a working microphone.
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/audio/devices", async (route) => {
    await held;
    await route.continue();
  });
  await gotoApp(page, "/welcome");
  await expect(page.getByTestId("welcome-page")).toContainText("Looking for microphones");
  await expect(page.getByTestId("welcome-page")).not.toContainText("No microphone found");
  release();
  await expect(page.getByTestId("welcome-page")).not.toContainText("Looking for microphones");
});

test("a_downloaded_model_is_not_shown_as_still_to_download", async ({ page }) => {
  await page.route("**/api/model", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), state: "ready" } });
  });
  await gotoApp(page, "/welcome");
  await expect(page.getByTestId("model-ready")).toBeVisible();
  await expect(page.getByTestId("model-size")).toContainText("on this computer");
  await expect(page.getByTestId("model-size")).not.toContainText("to download");
});

test("skip_marks_setup_done_and_changes_nothing", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await page.getByTestId("welcome-skip").click();
  await expect(page).toHaveURL(/\/$/);
  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.config.setup.done).toBe(true);
});

test("every_screen_leads_to_setup_until_it_is_done", async ({ page }) => {
  await gotoApp(page, "/settings#audio");
  await expect(page).toHaveURL(/\/welcome$/);
  await gotoApp(page, "/actions");
  await expect(page).toHaveURL(/\/welcome$/);
});

test("settings_has_no_transcription_or_storage_section", async ({ page }) => {
  // Which model runs, where it runs and where recordings live are not the user's to
  // manage: first-run setup downloads the model, and Settings no longer shows any of it.
  await gotoApp(page, "/welcome");
  await page.getByTestId("welcome-skip").click();
  await expect(page).toHaveURL(/\/$/);
  await gotoSettings(page, "audio");
  await expect(page.getByTestId("settings-section-speech")).toHaveCount(0);
  await expect(page.getByTestId("settings-section-storage")).toHaveCount(0);
  await expect(page.getByTestId("model-name")).toHaveCount(0);
  await expect(page.getByTestId("data-root")).toHaveCount(0);
});
