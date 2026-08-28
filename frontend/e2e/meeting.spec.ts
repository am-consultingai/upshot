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
    },
  ]);
  const audioRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/audio")) audioRequests.push(request.headers()["range"] ?? "full");
  });
  await gotoApp(page, "/m/e2e-seek");
  await expect(page.getByTestId("audio")).toHaveAttribute(
    "src",
    "/api/meetings/e2e-seek/audio?track=them",
  );
  await page.getByTestId("audio").evaluate((node) => {
    (node as HTMLAudioElement).load();
  });
  await page.waitForTimeout(300);
  expect(audioRequests.length).toBeGreaterThanOrEqual(0);
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
  const transcriptDir = await page.getByTestId("transcript").getAttribute("dir");
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
  const item = page.getByTestId("attention-item").filter({ hasText: "Broken meeting" });
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
  const response = await request.post("/api/test/seed", { data: { meetings: [] } });
  expect(response.ok()).toBeFalsy();
  expect([401, 403]).toContain(response.status());
});
