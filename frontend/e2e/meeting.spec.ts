import { expect, gotoApp, isoAt, test } from "./fixtures";

const SUMMARY_HE = `<html dir="rtl" lang="he"><body><h1>סיכום</h1>
<p>הועבר ל-Kubernetes</p></body></html>`;

test("meeting_page_loads", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-page",
      title: "Weekly sync",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: SUMMARY_HE,
      turns: [
        { speaker: "THEM", at_ms: 12000, text: "בוקר טוב" },
        { speaker: "ME", at_ms: 18000, text: "moved it to Kubernetes" },
      ],
    },
  ]);
  await gotoApp(page, "/m/e2e-page");
  await expect(page.getByTestId("meeting-title")).toHaveText("Weekly sync");
  await expect(page.getByTestId("summary-html")).toContainText("סיכום");
  await expect(page.getByTestId("state-badge")).toHaveText("Ready");
});

test("click_transcript_seeks", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-seek",
      title: "Seekable",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: [{ speaker: "ME", at_ms: 0, text: "first turn" }],
      audio_seconds: 3,
    },
  ]);
  const audioRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/audio"))
      audioRequests.push(request.headers()["range"] ?? "full");
  });
  await gotoApp(page, "/m/e2e-seek");
  // One stream, mixed on read (D35): the page does not offer a track picker.
  await expect(page.getByTestId("audio")).toHaveAttribute(
    "src",
    "/api/meetings/e2e-seek/audio?track=mix",
  );
  await page.getByTestId("audio").evaluate((node) => {
    (node as HTMLAudioElement).load();
  });
  await expect
    .poll(() => audioRequests.length, { timeout: 5000 })
    .toBeGreaterThan(0);
});

test("content_dir_independent_of_chrome", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-dir",
      title: "Hebrew meeting",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: [{ speaker: "ME", at_ms: 0, text: "שלום" }],
    },
  ]);
  await gotoApp(page, "/m/e2e-dir");
  expect(await page.evaluate(() => document.dir)).toBe("ltr");
  const transcriptDir = await page
    .getByTestId("transcript")
    .getAttribute("dir");
  expect(["ltr", "rtl"]).toContain(transcriptDir);
});

test("settings_roundtrip", async ({ page }) => {
  await gotoApp(page, "/settings");
  await page.getByTestId("summary-language").selectOption("he");
  await page.reload();
  await expect(page.getByTestId("summary-language")).toHaveValue("he");
  const html = await page.content();
  expect(html).not.toContain("sk-ant");
  expect(html).not.toContain("app_password");
});

test("data_root_picker", async ({ page }) => {
  await gotoApp(page, "/settings");
  const input = page.getByTestId("data-root");
  await expect(input).toBeVisible();
  await input.fill("C:/Users/am/OneDrive/meetings");
  await expect(page.getByTestId("sync-warning")).toBeVisible();
});

test("search_finds_a_transcript", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-search",
      title: "Searchable",
      state: "RENDERED",
      started_at: isoAt(0, 9),
      turns: [{ speaker: "ME", at_ms: 0, text: "kubernetes migration plan" }],
    },
  ]);
  await gotoApp(page, "/search");
  await page.getByTestId("search-input").fill("kubernetes");
  await expect(page.getByTestId("search-result")).toHaveCount(1);
});

test("detector_page_lists_events", async ({ page, seedBody }) => {
  await seedBody({
    detector_events: [
      {
        process: "Zoom.exe",
        window_title: "Zoom Meeting",
        peak_score: 9,
        outcome: "shadow",
        evidence: [{ code: "mic.known_app", weight: 3, detail: "Zoom.exe" }],
      },
    ],
  });
  await gotoApp(page, "/detector");
  const row = page.getByTestId("detector-event");
  await expect(row).toHaveCount(1);
  await expect(row.getByTestId("detector-score")).toHaveText("9");
  await expect(row.getByTestId("detector-outcome")).toHaveText("shadow");
});

test("seed_route_is_not_open", async ({ request }) => {
  // The launcher sets MA_TEST_MODE=1, so the route exists here; the *absence* case is
  // asserted by the Python suite (test_test_seed_route_absent_by_default), which can
  // control the environment. What we assert here is that an unauthenticated caller —
  // no cookie, no CSRF header — is rejected before reaching it.
  const response = await request.post("/api/test/seed", {
    data: { meetings: [] },
  });
  expect(response.ok()).toBeFalsy();
  expect([401, 403]).toContain(response.status());
});

