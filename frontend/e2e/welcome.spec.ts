/**
 * First-run setup, on the real backend (epic z8tj1hb01k).
 *
 * Every other spec runs with setup done — the e2e server starts that way and every seed
 * reset puts it back — so these ask for it undone first, and hand it back afterwards.
 * Nothing here installs, signs in or downloads: the CLI status is answered by the spec
 * (the machine running it may have Claude Code or Codex installed, and setup would
 * rightly pre-tick them), and Download is never pressed — it would reach Hugging Face
 * for gigabytes. The flow itself, every state of it, is walked on the mock in
 * setup-mock.spec.ts.
 */
import type { Page } from "@playwright/test";
import { expect, gotoApp, gotoSettings, reset, test } from "./fixtures";

test.beforeEach(async ({ page, seedBody }) => {
  await seedBody({ setup_done: false });
  // Neither CLI on this machine, whatever is really installed on it.
  await page.route("**/api/llm/status", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.providers = body.providers.map((p: { needs: string }) =>
      p.needs === "cli" ? { ...p, ready: false, signed_in: null, account: undefined } : p,
    );
    await route.fulfill({ response, json: body });
  });
});

test.afterEach(async ({ page, request }) => {
  // A status call still in flight must not outlive the test and fail the next one.
  await page.unrouteAll({ behavior: "ignoreErrors" });
  // Setup done again.
  await reset(request);
});

/** From the welcome to the AI step, skipping what can be skipped. */
async function toAiStep(page: Page): Promise<void> {
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-calendar")).toBeVisible();
  await page.getByTestId("setup-skip").click();
  await expect(page.getByTestId("setup-step-ai")).toBeVisible();
}

test("a_new_install_opens_on_setup_and_skipping_everything_lands_in_the_library", async ({ page }) => {
  await gotoApp(page, "/");
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByTestId("setup-step-welcome")).toBeVisible();
  // The screen has the window to itself: nothing assumes a finished setup.
  await expect(page.getByTestId("rail")).toHaveCount(0);
  // No CPU/GPU question any more: the device is chosen for the user.
  await expect(page.getByTestId("device-plan")).toHaveCount(0);

  await toAiStep(page);
  // The paid-plan notice comes first, and with nothing ticked a key is offered.
  await expect(page.getByTestId("ai-notice")).toBeVisible();
  await expect(page.getByTestId("key-offer")).toBeVisible();
  await expect(page.getByTestId("key-get-gemini")).toHaveAttribute("href", "https://aistudio.google.com/apikey");
  await page.getByTestId("setup-skip").click();

  // The sound check runs by itself: the microphone meter moves on the synthetic tone,
  // and the computer-audio test plays and finishes without being asked.
  await expect(page.getByTestId("setup-step-audio")).toBeVisible();
  await expect
    .poll(
      async () =>
        Number(await page.getByTestId("mic-meter-me").getByRole("meter").getAttribute("data-lit")),
      { timeout: 10_000 },
    )
    .toBeGreaterThan(0);
  await expect(page.getByTestId("speaker-test")).not.toHaveAttribute("data-phase", "playing", {
    timeout: 10_000,
  });
  await page.getByTestId("setup-next").click();

  // How to record comes last, with detect and notify already chosen (D64).
  await expect(page.getByTestId("setup-step-capture")).toBeVisible();
  await expect(page.getByTestId("capture-lead")).toContainText("notice when a meeting starts");
  await expect(page.getByTestId("capture-off")).toHaveCount(0);
  await expect(page.getByTestId("capture-shadow")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("setup-next").click();

  await expect(page.getByTestId("done-summaries")).toContainText("Transcripts only");
  await expect(page.getByTestId("done-capture")).toContainText("Notify me when a call starts");
  await page.getByTestId("setup-finish").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("rail")).toBeVisible();
  // The first arrival is celebrated, once.
  await expect(page.getByTestId("confetti")).toBeVisible();
  await expect(page.getByTestId("confetti")).toHaveCount(0, { timeout: 8000 });

  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.config.setup.done).toBe(true);
  expect(settings.config.setup.step).toBe("");
  expect(settings.config.llm.provider).toBe("none");
  expect(settings.config.detection.mode).toBe("shadow");
  expect(settings.config.detection.decided).toBe(true);

  // And it stays done, with no recording question left over the library, and no
  // second round of confetti.
  await page.reload();
  await expect(page.getByTestId("confetti")).toHaveCount(0);
  await expect(page.getByTestId("rail")).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("How should Upshot record your meetings?")).toHaveCount(0);
});

