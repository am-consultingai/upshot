import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";
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
}>({
  context: async ({ context }, use) => {
    await context.addCookies([
      { name: "ma_session", value: SESSION, url: BASE_URL },
      { name: "ma_csrf", value: CSRF, url: BASE_URL },
    ]);
    await use(context);
  },
  seed: async ({ request }, use) => {
    await use(async (meetings: SeedMeeting[]) => {
      const response = await request.post("/api/test/seed", {
        headers: { "X-CSRF-Token": CSRF, Cookie: `ma_session=${SESSION}; ma_csrf=${CSRF}` },
        data: { meetings, reset: true },
      });
      expect(response.ok()).toBeTruthy();
    });
  },
  seedBody: async ({ request }, use) => {
    await use(async (body: SeedBody) => {
      const response = await request.post("/api/test/seed", {
        headers: { "X-CSRF-Token": CSRF, Cookie: `ma_session=${SESSION}; ma_csrf=${CSRF}` },
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
    headers: { "X-CSRF-Token": CSRF, Cookie: `ma_session=${SESSION}; ma_csrf=${CSRF}` },
    data: { meetings: [], reset: true },
  });
  expect(response.ok()).toBeTruthy();
}

export async function gotoApp(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await expect(page.getByTestId("app")).toBeVisible();
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