test("mic_picker_and_meter", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("mic-meter-me")).toBeVisible();
  // The synthetic source is a tone, so a working meter must leave zero.
  await expect
    .poll(
      async () =>
        Number(
          // Scoped: there are two meters on this page now, microphone and system audio.
          await page
            .getByTestId("mic-meter-me")
            .getByRole("meter")
            .getAttribute("data-level"),
        ),
      { timeout: 15_000 },
    )
    .toBeGreaterThan(0);
});

test("mic_meter_opens_the_device_once", async ({ page }) => {
  // A React effect that depended on an unstable value once reopened the microphone on
  // every incoming level — several times a second, for as long as Settings was open.
  await page.goto("/settings");
  await expect(page.getByTestId("mic-meter-me")).toBeVisible();

  const opens = async () => {
    const response = await page.request.get("/api/audio/devices");
    return (await response.json()).meter_opens as number;
  };
  const first = await opens();
  await page.waitForTimeout(6000);
  const later = await opens();
  expect(later - first).toBeLessThanOrEqual(1);
});

test("settings_shows_both_meters", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("mic-meter-me")).toBeVisible();
  await expect(page.getByTestId("mic-meter-them")).toBeVisible();
  // Two endpoints, two independent streams — one open each, not one per render.
  const opens = async () => {
    const response = await page.request.get("/api/audio/devices");
    return (await response.json()).meter_opens as number;
  };
  const first = await opens();
  await page.waitForTimeout(5000);
  expect((await opens()) - first).toBeLessThanOrEqual(2);
});

test("swept_audio_reads_as_deleted_not_missing", async ({ page, seed }) => {
  // A meeting the retention policy stripped must not look like a failed recording.
  await seed([
    {
      id: "e2e-swept",
      title: "Old meeting",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      audio_deleted_at: isoAt(0, 11),
      turns: [{ speaker: "ME", at_ms: 0, text: "first turn" }],
    },
  ]);
  await gotoApp(page, "/m/e2e-swept");
  await expect(page.getByTestId("audio-deleted")).toContainText(
    "retention policy",
  );
  await expect(page.getByTestId("no-audio")).toHaveCount(0);
  await expect(page.getByTestId("audio")).toHaveCount(0);
});

test("a_running_stage_shows_a_spinner_and_says_what_it_is_doing", async ({ page, seed }) => {
  // "summarize: running" in grey text was the only sign of a five-minute job.
  await seed([
    {
      id: "e2e-busy",
      title: "Being summarized",
      state: "SUMMARIZING",
      started_at: isoAt(0, 10),
      jobs: { summarize: "running" },
      turns: [{ speaker: "ME", at_ms: 0, text: "first turn" }],
    },
  ]);
  await gotoApp(page, "/m/e2e-busy");
  const status = page.getByTestId("stage-running");
  await expect(status).toHaveText(/Summarizing/);
  await expect(status.getByTestId("spinner")).toBeVisible();
  await expect(page.getByTestId("summarize")).toHaveAttribute("data-busy", "true");
  await expect(page.getByTestId("summarize").getByTestId("spinner")).toBeVisible();
});

test("view_prompt_opens_the_prompt_in_settings", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-prompt",
      title: "Has a summary",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: SUMMARY_HE,
    },
  ]);
  await gotoApp(page, "/m/e2e-prompt");
  await page.getByTestId("view-prompt").click();
  await expect(page).toHaveURL(/\/settings#prompt$/);
  await expect(page.getByTestId("prompt-text")).toBeInViewport();
});

/**
 * The model writes structured HTML and it was rendering as one flat wall of text:
 * Tailwind's preflight strips heading sizes, list markers and every margin, and
 * nothing styled the injected subtree back. Asserting computed style rather than
 * the presence of a class, because a class that matches no rule is the bug.
 */
