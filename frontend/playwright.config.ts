import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.MA_E2E_PORT ?? 8123);
export const BASE_URL = `http://127.0.0.1:${PORT}`;
export const SESSION = process.env.MA_E2E_SESSION ?? "e2e-session-secret";
export const CSRF = process.env.MA_E2E_CSRF ?? "e2e-csrf-secret";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    // the project venv directly: `uv run` can block on a lock held by another uv
    command: process.env.MA_E2E_PYTHON ?? ".venv/bin/python scripts/e2e_server.py",
    cwd: "..",
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      MA_E2E_PORT: String(PORT),
      MA_E2E_SESSION: SESSION,
      MA_E2E_CSRF: CSRF,
      MA_TEST_MODE: "1",
    },
  },
});
