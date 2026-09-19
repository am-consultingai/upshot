import {
  test as base,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { BASE_URL, CSRF, SESSION } from "../playwright.config";

export interface SeedMeeting {
  id: string;
  title?: string;
  state?: string;
  started_at?: string;
  jobs?: Record<string, string>;
  turns?: { speaker: string; at_ms: number; text: string }[];
  summary_html?: string;
  notes?: Record<string, unknown>;
  /** Write this many seconds of real two-track audio, so the page shows a player. */
  audio_seconds?: number;
  /** Pretend the retention sweep already removed this meeting's audio. */
  audio_deleted_at?: string;
}

export interface SeedBody {
  meetings?: SeedMeeting[];
  detector_events?: {
    process?: string;
    window_title?: string;
    peak_score?: number;
    outcome?: string;
    evidence?: { code: string; weight: number; detail: string }[];
  }[];
}

export const test = base.extend<{
  seed: (meetings: SeedMeeting[]) => Promise<void>;
  seedBody: (body: SeedBody) => Promise<void>;
  /** Seed *without* clearing first: for asserting what a live event does to an open page. */
  seedMore: (body: SeedBody) => Promise<void>;
}>({
  // With UP_E2E_CDP set, the specs run against a browser on another machine — in
  // practice a Chrome on Windows, driven from WSL, because Chromium inside WSL produces
  // no animation frames at all and every click times out waiting for the page to settle.
  // The page then resolves 127.0.0.1 on the browser's machine, which is exactly where the
  // app under test is listening.
  context: async ({ playwright, contextOptions, context: local }, use) => {
    const endpoint = process.env.UP_E2E_CDP;
    const remote = endpoint ? await playwright.chromium.connectOverCDP(endpoint) : null;
    // A fresh context, not `contexts()[0]`: the browser's default context carries none of
    // the test options, so `baseURL` is unset and every relative `goto("/")` has nothing
    // to resolve against.
    const context = remote ? await remote.newContext(contextOptions) : local;
    await context.addCookies([
      { name: "up_session", value: SESSION, url: BASE_URL },
      { name: "up_csrf", value: CSRF, url: BASE_URL },
    ]);
    await use(context);
    if (remote) {
      for (const page of context.pages()) await page.close();
      await remote.close();
    }
  },
  seed: async ({ request }, use) => {
    await use(async (meetings: SeedMeeting[]) => {
      const response = await request.post("/api/test/seed", {
        headers: {
          "X-CSRF-Token": CSRF,
          Cookie: `up_session=${SESSION}; up_csrf=${CSRF}`,
        },
        data: { meetings, reset: true },
      });
      expect(response.ok()).toBeTruthy();
    });
  },
  seedMore: async ({ request }, use) => {
    await use(async (body: SeedBody) => {
      const response = await request.post("/api/test/seed", {
        headers: {
          "X-CSRF-Token": CSRF,
          Cookie: `up_session=${SESSION}; up_csrf=${CSRF}`,
        },
        data: { ...body },
      });
      expect(response.ok()).toBeTruthy();
    });
  },
  seedBody: async ({ request }, use) => {
    await use(async (body: SeedBody) => {
      const response = await request.post("/api/test/seed", {
        headers: {
          "X-CSRF-Token": CSRF,
          Cookie: `up_session=${SESSION}; up_csrf=${CSRF}`,
        },
        data: { ...body, reset: true },
      });
      expect(response.ok()).toBeTruthy();
    });
  },
});

export { expect };

/** Every spec starts from an empty library, whatever ran before it. */
export async function reset(request: APIRequestContext): Promise<void> {
  const response = await request.post("/api/test/seed", {
    headers: {
      "X-CSRF-Token": CSRF,
      Cookie: `up_session=${SESSION}; up_csrf=${CSRF}`,
    },
    data: { meetings: [], reset: true },
  });
  expect(response.ok()).toBeTruthy();
}

export async function gotoApp(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await expect(page.getByTestId("app")).toBeVisible();
  /*
   * Wait for the event stream, not just for the page.
   *
   * Server-sent events have no replay, so anything published between the page
   * rendering and its EventSource connecting is lost. A spec that seeds straight
   * after gotoApp was racing that window: the detection-nudge test failed about
   * one run in two, always by the nudge simply never arriving.
   */
  await expect(page.locator("html")).toHaveAttribute("data-stream", "open", {
    timeout: 10_000,
  });
}

/**
 * Open Settings at a given section.
 *
 * Settings is sectioned now, so a control only exists while its section is
 * showing. The section lives in the hash, which is also what a meeting's
 * "View prompt" link uses.
 */
export async function gotoSettings(
  page: Page,
  section: "audio" | "appearance" | "storage" | "summaries" | "prompt" = "audio",
): Promise<void> {
  await gotoApp(page, `/settings#${section}`);
  await expect(page.getByTestId(`settings-section-${section}`)).toHaveAttribute(
    "aria-current",
    "page",
  );
}

/** A timestamp `minutes` in the past — a "recording now" card needs one. */
export function minutesAgo(minutes: number): string {
  const date = new Date(Date.now() - minutes * 60_000);
  const pad = (value: number) => String(value).padStart(2, "0");
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

export function isoAt(dayOffset: number, hour: number): string {
  const date = new Date();
  date.setDate(date.getDate() - dayOffset);
  date.setHours(hour, 0, 0, 0);
  const pad = (value: number) => String(value).padStart(2, "0");
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:00` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}
