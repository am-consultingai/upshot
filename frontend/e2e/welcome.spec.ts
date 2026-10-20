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
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, gotoSettings, reset, test } from "./fixtures";

test.beforeEach(async ({ page, seedBody }) => {
  await seedBody({ setup_done: false });
  // Neither CLI on this machine, whatever is really installed on it.
  await page.route("**/api/llm/status*", async (route) => {
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

/** From the welcome to the AI plans question, which comes first (setup plan, O5). */
async function toServices(page: Page): Promise<void> {
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-services")).toBeVisible();
}

/** Past the calendar, skipped: it follows the plans question. */
async function skipCalendar(page: Page): Promise<void> {
  await expect(page.getByTestId("setup-step-calendar")).toBeVisible();
  await page.getByTestId("setup-skip").click();
}

/** On to the AI step with a Google AI plan chosen (D75, D79). */
async function toAiStep(page: Page): Promise<void> {
  await toServices(page);
  await page.getByTestId("service-antigravity").click();
  await page.getByTestId("setup-next").click();
  await skipCalendar(page);
  await expect(page.getByTestId("setup-step-ai")).toBeVisible();
}

/** On to the calendar, with no plan paid for. */
async function toCalendar(page: Page): Promise<void> {
  await toServices(page);
  await page.getByTestId("services-none").click();
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-calendar")).toBeVisible();
}

/** No plan paid for: the connect step is passed over and the no-plan step comes next. */
async function toKeyStep(page: Page): Promise<void> {
  await toServices(page);
  // The plans question wants an answer: Continue alone says so and stays.
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("services-unanswered")).toBeVisible();
  await expect(page.getByTestId("setup-step-services")).toBeVisible();
  await page.getByTestId("services-none").click();
  await page.getByTestId("setup-next").click();
  await skipCalendar(page);
  await expect(page.getByTestId("setup-step-key")).toBeVisible();
}

/** The no-plan step answered with transcripts only. */
async function transcriptsOnly(page: Page): Promise<void> {
  await page.getByTestId("key-choice-none").click();
  await page.getByTestId("setup-next").click();
}

test("a_new_install_opens_on_setup_and_skipping_everything_lands_in_the_library", async ({ page }) => {
  // A true first install: nothing summarizes yet (the e2e seed sets the fake provider).
  await page.request.put("/api/settings", {
    headers: { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` },
    data: { values: { "llm.provider": "none" } },
  });
  await gotoApp(page, "/");
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByTestId("setup-step-welcome")).toBeVisible();
  // The screen has the window to itself: nothing assumes a finished setup.
  await expect(page.getByTestId("rail")).toHaveCount(0);
  // No CPU/GPU question any more: the device is chosen for the user.
  await expect(page.getByTestId("device-plan")).toHaveCount(0);

  await toKeyStep(page);
  // Nothing ticked: no connect step, and the no-plan step asks how summaries get written —
  // part of the "Connect AI" stop, not a stop of its own.
  await expect(page.getByTestId("setup-crumb-connect")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("setup-crumb-key")).toHaveCount(0);
  // An answer is needed: Continue does nothing until one is chosen. Playwright will not
  // press an aria-disabled button; a person's mouse does.
  await page.getByTestId("setup-next").click({ force: true });
  await expect(page.getByTestId("setup-step-key")).toBeVisible();
  await page.getByTestId("key-choice-key").click();
  await expect(page.getByTestId("key-offer")).toBeVisible();
  await expect(page.getByTestId("key-get-gemini")).toHaveAttribute("href", "https://aistudio.google.com/apikey");
  // Gemini's key only: other providers' keys are for Settings.
  await expect(page.getByTestId("key-provider-anthropic")).toHaveCount(0);
  await transcriptsOnly(page);

  // The sound check: the microphone meter moves on the synthetic tone, and the
  // computer-audio test plays when asked, not on arrival.
  await expect(page.getByTestId("setup-step-audio")).toBeVisible();
  await expect(page.getByTestId("speaker-test")).toHaveAttribute("data-phase", "idle");
  await expect
    .poll(
      async () =>
        Number(await page.getByTestId("mic-meter-me").getByRole("meter").getAttribute("data-lit")),
      { timeout: 10_000 },
    )
    .toBeGreaterThan(0);
  await page.getByTestId("speaker-play").click();
  await expect(page.getByTestId("speaker-test")).not.toHaveAttribute("data-phase", "idle");
  // The button stays while the sound plays, so the keyboard focus stays on it.
  await expect(page.getByTestId("speaker-play")).toBeFocused();
  await expect(page.getByTestId("speaker-test")).not.toHaveAttribute("data-phase", "playing", {
    timeout: 10_000,
  });
  await page.getByTestId("setup-next").click();

  // How to record has its own screen, with detect and notify already chosen (D64).
  await expect(page.getByTestId("setup-step-capture")).toBeVisible();
  await expect(page.getByTestId("capture-lead")).toContainText("notice when a meeting starts");
  await expect(page.getByTestId("capture-off")).toHaveCount(0);
  await expect(page.getByTestId("capture-shadow")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("setup-next").click();

  // Finish says what was set up, one line each with Change (setup plan, §3.6).
  await expect(page.getByTestId("setup-step-finish")).toBeVisible();
  await expect(page.getByTestId("done-calendar")).toContainText("Not connected");
  await expect(page.getByTestId("done-summaries")).toContainText("Transcripts only");
  await expect(page.getByTestId("done-capture")).toContainText("Notify me when a call starts");
  await expect(page.getByTestId("done-audio")).toContainText(/working|not heard/);
  // Change opens that step; walking on comes back to Finish, the last step.
  await page.getByTestId("done-change-capture").click();
  await expect(page.getByTestId("setup-step-capture")).toBeVisible();
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-finish")).toHaveText("Start using Upshot");
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

test("a_google_ai_plan_gets_its_own_card_and_the_key_only_follows_if_it_is_not_signed_in", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await toAiStep(page);
  // The Gemini card is a Google AI plan now: Antigravity CLI, installed and signed in here.
  await expect(page.getByTestId("ai-card-antigravity")).toBeVisible();
  await expect(page.getByTestId("ai-notice")).toBeVisible();
  await expect(page.getByTestId("key-offer")).toHaveCount(0);
  // Not signed in (this server cannot install it): the key step comes next.
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-key")).toBeVisible();
  await page.getByTestId("key-choice-key").click();
  await expect(page.getByTestId("key-offer")).toBeVisible();
  await page.getByTestId("setup-back").click();
  await expect(page.getByTestId("setup-step-ai")).toBeVisible();
});

test("the_plans_question_says_who_writes_the_summaries_and_none_is_an_answer_of_its_own", async ({
  page,
}) => {
  await gotoApp(page, "/welcome");
  await toServices(page);
  await expect(page.locator("h1")).toHaveText("Which AI plans do you pay for?");
  await expect(page.getByTestId("services-lead")).toContainText("Free accounts won't work here");
  await page.getByTestId("service-claude").click();
  await expect(page.getByTestId("services-order")).toHaveText("Claude will write your summaries.");
  await page.getByTestId("service-antigravity").click();
  await page.getByTestId("service-codex").click();
  // In the order they are used, whatever order they were ticked in.
  await expect(page.getByTestId("services-order")).toHaveText(
    "Claude will write your summaries. When its limit runs out, ChatGPT takes over. " +
      "Gemini is a spare, used if one of these isn't set up.",
  );
  // "I don't pay for any of these" clears the ticks, and a tick clears it.
  await page.getByTestId("services-none").click();
  await expect(page.getByTestId("service-claude")).toHaveAttribute("aria-checked", "false");
  await expect(page.getByTestId("services-order")).toHaveCount(0);
  await page.getByTestId("service-codex").click();
  await expect(page.getByTestId("services-none")).toHaveAttribute("aria-checked", "false");
  await expect(page.getByTestId("setup-next")).toHaveText("Continue");
});

test("continue_waits_while_a_helper_installs_and_says_so", async ({ page }) => {
  // An install that is still running: launched, its window not yet closed.
  await page.route("**/api/llm/install", (route) =>
    route.fulfill({ json: { launched: true, command: "irm …", docs: "", log: "" } }),
  );
  await page.unroute("**/api/llm/status*");
  await page.route("**/api/llm/status*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.providers = body.providers.map((p: { needs: string }) =>
      p.needs === "cli" ? { ...p, ready: false, signed_in: null, account: undefined, console_open: true } : p,
    );
    await route.fulfill({ response, json: body });
  });
  await gotoApp(page, "/welcome");
  await toAiStep(page);
  await expect(page.getByTestId("ai-installing-antigravity")).toBeVisible();
  const next = page.getByTestId("setup-next");
  await expect(next).toHaveAttribute("aria-disabled", "true");
  // Playwright will not press an aria-disabled button; a person's mouse does.
  await next.click({ force: true });
  await expect(page.getByTestId("ai-wait-install")).toHaveText("Let's wait until the installation finishes.");
  await expect(page.getByTestId("setup-step-ai")).toBeVisible();
});

test("a_signed_in_subscription_passes_the_key_step_over", async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/llm/status*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.providers = body.providers.map((p: { id: string; needs: string }) =>
      p.id === "antigravity-subscription"
        ? { ...p, ready: true, signed_in: true, account: "Google account" }
        : p.needs === "cli"
          ? { ...p, ready: false, signed_in: null, account: undefined }
          : p,
    );
    await route.fulfill({ response, json: body });
  });
  await gotoApp(page, "/welcome");
  await toServices(page);
  // Already working, so already ticked.
  await expect(page.getByTestId("service-antigravity")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("setup-next").click();
  await skipCalendar(page);
  await expect(page.getByTestId("ai-ready-antigravity")).toBeVisible();
  await expect(page.getByTestId("setup-crumb-key")).toHaveCount(0);
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-audio")).toBeVisible();
});

/** How many of a scene's animations are running now. */
async function running(page: Page, testId: string): Promise<number> {
  return page.evaluate(
    (id) =>
      document
        .querySelector(`[data-testid="${id}"]`)
        ?.getAnimations({ subtree: true })
        .filter((a) => a.playState === "running").length ?? -1,
    testId,
  );
}

test("scenes_hold_still_and_play_once_when_asked", async ({ page }) => {
  // Nothing loops beside a question (setup plan, 2026-10-20): Google's pages rest on the
  // consent screen until "Show me", which plays them once.
  await gotoApp(page, "/welcome");
  await toCalendar(page);
  await expect(page.getByTestId("scene-calendar")).toBeVisible();
  expect(await running(page, "scene-calendar")).toBe(0);
  await page.getByTestId("scene-show").click();
  await expect.poll(() => running(page, "scene-calendar")).toBeGreaterThan(0);
  // Pressed again while playing it does nothing, and keeps the keyboard focus.
  await expect(page.getByTestId("scene-show")).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByTestId("scene-show")).toBeFocused();
});

test("under_reduced_motion_nothing_plays_by_itself", async ({ page }) => {
  // The welcome plays once on arrival, but not for someone who asked for less motion.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await gotoApp(page, "/welcome");
  await expect(page.getByTestId("scene-hero")).toBeVisible();
  expect(await running(page, "scene-hero")).toBe(0);
  await toCalendar(page);
  await expect(page.getByTestId("scene-calendar")).toBeVisible();
  const clock = () =>
    page.evaluate(
      () => document.querySelector('[data-testid="scene-calendar"]')?.getAnimations({ subtree: true })[0]?.currentTime,
    );
  const still = await clock();
  await page.getByTestId("scene-show").click();
  // Stepped, not moved: the clocks stay stopped, on another frame than the still one.
  await expect.poll(clock).not.toBe(still);
  expect(await running(page, "scene-calendar")).toBe(0);
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
  // The test machine, job 012: the list took seconds, and the screen said "No microphone found"
  // the whole time, on a laptop with a working microphone.
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/audio/devices", async (route) => {
    await held;
    await route.continue();
  });
  await gotoApp(page, "/welcome");
  await toKeyStep(page);
  await transcriptsOnly(page);
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

test("settings_has_no_model_section_and_storage_only_shows", async ({ page, request }) => {
  // Which model runs and where it runs are not the user's to manage: the installer and
  // setup fetch the model, and Settings shows none of it. Where recordings live is
  // shown, so "local only" can be checked, but not chosen: nothing there to edit.
  await reset(request);
  await gotoSettings(page, "audio");
  await expect(page.getByTestId("settings-section-speech")).toHaveCount(0);
  await expect(page.getByTestId("model-name")).toHaveCount(0);
  await expect(page.getByTestId("data-root")).toHaveCount(0);
  await page.getByTestId("settings-section-storage").click();
  await expect(page.getByTestId("storage-folder")).toBeVisible();
  await expect(page.getByTestId("settings-page").locator("input, select, textarea")).toHaveCount(0);
});

test("setup_is_centred_and_reads_in_english_or_hebrew", async ({ page }) => {
  await gotoApp(page, "/welcome");
  const title = page.getByTestId("setup-step-welcome").locator("h1");
  const box = (await title.boundingBox())!;
  const width = page.viewportSize()!.width;
  // Centred: as much room on either side, give or take the scrollbar.
  expect(Math.abs(box.x - (width - box.x - box.width))).toBeLessThan(40);

  await page.getByTestId("setup-language").selectOption("he");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(title).toHaveText("ברוכים הבאים ל-Upshot");
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.ui.language)
    .toBe("he");
  await page.getByTestId("setup-language").selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

for (const [language, welcome] of [
  ["de", "Willkommen bei Upshot"],
  ["es", "Le damos la bienvenida a Upshot"],
  ["fr", "Bienvenue dans Upshot"],
] as const) {
  test(`setup_reads_in_${language}_and_keeps_it_as_the_apps_language`, async ({ page }) => {
    await gotoApp(page, "/welcome");
    await page.getByTestId("setup-language").selectOption(language);
    await expect(page.locator("html")).toHaveAttribute("lang", language);
    await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
    await expect(page.getByTestId("setup-step-welcome").locator("h1")).toHaveText(welcome);
    await expect
      .poll(async () => (await (await page.request.get("/api/settings")).json()).config.ui.language)
      .toBe(language);
  });
}

test("closing_googles_tab_puts_the_connect_button_back", async ({ page }) => {
  await gotoApp(page, "/welcome");
  await toCalendar(page);
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
    forward: (await forwardButton().boundingBox())!,
  });
  const forwardButton = () =>
    page.getByTestId("setup-next").or(page.getByTestId("setup-skip")).or(page.getByTestId("setup-finish")).first();
  const first = await where();
  await page.getByTestId("setup-next").click();
  // "I don't pay for any of these" passes the connect step over (D75) and asks how
  // summaries get written on a step of its own; Finish is last, and its forward ends setup.
  const steps = ["services", "calendar", "key", "audio", "capture", "finish"];
  for (const step of steps) {
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
    if (step === "services") await page.getByTestId("services-none").click();
    if (step === "key") await page.getByTestId("key-choice-none").click();
    if (step !== steps.at(-1)) await forwardButton().click();
  }
});

test("a_second_calendar_can_be_added_in_setup", async ({ page }) => {
  // Connected already, as a second pass through setup finds it; then another account
  // beside it (D82), which never replaces the first.
  const accounts = [{ id: "ga_dana0001", address: "dana@example.com" }];
  const status = () => ({
    configured: true, state: "connected", account: accounts[0].address, auth_url: null,
    error: null, scope: "", opens_externally: true,
    accounts: accounts.map((a, i) => ({ ...a, state: "connected", visible: true, color: i + 1, error: null })),
  });
  await page.route("**/api/calendar/status", (route) => route.fulfill({ json: status() }));
  let connects = 0;
  await page.route("**/api/calendar/connect", (route) => {
    connects += 1;
    accounts.push({ id: "ga_noa00001", address: "noa@example.com" }); // Google's chooser
    return route.fulfill({
      json: { ...status(), state: "connecting", auth_url: "https://accounts.google.com/x", opened: true },
    });
  });
  await gotoApp(page, "/welcome");
  await toCalendar(page);
  await expect(page.getByTestId("calendar-connected")).toContainText("dana@example.com");

  await page.getByTestId("calendar-add").click();
  expect(connects).toBe(1);
  // Both are listed, and setup stays on the step: a third may follow.
  await expect(page.getByTestId("calendar-connected-account")).toHaveCount(2);
  await expect(page.getByTestId("calendar-connected")).toContainText("dana@example.com");
  await expect(page.getByTestId("calendar-connected")).toContainText("noa@example.com");
  await page.waitForTimeout(2000);
  await expect(page.getByTestId("calendar-add")).toBeVisible();
});

/** Settings as a setup finished before left them: setup reopened after an install (D69). */
async function earlier(page: Page, values: Record<string, unknown>): Promise<void> {
  await page.request.put("/api/settings", {
    headers: { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` },
    data: { values: { "detection.decided": true, ...values } },
  });
}

test("setup_reopened_after_an_install_opens_on_the_review_and_keeps_every_choice", async ({ page }) => {
  await earlier(page, { "detection.mode": "on", "llm.provider": "gemini", "llm.fallback_provider": "" });
  await gotoApp(page, "/");
  await expect(page.getByTestId("setup-step-finish")).toBeVisible();
  await expect(page.locator("h1")).toHaveText("Welcome back. Here's your setup.");
  await expect(page.getByTestId("done-capture")).toContainText("Record calls automatically");
  await expect(page.getByTestId("done-summaries")).toContainText("Gemini, with your key");
  await expect(page.getByTestId("setup-back")).toHaveCount(0);
  await page.getByTestId("setup-finish").click();
  await expect(page).toHaveURL(/\/$/);
  const config = (await (await page.request.get("/api/settings")).json()).config;
  expect(config.detection.mode).toBe("on");
  expect(config.llm.provider).toBe("gemini");
  expect(config.setup.done).toBe(true);
});

test("detection_switched_off_in_settings_stays_off_when_setup_reopens", async ({ page }) => {
  await earlier(page, { "detection.mode": "off" });
  await gotoApp(page, "/");
  await expect(page.getByTestId("done-capture")).toContainText("Off");
  // How to record shows neither way chosen; going on without one leaves it off.
  await page.getByTestId("done-change-capture").click();
  await expect(page.getByTestId("capture-shadow")).toHaveAttribute("aria-checked", "false");
  await expect(page.getByTestId("capture-on")).toHaveAttribute("aria-checked", "false");
  await page.getByTestId("setup-next").click();
  await page.getByTestId("setup-finish").click();
  await expect(page).toHaveURL(/\/$/);
  const config = (await (await page.request.get("/api/settings")).json()).config;
  expect(config.detection.mode).toBe("off");
});

test("transcripts_only_is_an_answer_even_over_a_key_set_before", async ({ page }) => {
  await earlier(page, { "detection.mode": "shadow", "llm.provider": "gemini" });
  await gotoApp(page, "/");
  await page.getByTestId("done-change-summaries").click();
  await page.getByTestId("services-none").click();
  await page.getByTestId("setup-next").click();
  await skipCalendar(page);
  // Keeping the key is offered, and chosen, until another answer is picked.
  await expect(page.getByTestId("key-choice-keep")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("key-choice-keep")).toContainText("Keep Gemini, with your key");
  await transcriptsOnly(page);
  await page.getByTestId("setup-next").click(); // sound check
  await page.getByTestId("setup-next").click(); // how to record
  await expect(page.getByTestId("done-summaries")).toContainText("Transcripts only");
  await page.getByTestId("setup-finish").click();
  await expect(page).toHaveURL(/\/$/);
  expect((await (await page.request.get("/api/settings")).json()).config.llm.provider).toBe("none");
});

test("connect_ai_takes_one_plan_per_screen", async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  // Both installed and signed out here, whatever this machine really has.
  await page.route("**/api/llm/status*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.providers = body.providers.map((p: { id: string; needs: string }) =>
      p.id === "claude-subscription" || p.id === "codex-subscription"
        ? { ...p, ready: true, installed: true, signed_in: false, account: undefined }
        : p.needs === "cli"
          ? { ...p, ready: false, signed_in: null, account: undefined }
          : p,
    );
    await route.fulfill({ response, json: body });
  });
  await gotoApp(page, "/welcome");
  await toServices(page);
  await page.getByTestId("service-claude").click();
  await page.getByTestId("service-codex").click();
  await page.getByTestId("setup-next").click();
  await skipCalendar(page);
  await expect(page.getByTestId("ai-count")).toHaveText("1 of 2");
  await expect(page.getByTestId("ai-card-claude")).toBeVisible();
  await expect(page.getByTestId("ai-card-codex")).toHaveCount(0);
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("ai-count")).toHaveText("2 of 2");
  await expect(page.getByTestId("ai-card-codex")).toBeVisible();
  await page.getByTestId("setup-back").click();
  await expect(page.getByTestId("ai-card-claude")).toBeVisible();
  // Back into it from the step after opens on the plan just left, not the first.
  await page.getByTestId("setup-next").click();
  await page.getByTestId("setup-next").click();
  await expect(page.getByTestId("setup-step-key")).toBeVisible();
  await page.getByTestId("setup-back").click();
  await expect(page.getByTestId("ai-count")).toHaveText("2 of 2");
});

