/**
 * Codex as an AI agent, driven through Settings and a real summarize.
 *
 * The app talks to a fake `codex` (tests/fixtures/codex.py — the same script the
 * Python tests use, answering `--version`, `login status` and `exec` the way the real
 * 0.156.1 build does). Small wrappers pick its mode, and `llm.codex_cli_path` points
 * the running app at one, so each state of the Settings row — not installed, signed
 * out, signed in — and the allowance running out are all reached without OpenAI.
 */
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { APIRequestContext, Page } from "@playwright/test";
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const FOLDER = join(tmpdir(), `upshot-fake-codex-${process.pid}`);
const PATHS: Record<string, string> = {};

// The fake is a shebang script with sh wrappers, as in the Python tests, which skip it
// on Windows for the same reason.
test.skip(process.platform === "win32", "the fake Codex CLI is a shebang script");

test.beforeAll(() => {
  mkdirSync(FOLDER, { recursive: true });
  execFileSync(
    join(ROOT, ".venv/bin/python"),
    ["-c", `from pathlib import Path; from tests.fixtures.codex import install_fake_codex; install_fake_codex(Path(${JSON.stringify(FOLDER)}))`],
    { cwd: ROOT },
  );
  for (const mode of ["ok", "signed-out", "quota"]) {
    const wrapper = join(FOLDER, `codex-${mode}`);
    writeFileSync(wrapper, `#!/bin/sh\nFAKE_CODEX_MODE=${mode} exec "${join(FOLDER, "codex")}" "$@"\n`);
    chmodSync(wrapper, 0o755);
    PATHS[mode] = wrapper;
  }
  PATHS.missing = join(FOLDER, "no-such-codex");
});

async function settings(request: APIRequestContext, values: Record<string, unknown>) {
  const response = await request.put("/api/settings", { headers: HEADERS, data: { values } });
  return response;
}

test.beforeEach(async ({ request, seed }) => {
  await seed([]);
  await settings(request, { "detection.decided": true, "llm.provider": "fake", "llm.fallback_provider": "" });
});

test.afterEach(async ({ request }) => {
  // Put everything back: a real-looking provider left selected stalls later specs.
  await settings(request, { "llm.provider": "fake", "llm.fallback_provider": "" });
  await settings(request, { "llm.codex_cli_path": "codex" });
});

const row = (page: Page) => page.locator('[data-provider="codex-subscription"]');

test("codex_not_installed_offers_the_install_and_refuses_to_be_chosen", async ({ page, request }) => {
  await settings(request, { "llm.codex_cli_path": PATHS.missing });
  await gotoApp(page, "/settings#summaries");
  const codex = row(page);
  await expect(codex).toHaveAttribute("data-ready", "false");
  // Named for the tool and whose plan it spends, never "GPT" (OpenAI's brand rules, D58).
  await expect(codex).toContainText("Codex CLI (your own ChatGPT plan)");
  await expect(codex.getByTestId("provider-ready")).toHaveText("Not installed");
  await expect(codex.getByTestId("provider-hint")).toHaveText("Codex is not installed on this machine.");
  await expect(codex.getByTestId("provider-install")).toBeVisible();
  // What Install will do is stated before it is pressed. On Windows that is the exact
  // installer command; this Linux test server has no installer to run, and says so.
  await expect(codex.getByTestId("provider-install-hint")).toContainText(
    process.platform === "win32" ? "codex" : "opens the install guide",
  );
  await expect(codex.getByTestId("provider-plan")).toContainText("ChatGPT plan's Codex allowance");

  // Choosing it is refused rather than failing after the next meeting.
  const refused = await settings(request, { "llm.provider": "codex-subscription" });
  expect(refused.status()).toBe(409);
  expect(await refused.text()).toContain("Codex is not installed");
  await codex.getByTestId("provider-select").click();
  await expect(codex).toHaveAttribute("data-active", "false");
});