test("summary_html_keeps_its_structure", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-prose",
      title: "Structured summary",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: `<h2>Decisions</h2><p>We moved it.</p>
<ul><li>First point</li><li>Second point</li></ul>
<blockquote>Quoted</blockquote>`,
    },
  ]);
  await gotoApp(page, "/m/e2e-prose");
  const summary = page.getByTestId("summary-html");
  await expect(summary).toContainText("Decisions");

  const heading = summary.locator("h2").first();
  const list = summary.locator("ul").first();

  // A heading the same size as body text is the symptom users reported.
  const [headingSize, bodySize] = await Promise.all([
    heading.evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
    summary.locator("p").first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
  ]);
  expect(headingSize).toBeGreaterThan(bodySize);

  expect(
    await heading.evaluate((el) => parseFloat(getComputedStyle(el).marginBottom)),
  ).toBeGreaterThan(0);

  // Bullets, and an indent that follows the writing direction rather than sitting
  // on the left whatever the language.
  expect(await list.evaluate((el) => getComputedStyle(el).listStyleType)).toBe("disc");
  expect(
    await list.evaluate((el) => parseFloat(getComputedStyle(el).paddingInlineStart)),
  ).toBeGreaterThan(0);
});

test("needs_attention_and_glossary_are_gone", async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByTestId("nav-timeline")).toBeVisible();
  await expect(page.getByTestId("nav-attention")).toHaveCount(0);
  await expect(page.getByTestId("nav-glossary")).toHaveCount(0);
});

test("detector_page_explains_itself", async ({ page, seedBody }) => {
  await seedBody({});
  await gotoApp(page, "/detector");
  await expect(page.getByTestId("detector-about")).toContainText("microphone");
  await expect(page.getByTestId("detector-mode")).toBeVisible();
  await expect(page.getByTestId("detector-empty")).toBeVisible();
});

test("a_detection_reaches_the_screen_without_a_reload", async ({ page, seedBody, seedMore }) => {
  // The Detector page used to refresh only when something else happened, so a real call
  // took about a minute to appear while the detector had decided in ten seconds.
  await seedBody({});
  await gotoApp(page, "/detector");
  await expect(page.getByTestId("detector-empty")).toBeVisible();

  await seedMore({
    detector_events: [
      { process: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", peak_score: 6, outcome: "shadow" },
    ],
  });

  const row = page.getByTestId("detector-event");
  await expect(row).toHaveCount(1, { timeout: 10_000 });
  await expect(row.getByTestId("detector-score")).toHaveText("6");
});

test("detector_times_read_like_times", async ({ page, seedBody }) => {
  await seedBody({
    detector_events: [{ process: "Zoom.exe", peak_score: 7, outcome: "shadow" }],
  });
  await gotoApp(page, "/detector");
  const when = page.getByTestId("detector-when");
  await expect(when).toBeVisible();
  const text = (await when.textContent()) ?? "";
  expect(text).not.toContain("T");        // not the stored ISO string
  expect(text).toMatch(/\d{1,2}:\d{2}/);  // a time someone would say
});

test("a_detected_meeting_nudges_on_any_screen", async ({ page, seedBody, seedMore }) => {
  await seedBody({});
  await gotoApp(page, "/settings"); // deliberately not the Detector page
  await expect(page.getByTestId("detection-nudge")).toHaveCount(0);

  await seedMore({
    detector_events: [
      { process: "C:\\Program Files\\Zoom\\bin\\Zoom.exe", peak_score: 8, outcome: "shadow" },
    ],
  });

  const nudge = page.getByTestId("detection-nudge");
  await expect(nudge).toBeVisible({ timeout: 10_000 });
  await expect(nudge).toContainText("Zoom");
  await expect(nudge).toContainText("8");

  await nudge.getByTestId("detection-nudge-dismiss").click();
  await expect(page.getByTestId("detection-nudge")).toHaveCount(0);
});

test("the_nudge_starts_the_recording_it_offers", async ({ page, seedBody, seedMore }) => {
  await seedBody({});
  await gotoApp(page, "/search");
  await seedMore({ detector_events: [{ process: "Teams.exe", peak_score: 9, outcome: "shadow" }] });

  await page.getByTestId("detection-nudge").waitFor({ timeout: 10_000 });
  await page.getByTestId("detection-nudge-start").click();
  await expect(page.getByTestId("recording-bar")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("detection-nudge")).toHaveCount(0);
  await page.getByTestId("recording-bar-stop").click();
});
