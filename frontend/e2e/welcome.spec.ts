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
  // Setup done again, the meeting language back at its default.
  await reset(request);
});

test("a_new_install_opens_on_setup_and_done_lands_in_the_library", async ({ page }) => {
  await gotoApp(page, "/");
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByTestId("welcome-page")).toBeVisible();
  // The screen has the window to itself: nothing assumes a finished setup.
  await expect(page.getByTestId("rail")).toHaveCount(0);

  // 1. Hebrew is offered first, and the model is the Hebrew one.
  await expect(page.getByTestId("meeting-language")).toHaveValue("he");
  await expect(page.getByTestId("model-name")).toContainText("ivrit-ai/");

  // 2. What the download is, before it is pressed.
  await expect(page.getByTestId("model-size")).toContainText("GB");
  await expect(page.getByTestId("model-free")).toContainText("free");
  await expect(page.getByTestId("model-download")).toBeVisible();

  // 3. Both meters are there, and the synthetic tone moves the microphone's.
  await expect(page.getByTestId("mic-meter-me")).toBeVisible();
  await expect(page.getByTestId("mic-meter-them")).toBeVisible();
  await expect
    .poll(
      async () =>
        Number(await page.getByTestId("mic-meter-me").getByRole("meter").getAttribute("data-lit")),
      { timeout: 10_000 },
    )
    .toBeGreaterThan(0);

  // 4. Where it will run, and why.
  await expect(page.getByTestId("device-plan")).toBeVisible();
  await expect(page.getByTestId("device-reason")).not.toBeEmpty();

  // English is transcribed as English: the model follows the language.
  await page.getByTestId("meeting-language").selectOption("en");
  await expect(page.getByTestId("model-name")).toContainText("faster-whisper");
  await expect(page.getByTestId("meeting-language-note")).toContainText("English as English");

  await page.getByTestId("welcome-done").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("rail")).toBeVisible();

  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.config.setup.done).toBe(true);
  expect(settings.config.asr.language_mode).toBe("fixed");
  expect(settings.config.asr.default_language).toBe("en");

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

test("done_saves_the_hebrew_that_was_shown", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await page.getByTestId("welcome-done").click();
  await expect(page).toHaveURL(/\/$/);
  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.config.asr.language_mode).toBe("fixed");
  expect(settings.config.asr.default_language).toBe("he");
});

test("skip_marks_setup_done_and_changes_nothing", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await page.getByTestId("welcome-skip").click();
  await expect(page).toHaveURL(/\/$/);
  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.config.setup.done).toBe(true);
  expect(settings.config.asr.language_mode).toBe("detect");
});

test("every_screen_leads_to_setup_until_it_is_done", async ({ page }) => {
  await gotoApp(page, "/settings#audio");
  await expect(page).toHaveURL(/\/welcome$/);
  await gotoApp(page, "/actions");
  await expect(page).toHaveURL(/\/welcome$/);
});

test("setup_stays_reachable_from_settings", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await page.getByTestId("welcome-skip").click();
  await expect(page).toHaveURL(/\/$/);

  await gotoSettings(page, "speech");
  await expect(page.getByTestId("meeting-language")).toHaveValue("detect");
  await expect(page.getByTestId("model-name")).toBeVisible();
  await expect(page.getByTestId("asr-device")).toHaveValue("auto");

  // Chosen in Settings, the language moves the model there too.
  await page.getByTestId("meeting-language").selectOption("en");
  await expect(page.getByTestId("model-name")).toContainText("faster-whisper");

  // And the whole screen can be had again, without being sent back to it.
  await page.getByTestId("open-welcome").click();
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByTestId("meeting-language")).toHaveValue("en");
});
