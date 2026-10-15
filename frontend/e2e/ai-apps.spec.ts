import { BASE_URL } from "../playwright.config";
import { accessKey, expect, gotoApp, gotoSettings, isoAt, test } from "./fixtures";

/**
 * AI apps (D98): the user's own AI tools read meetings over /mcp/ext/ with the access key,
 * Settings shows how to connect each one, and the page lists what they asked.
 */

const ACCEPT = { Accept: "application/json, text/event-stream" };

test("settings_show_the_key_the_commands_and_the_urls", async ({ page, seedBody }) => {
  await seedBody({});
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await gotoSettings(page, "transcription");
  await expect(page.getByTestId("ai-apps-enabled")).toBeChecked();

  // The key: hidden until asked for, copied, and replaced after a confirmation.
  await expect(page.getByTestId("ai-apps-key-text")).toHaveCount(0);
  await page.getByTestId("ai-apps-key-show").click();
  const first = await accessKey(page);
  await expect(page.getByTestId("ai-apps-key-text")).toHaveText(first);
  await page.getByTestId("ai-apps-key-copy").click();
  await expect(page.getByTestId("ai-apps-key-copy")).toHaveText("Copied");
  await page.getByTestId("ai-apps-key-reset").click();
  await page.getByTestId("confirm-dialog").getByRole("button", { name: "New key" }).click();
  await expect(page.getByText("A new access key is in use")).toBeVisible();
  const second = await accessKey(page);
  expect(second).not.toBe(first);
  await expect(page.getByTestId("ai-apps-key-text")).toHaveText(second);

  // Each app by its own command, on Windows or in WSL.
  const bridge = "C:\\Users\\someone\\AppData\\Local\\Programs\\Upshot\\mcp\\upshot-mcp.exe";
  await expect(page.getByTestId("ai-app-codex-text")).toHaveText(`codex mcp add upshot -- "${bridge}"`);
  // ChatGPT reads Codex's file: its own form takes the bridge's path.
  await expect(page.getByTestId("ai-app-codex-steps")).toContainText("Add MCP server");
  await expect(page.getByTestId("ai-app-codex-path-text")).toHaveText(bridge);
  // Found on this computer: a terminal with the command typed in. The e2e server has no
  // terminal to open, so the command is copied and the toast says to paste it.
  await page.getByTestId("ai-app-codex-terminal").click();
  await expect(page.getByText("Copied. Paste it in a terminal and press Enter.")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`codex mcp add upshot -- "${bridge}"`);
  await page.getByTestId("ai-app-antigravity-tab-wsl").click();
  await expect(page.getByTestId("ai-app-antigravity-text")).toContainText("agy mcp add upshot -- \"/mnt/c/");
  await expect(page.getByTestId("ai-app-antigravity-terminal")).toBeVisible();
  // Codex is found on Windows only here: its Windows command, no WSL tab.
  await expect(page.getByTestId("ai-app-codex-tab-wsl")).toHaveCount(0);
  await expect(page.getByTestId("ai-app-claude-code-tab-wsl")).toBeVisible();

  // VS Code is here, so the mcp.json row is with the apps found; the row that takes a
  // URL waits under More apps, closed while apps were found.
  await expect(page.getByTestId("ai-app-mcp-json-text")).toContainText('"mcpServers"');
  await expect(page.getByTestId("ai-apps-url-docker-text")).toHaveCount(0);
  await page.getByTestId("ai-apps-more").click();
  // Apps that take a web address: here, and from Docker.
  await expect(page.getByTestId("ai-apps-url-docker-text")).toHaveText(/^http:\/\/host\.docker\.internal:\d+\/mcp\/ext\/$/);
});

test("the_activity_tab_lists_what_an_ai_app_read", async ({ page, seedBody, playwright }) => {
  await seedBody({
    meetings: [
      {
        id: "m-budget",
        title: "Q4 budget review",
        state: "RENDERED",
        started_at: isoAt(0, 9),
        turns: [{ speaker: "THEM", at_ms: 4000, text: "The budget is cut by ten percent." }],
      },
    ],
  });
  await gotoApp(page, "/transcriptions?tab=activity");
  await expect(page.getByTestId("ai-activity-empty")).toBeVisible();

  const key = await accessKey(page);
  const app = await playwright.request.newContext({ baseURL: BASE_URL });
  const call = (agent: string, name: string, args: Record<string, unknown>) =>
    app.post("/mcp/ext/", {
      headers: { ...ACCEPT, Authorization: `Bearer ${key}`, "X-Upshot-Agent": agent },
      data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } },
    });
  const read = await call("claude-code", "get_transcript", { meeting_id: "m-budget" });
  expect(read.status()).toBe(200);
  expect(JSON.stringify(await read.json())).toContain("The budget is cut by ten percent.");
  await call("codex", "search", { query: "budget" });
  // No key: refused before it reaches a tool.
  expect((await app.post("/mcp/ext/", { headers: ACCEPT, data: {} })).status()).toBe(401);
  await app.dispose();

  const rows = page.getByTestId("ai-activity-row");
  await expect(rows).toHaveCount(2, { timeout: 15_000 });
  await expect(rows.first()).toContainText("codex");
  await expect(rows.nth(1)).toContainText("claude-code");
  await expect(rows.nth(1)).toContainText("Q4 budget review");
  await expect(rows.nth(1).getByTestId("ai-activity-outcome")).toHaveText("Answered");

  await page.getByTestId("ai-activity-filter").selectOption("claude-code");
  await expect(rows).toHaveCount(1);

  await page.getByTestId("ai-activity-clear").click();
  await page.getByTestId("confirm-dialog").getByRole("button", { name: "Clear activity" }).click();
  await expect(page.getByTestId("ai-activity-empty")).toBeVisible();

  // Back to the files, by the tab.
  await page.getByTestId("ai-apps-tab-transcriptions").click();
  await expect(page.getByTestId("transcriptions-page")).toBeVisible();
});

test("meeting_access_can_be_turned_off", async ({ page, seedBody, playwright }) => {
  await seedBody({});
  await gotoSettings(page, "transcription");
  await page.getByTestId("ai-apps-enabled").uncheck();
  await page.reload();
  await expect(page.getByTestId("ai-apps-enabled")).not.toBeChecked();
  const app = await playwright.request.newContext({ baseURL: BASE_URL });
  const reply = await app.post("/mcp/ext/", {
    headers: { ...ACCEPT, Authorization: `Bearer ${await accessKey(page)}` },
    data: {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "search", arguments: { query: "x" } },
    },
  });
  expect(JSON.stringify(await reply.json())).toContain("turned off in Upshot");
  await app.dispose();
  await page.getByTestId("ai-apps-enabled").check();
});