test("finish_after_an_install_waits_for_the_plans_before_it_can_write_summaries", async ({ page }) => {
  // A subscription that works, but whose facts arrive late: Finish must not write "none".
  await page.unrouteAll({ behavior: "ignoreErrors" });
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/llm/status*", async (route) => {
    await held;
    const response = await route.fetch();
    const body = await response.json();
    body.providers = body.providers.map((p: { id: string; needs: string }) =>
      p.id === "claude-subscription"
        ? { ...p, ready: true, installed: true, signed_in: true, account: "dana@example.com" }
        : p.needs === "cli"
          ? { ...p, ready: false, signed_in: null, account: undefined }
          : p,
    );
    await route.fulfill({ response, json: body });
  });
  await earlier(page, { "detection.mode": "shadow", "llm.provider": "claude-subscription" });
  await gotoApp(page, "/");
  await expect(page.getByTestId("setup-step-finish")).toBeVisible();
  await expect(page.getByTestId("setup-finish")).toHaveAttribute("aria-busy", "true");
  release();
  await expect(page.getByTestId("setup-finish")).toHaveAttribute("aria-busy", "false");
  await expect(page.getByTestId("done-summaries")).toContainText("Claude");
  await page.getByTestId("setup-finish").click();
  await expect(page).toHaveURL(/\/$/);
  const config = (await (await page.request.get("/api/settings")).json()).config;
  expect(config.llm.provider).toBe("claude-subscription");
});