test("codex_signed_out_asks_for_the_sign_in_and_is_flagged", async ({ page, request }) => {
  await settings(request, { "llm.codex_cli_path": PATHS["signed-out"] });
  await gotoApp(page, "/settings#summaries");
  const codex = row(page);
  await expect(codex).toHaveAttribute("data-ready", "true");
  await expect(codex).toHaveAttribute("data-signed-in", "false");
  await expect(codex.getByTestId("provider-ready")).toHaveText("Not signed in");
  await expect(codex.getByTestId("provider-signin")).toBeEnabled();
  await expect(codex.getByTestId("provider-hint")).toContainText("OpenAI's own sign-in");

  // Installed but signed out may be chosen — and the "!" says it will not work yet.
  await codex.getByTestId("provider-select").click();
  await expect(codex).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("settings-warning-summaries")).toBeVisible();
});

test("codex_signed_in_tests_ok_summarizes_a_meeting_and_takes_a_fallback", async ({ page, request, seed }) => {
  await seed([
    {
      id: "e2e-codex",
      title: "Pricing review",
      state: "RENDERED",
      started_at: isoAt(0, 9),
      language: "en",
      turns: [
        { speaker: "ME", at_ms: 0, end_ms: 4000, text: "Let us settle the pricing for the new tier." },
        { speaker: "THEM", at_ms: 4000, end_ms: 9000, text: "I will send the deck by Thursday." },
      ],
      summary_html: "<p>An older summary.</p>",
    },
  ]);
  await settings(request, { "llm.codex_cli_path": PATHS.ok, "detection.decided": true });
  await gotoApp(page, "/settings#summaries");
  const codex = row(page);
  await expect(codex.getByTestId("provider-ready")).toHaveText("Signed in");
  await codex.getByTestId("provider-select").click();
  await expect(codex).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("settings-warning-summaries")).toHaveCount(0);

  await codex.getByTestId("provider-test").click();
  await expect(codex.getByTestId("provider-test-result")).toHaveText("Working");

  // The allowance fallback is a deliberate choice, shown only for a subscription.
  const fallback = page.getByTestId("provider-fallback");
  await expect(fallback).toHaveValue("");
  await fallback.selectOption("ollama");
  await page.reload();
  await expect(page.getByTestId("provider-fallback")).toHaveValue("ollama");
  await page.getByTestId("provider-fallback").selectOption("");

  // A real summarize, through the worker, by the fake Codex.
  await gotoApp(page, "/m/e2e-codex");
  await page.getByTestId("overflow-menu-trigger").click();
  await page.locator("[data-testid=overflow-menu-item][data-item=resummarize]").click();
  await expect(page.getByTestId("stage-running")).toBeVisible();
  // The e2e worker runs on request, so the pipeline runs now rather than on a timer.
  await request.post("/api/test/run-jobs", { headers: HEADERS });
  await expect(page.getByTestId("summary-html")).toContainText("Summarized by the fake Codex.", {
    timeout: 15_000,
  });
  const meeting = await (await request.get("/api/meetings/e2e-codex", { headers: HEADERS })).json();
  expect(meeting.summarized_by?.provider).toBe("codex-subscription");
});

test("an_exhausted_allowance_waits_and_says_why_instead_of_failing", async ({ page, request, seed }) => {
  await seed([
    {
      id: "e2e-quota",
      title: "Budget sync",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      language: "en",
      turns: [{ speaker: "ME", at_ms: 0, end_ms: 3000, text: "The budget is approved." }],
      summary_html: "<p>An older summary.</p>",
    },
  ]);
  await settings(request, { "llm.codex_cli_path": PATHS.quota, "detection.decided": true });
  expect((await settings(request, { "llm.provider": "codex-subscription" })).status()).toBe(200);

  await gotoApp(page, "/m/e2e-quota");
  await page.getByTestId("overflow-menu-trigger").click();
  await page.locator("[data-testid=overflow-menu-item][data-item=resummarize]").click();
  await expect(page.getByTestId("stage-running")).toBeVisible();
  await request.post("/api/test/run-jobs", { headers: HEADERS });
  // Not a failure: the job waits, and the page says so in plain words.
  await expect(page.getByTestId("stage-waiting-reason")).toContainText("Codex allowance is used up", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("stage-failed")).toHaveCount(0);
  const attention = await (await request.get("/api/attention", { headers: HEADERS })).json();
  expect(JSON.stringify(attention)).toContain("e2e-quota");
});