test("closing_the_app_half_way_reopens_setup_where_it_was_left", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await toAiStep(page);
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.setup.step)
    .toBe("ai");
  await page.reload();
  await expect(page.getByTestId("setup-step-ai")).toBeVisible();
  await expect(page.getByTestId("setup-resumed")).toBeVisible();
});

test("the_speech_model_is_never_asked_for", async ({ page }) => {
  // Fetching it is the installer's job, even when it is missing.
  await page.route("**/api/model", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), state: "missing" } });
  });
  await gotoApp(page, "/welcome");
  await expect(page.getByTestId("setup-crumb-welcome")).toBeVisible();
  await expect(page.getByTestId("setup-crumb-model")).toHaveCount(0);
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
  await toAiStep(page);
  await page.getByTestId("setup-skip").click();
  await expect(page.getByTestId("setup-step-audio")).toBeVisible();
  await expect(page.getByTestId("setup-step-audio")).not.toContainText("No microphone found");
  release();
  await expect(page.getByTestId("mic-device")).toBeVisible();
});

test("every_screen_leads_to_setup_until_it_is_done", async ({ page }) => {
  await gotoApp(page, "/settings#audio");
  await expect(page).toHaveURL(/\/welcome$/);
  await gotoApp(page, "/actions");
  await expect(page).toHaveURL(/\/welcome$/);
});

test("settings_has_no_transcription_or_storage_section", async ({ page, request }) => {
  // Which model runs, where it runs and where recordings live are not the user's to
  // manage: the installer and setup fetch the model, and Settings shows none of it.
  await reset(request);
  await gotoSettings(page, "audio");
  await expect(page.getByTestId("settings-section-speech")).toHaveCount(0);
  await expect(page.getByTestId("settings-section-storage")).toHaveCount(0);
  await expect(page.getByTestId("model-name")).toHaveCount(0);
  await expect(page.getByTestId("data-root")).toHaveCount(0);
});

test("setup_is_centred_and_reads_in_english_or_hebrew", async ({ page }) => {
  await gotoApp(page, "/welcome");
  const title = page.getByTestId("setup-step-welcome").locator("h1");
  const box = (await title.boundingBox())!;
  const width = page.viewportSize()!.width;
  // Centred: as much room on either side, give or take the scrollbar.
  expect(Math.abs(box.x - (width - box.x - box.width))).toBeLessThan(40);

  await page.getByTestId("setup-language-he").click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(title).toHaveText("ברוכים הבאים ל-Upshot");
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.ui.language)
    .toBe("he");
  await page.getByTestId("setup-language-en").click();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

test("closing_googles_tab_puts_the_connect_button_back", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await page.getByTestId("setup-next").click();
  const popup = page.waitForEvent("popup");
  await page.getByTestId("calendar-connect").click();
  await expect(page.getByTestId("calendar-connect")).toHaveAttribute("aria-busy", "true");
  await (await popup).close();
  await expect(page.getByTestId("calendar-connect")).toHaveAttribute("aria-busy", "false", { timeout: 10_000 });
  await expect(page.getByTestId("calendar-connect")).toHaveText("Connect with Google");
});

test("the_progress_track_the_title_and_the_buttons_stay_put_from_step_to_step", async ({ page }) => {
  // Components keep their box from step to step: nothing to re-find after Continue.
  await gotoApp(page, "/welcome");
  const where = async () => ({
    track: (await page.getByRole("navigation").boundingBox())!,
    title: (await page.locator("[data-testid^=setup-step-] h1").boundingBox())!,
    // The step's main content — cards, panels, choices — starts on the same line too.
    content: (await page.getByTestId("setup-content").boundingBox())!,
    forward: (await page.getByTestId("setup-next").or(page.getByTestId("setup-skip")).first().boundingBox())!,
  });
  const first = await where();
  await page.getByTestId("setup-next").click();
  for (const step of ["calendar", "ai", "audio", "capture"]) {
    await expect(page.getByTestId(`setup-step-${step}`)).toBeVisible();
    const now = await where();
    expect(now.track.y).toBe(first.track.y);
    expect(now.title.y).toBe(first.title.y);
    expect(now.content.y).toBe(first.content.y);
    expect(Math.round(now.forward.y)).toBe(Math.round(first.forward.y));
    // Forward keeps the end slot, whether it says Continue or Skip.
    const width = page.viewportSize()!.width;
    const endEdge = (box: { x: number; width: number }) => Math.round(box.x + box.width);
    expect(Math.abs(endEdge(now.forward) - endEdge(first.forward))).toBeLessThan(2);
    expect(endEdge(now.forward)).toBeLessThan(width);
    const forward = page.getByTestId("setup-next").or(page.getByTestId("setup-skip")).first();
    await forward.click();
  }
});
