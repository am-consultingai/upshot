import { defineConfig, devices } from "@playwright/test";
import { BASE_URL } from "./playwright.config";

/**
 * The same specs, run against the app on Windows through a browser on Windows.
 *
 * No `webServer`: the harness (`scripts/windows/e2e.py`) owns the app instance and the
 * browser, because both live on the other side of the WSL boundary. `UP_E2E_CDP` points
 * the fixtures at that browser.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "../artifacts/e2e-windows.json" }]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
});
