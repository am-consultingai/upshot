import type { Page } from "@playwright/test";
import { BASE_URL, CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, gotoSettings, minutesAgo, test } from "./fixtures";

/**
 * The Transcriptions page and its Settings section (D86): files apart from meetings,
 * live rows, downloads, and how to connect Claude.
 */

/** A short 16 kHz mono WAV of noise, built here so the spec carries no binary fixture. */
function wav(seconds: number): Buffer {
  const rate = 16000;
  const samples = rate * seconds;
  const data = Buffer.alloc(samples * 2);
  let seed = 7;
  for (let i = 0; i < samples; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    data.writeInt16LE(((seed % 9000) - 4500) | 0, i * 2);
  }
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVEfmt ", 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

async function runJobs(page: Page) {
  const response = await page.request.post("/api/test/run-jobs", {
    headers: { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` },
  });
  expect(response.ok()).toBeTruthy();
}

const HEBREW_DONE = {
  state: "done" as const,
  source_name: "ראיון.mp4",
  language: "he",
  client: "mcp" as const,
  segments: [
    { speaker: "S1", text: "שלום לכולם ותודה שבאתם" },
    { speaker: "S2", text: "תודה שהזמנתם אותי" },
  ],
};

test("the_empty_page_says_what_it_is_for_and_where_to_connect_claude", async ({ page, seedBody }) => {
  await seedBody({});
  await gotoApp(page, "/transcriptions");
  await expect(page.getByTestId("transcriptions-empty")).toBeVisible();
  await page.getByTestId("transcriptions-empty").getByRole("link").click();
  await expect(page).toHaveURL(/\/settings#transcription$/);
});

test("a_dropped_file_waits_transcribes_and_is_done", async ({ page, seedBody }) => {
  await seedBody({});
  await gotoApp(page, "/transcriptions");
  await page.getByTestId("transcription-language").selectOption("he");
  await page.getByTestId("transcription-file").setInputFiles({
    name: "שיחה.wav",
    mimeType: "audio/wav",
    buffer: wav(8),
  });
  const row = page.getByTestId("transcription-row");
  await expect(row).toHaveCount(1);
  await expect(row).toHaveAttribute("data-state", "pending");
  await expect(row.getByTestId("transcription-state")).toContainText("position 1");
  await expect(row).toContainText("שיחה.wav");

  await runJobs(page);
  // The worker's events reach the row: no reload.
  await expect(row).toHaveAttribute("data-state", "done");
  await expect(row.getByTestId("transcription-state")).toHaveText("Done");
  await expect(row).toContainText("This app");

  // The language choice is remembered in this browser.
  await page.reload();
  await expect(page.getByTestId("transcription-language")).toHaveValue("he");
});

test("each_state_reads_as_itself", async ({ page, seedBody }) => {
  await seedBody({
    transcriptions: [
      { state: "pending", source_name: "waiting.mp3" },
      { state: "running", source_name: "busy.mp3", phase: "transcribe", progress: 0.42 },
      { state: "failed", source_name: "broken.mp3", last_error: "UnsupportedAudio: broken.mp3 has no audio" },
      { state: "cancelled", source_name: "stopped.mp3" },
      HEBREW_DONE,
    ],
  });
  await gotoApp(page, "/transcriptions");
  const row = (name: string) => page.getByTestId("transcription-row").filter({ hasText: name });
  await expect(row("waiting.mp3").getByTestId("transcription-state")).toContainText("Waiting");
  await expect(row("busy.mp3").getByTestId("transcription-state")).toHaveText(/Transcribing · transcribing \d+%/);
  await expect(row("broken.mp3").getByTestId("transcription-error")).toContainText("has no audio");
  await expect(row("broken.mp3").getByTestId("transcription-retry")).toBeVisible();
  await expect(row("stopped.mp3").getByTestId("transcription-state")).toHaveText("Cancelled");
  await expect(row("ראיון.mp4")).toContainText("Claude");
});

test("a_finished_transcript_downloads_and_reads_in_its_own_direction", async ({ page, seedBody }) => {
  await seedBody({ transcriptions: [HEBREW_DONE] });
  await gotoApp(page, "/transcriptions");
  const row = page.getByTestId("transcription-row");

  await row.getByTestId("transcription-download").click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "SRT" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("ראיון.srt");

  await row.getByTestId("transcription-view").click();
  const view = page.getByTestId("transcript-view");
  await expect(view).toHaveAttribute("dir", "rtl");
  await expect(view).toContainText("שלום לכולם");
  await expect(view).toContainText("S2");
});

test("the_page_works_in_hebrew", async ({ page, seedBody }) => {
  await seedBody({ transcriptions: [HEBREW_DONE] });
  await page.request.put("/api/settings", {
    headers: { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` },
    data: { values: { "ui.language": "he" } },
  });
  await gotoApp(page, "/transcriptions");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "תמלולים" })).toBeVisible();
  await expect(page.getByTestId("transcription-state")).toHaveText("הושלם");
});

