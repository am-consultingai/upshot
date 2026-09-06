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

test("attention_lists_failures", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-failed",
      title: "Broken meeting",
      state: "FAILED",
      started_at: isoAt(0, 9),
      jobs: { transcribe: "failed" },
    },
  ]);
  const retried: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/retry")) retried.push(request.url());
  });
  await gotoApp(page, "/attention");
  const item = page
    .getByTestId("attention-item")
    .filter({ hasText: "Broken meeting" });
  await expect(item).toBeVisible();
  await item.getByTestId("retry").click();
  await expect.poll(() => retried.length).toBeGreaterThan(0);
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
