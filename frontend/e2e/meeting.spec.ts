import { expect, gotoApp, isoAt, test, gotoSettings } from "./fixtures";

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
  // The body is shown; its generic "סיכום" heading is not, so the first paragraph leads.
  await expect(page.getByTestId("summary-html")).toContainText("הועבר ל-Kubernetes");
  await expect(page.getByTestId("summary-html").locator("h1")).toHaveCount(0);
  await expect(page.getByTestId("state-badge")).toHaveText("Ready");
});

test("rename_edits_the_title_in_place", async ({ page, seed }) => {
  await seed([
    { id: "e2e-rename", title: "Weekly sync", state: "RENDERED", started_at: isoAt(0, 10) },
  ]);
  await gotoApp(page, "/m/e2e-rename");

  // Escape leaves the name as it was. The title itself is the control: double-click it.
  await page.getByTestId("meeting-title").dblclick();
  await page.getByTestId("meeting-title-input").fill("Not this");
  await page.getByTestId("meeting-title-input").press("Escape");
  await expect(page.getByTestId("meeting-title")).toHaveText("Weekly sync");

  // Enter saves exactly what was typed — nothing appended. Rename is in the ⋯ menu too.
  await page.getByTestId("overflow-menu-trigger").click();
  await page.locator("[data-testid=overflow-menu-item][data-item=rename]").click();
  await expect(page.getByTestId("meeting-title-input")).toHaveValue("Weekly sync");
  await page.getByTestId("meeting-title-input").fill("Budget review with Dana");
  await page.getByTestId("meeting-title-input").press("Enter");
  await expect(page.getByTestId("meeting-title")).toHaveText("Budget review with Dana");
  await page.reload();
  await expect(page.getByTestId("meeting-title")).toHaveText("Budget review with Dana");
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
  // The transcript and its transport live on the second pill.
  await page.getByTestId("meeting-tab-transcript").click();
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
  // The transcript and its transport live on the second pill.
  await page.getByTestId("meeting-tab-transcript").click();
  expect(await page.evaluate(() => document.dir)).toBe("ltr");
  const transcriptDir = await page
    .getByTestId("transcript")
    .getAttribute("dir");
  expect(["ltr", "rtl"]).toContain(transcriptDir);
});

test("settings_roundtrip", async ({ page }) => {
  await gotoSettings(page, "appearance");
  await page.getByTestId("summary-language").selectOption("he");
  await page.reload();
  await expect(page.getByTestId("summary-language")).toHaveValue("he");
  const html = await page.content();
  expect(html).not.toContain("sk-ant");
  expect(html).not.toContain("app_password");
});