test("cancel_then_delete", async ({ page, seedBody }) => {
  await seedBody({ transcriptions: [{ state: "pending", source_name: "long.mp3" }] });
  await gotoApp(page, "/transcriptions");
  const row = page.getByTestId("transcription-row");
  await row.getByTestId("transcription-cancel").click();
  await expect(row).toHaveAttribute("data-state", "cancelled");

  await row.getByTestId("transcription-menu").click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByTestId("transcription-row")).toHaveCount(0);
});

test("a_transcription_event_refetches_the_list_and_nothing_else", async ({ page, seedBody, seedMore }) => {
  await seedBody({ meetings: [{ id: "m-weekly", title: "Weekly", state: "RENDERED", started_at: minutesAgo(90) }] });
  await gotoApp(page, "/transcriptions");
  await expect(page.getByTestId("transcriptions-empty")).toBeVisible();
  const fetched: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/") && path !== "/api/status") fetched.push(path);
  });
  // Created through the API, as a program would: the page hears of it by event only.
  await seedMore({ transcriptions: [{ state: "pending", source_name: "from-claude.mp3", client: "mcp" }] });
  await expect(page.getByTestId("transcription-row")).toHaveCount(1);
  expect(fetched.filter((path) => path !== "/api/test/seed")).toEqual(["/api/v1/transcriptions"]);
});

test("file_transcriptions_never_reach_the_library", async ({ page, seedBody }) => {
  await seedBody({ transcriptions: [HEBREW_DONE] });
  await gotoApp(page, "/");
  await expect(page.getByText("ראיון.mp4")).toHaveCount(0);
});

test("g_t_and_the_sidebar_lead_here", async ({ page, seedBody }) => {
  await seedBody({});
  await gotoApp(page, "/");
  await page.keyboard.press("g");
  await page.keyboard.press("t");
  await expect(page.getByTestId("transcriptions-page")).toBeVisible();
  await gotoApp(page, "/");
  await page.getByTestId("nav-transcriptions").click();
  await expect(page).toHaveURL(/\/transcriptions$/);
});

test("a_meeting_waiting_behind_files_says_so", async ({ page, seedBody }) => {
  await seedBody({
    transcriptions: [{ state: "pending", source_name: "first.mp3" }],
    meetings: [{
        id: "m-later",
        title: "Later meeting",
        state: "RECORDED",
        started_at: minutesAgo(30),
        jobs: { transcribe: "pending" },
      },],
  });
  await gotoApp(page, "/m/m-later");
  await expect(page.getByTestId("files-ahead")).toContainText("1 file transcriptions ahead");
});

test("settings_show_how_to_connect_claude_with_this_install", async ({ page, seedBody, playwright }) => {
  await seedBody({});
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await gotoSettings(page, "transcription");
  await expect(page.getByTestId("transcription-copy-wsl-text")).toHaveText(
    'claude mcp add upshot-transcribe -- "/mnt/c/Users/someone/AppData/Local/Programs/Upshot/mcp/upshot-mcp.exe" --wsl-distro "$WSL_DISTRO_NAME"',
  );
  await expect(page.getByTestId("transcription-copy-windows-text")).toContainText(
    "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe",
  );
  await page.getByTestId("transcription-copy-wsl").click();
  await expect(page.getByTestId("transcription-copy-wsl")).toHaveText("Copied");

  await page.getByTestId("transcription-add-desktop").click();
  await expect(page.getByText("Claude Desktop is asking you to install Upshot")).toBeVisible();

  // The switch: off for programs, and the page keeps working.
  await page.getByTestId("transcription-enabled").uncheck();
  await page.reload();
  await expect(page.getByTestId("transcription-enabled")).not.toBeChecked();
  // A program: its own request context, so no cookie of the page's.
  const program = await playwright.request.newContext({ baseURL: BASE_URL });
  expect((await program.get("/api/v1/transcriptions")).status()).toBe(403);
  await program.dispose();
  await gotoApp(page, "/transcriptions");
  await expect(page.getByTestId("transcriptions-empty")).toBeVisible();
  await gotoSettings(page, "transcription");
  await page.getByTestId("transcription-enabled").check();
});