test("data_root_picker", async ({ page }) => {
  await gotoSettings(page, "storage");
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


test("seed_route_is_not_open", async ({ request }) => {
  // The launcher sets UP_TEST_MODE=1, so the route exists here; the *absence* case is
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
  await gotoSettings(page, "audio");
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
  await gotoSettings(page, "audio");
  await expect(page.getByTestId("mic-meter-me")).toBeVisible();

  const opens = async () => {
    const response = await page.request.get("/api/audio/devices");
    return (await response.json()).meter_opens as number;
  };

  // Wait for the streams to actually reach the server before taking a baseline.
  // Becoming visible only means the element mounted; the EventSource connects a
  // moment later, and sampling in between measures the connection rather than a
  // reopen — which is what this test is for.
  await expect.poll(opens, { timeout: 10_000 }).toBeGreaterThan(0);
  const first = await opens();

  await page.waitForTimeout(6000);
  expect(await opens()).toBe(first);
});

test("settings_shows_both_meters", async ({ page }) => {
  await gotoSettings(page, "audio");
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

test("two_tabs_share_the_meters_instead_of_fighting_for_them", async ({ page, context }) => {
  // Job 013 on machine B: three pages on the setup screen reopened the devices about
  // twenty times a second, and neither meter ever showed a level.
  const second = await context.newPage();
  await gotoSettings(page, "audio");
  await gotoSettings(second, "audio");
  for (const tab of [page, second]) {
    await expect(tab.getByTestId("mic-meter-me")).toBeVisible();
    await expect(tab.getByTestId("mic-meter-them")).toBeVisible();
  }
  const opens = async () => {
    const response = await page.request.get("/api/audio/devices");
    return (await response.json()).meter_opens as number;
  };
  await page.waitForTimeout(1500);
  const first = await opens();
  await page.waitForTimeout(5000);
  expect(await opens()).toBe(first);
  // The computer-audio meter is silent whenever nothing plays; it must not blame the microphone.
  await expect(page.getByTestId("mic-meter-hint-them")).not.toContainText(/microphone/i);
  await second.close();
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
  // Audio is the transcript's business; the notice sits where the player would.
  await page.getByTestId("meeting-tab-transcript").click();
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
  await page.getByTestId("overflow-menu-trigger").click();
  await page.getByTestId("overflow-menu").getByText("View prompt").click();
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




test("a_detected_meeting_nudges_on_any_screen", async ({ page, seedBody, seedMore }) => {
  await seedBody({});
  await gotoApp(page, "/settings"); // the nudge must reach any screen, not one of its own
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

/**
 * The verification loop, which is the point of keeping the recording at all.
 *
 * A generated claim leads to the transcript passage behind it; that passage leads
 * to the audio; the audio leads back to the line being spoken. Granola, the
 * best-regarded product in this category, cannot do the last two hops — it
 * transcribes and discards, and its own reviewers list "no playback for
 * verification" as the gap.
 */
test("transcript_seeks_the_audio_and_playback_marks_the_line", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-loop",
      title: "Verifiable",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: [
        { speaker: "ME", at_ms: 0, text: "opening remark" },
        { speaker: "THEM", at_ms: 2000, text: "the second turn" },
      ],
      audio_seconds: 4,
    },
  ]);
  await gotoApp(page, "/m/e2e-loop");
  // The transcript and its transport live on the second pill.
  await page.getByTestId("meeting-tab-transcript").click();

  // Present and driving playback, but never shown: the transport is the UI.
  const audio = page.getByTestId("audio");
  await expect(audio).toHaveCount(1);

  // Clicking the second turn seeks the transport to that turn's start.
  await page.getByTestId("transcript-turn").nth(1).click();
  await expect
    .poll(async () => audio.evaluate((el) => (el as HTMLAudioElement).currentTime), {
      timeout: 5000,
    })
    .toBeGreaterThanOrEqual(1.9);

  // And the line being spoken is marked, so the loop closes back to the text.
  await expect(page.getByTestId("transcript-turn").nth(1)).toHaveAttribute(
    "data-speaking",
    "true",
  );
  await expect(page.getByTestId("transcript-turn").nth(0)).not.toHaveAttribute(
    "data-speaking",
    "true",
  );

  // Sides are the two recorded tracks, which is all this app actually knows:
  // it has no diarisation, so a turn is the microphone or the system, never a name.
  await expect(page.getByTestId("transcript-turn").nth(0)).toHaveAttribute("data-track", "me");
  await expect(page.getByTestId("transcript-turn").nth(1)).toHaveAttribute("data-track", "them");
});

/**
 * The transport replaces the browser's default player.
 *
 * Nothing in this category ships `<audio controls>`; the element survives only as
 * the engine, hidden, while wavesurfer draws and syncs. Playback must therefore be
 * possible before the waveform exists — decoding an hour of audio is real work and
 * pressing play should not wait for it.
 */
test("the_transport_replaces_the_native_player", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-wave",
      title: "Has audio",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: [{ speaker: "ME", at_ms: 0, text: "hello" }],
      audio_seconds: 3,
    },
  ]);

  await gotoApp(page, "/m/e2e-wave");
  // The transcript and its transport live on the second pill.
  await page.getByTestId("meeting-tab-transcript").click();

  // Our own controls, not the browser's.
  await expect(page.getByTestId("play-pause")).toBeVisible();
  await expect(page.getByTestId("skip-back")).toBeVisible();
  await expect(page.getByTestId("skip-forward")).toBeVisible();
  await expect(page.getByTestId("playback-rate")).toBeVisible();

  // The element is present and driving playback, but never shown.
  const audio = page.getByTestId("audio");
  await expect(audio).toHaveCount(1);
  expect(await audio.evaluate((el) => el.hasAttribute("controls"))).toBe(false);

  await page.getByTestId("play-pause").click();
  await expect
    .poll(async () => audio.evaluate((el) => (el as HTMLAudioElement).currentTime), {
      timeout: 5000,
    })
    .toBeGreaterThan(0);
});
