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
  turns?: { speaker: string; at_ms: number; end_ms?: number; text: string }[];
  /** How long it ran, so a row and a chip can say "42m". */
  duration_s?: number;
  ended_at?: string;
  tags?: string[];
  /** A name for a transcript speaker slot: { THEM_1: "Dana Levi" }. */
  speaker_names?: Record<string, string>;
  /** Topic sections, as the summarizer would have divided the conversation. */
  chapters?: { title: string; start_ms: number; end_ms?: number | null }[];
  sensitive?: boolean;
  summary_html?: string;
  notes?: Record<string, unknown>;
  /** Action items, as a summary would have left them. `done` ticks one off. */
  action_items?: {
    who?: string;
    what: string;
    due?: string;
    /** YYYY-MM-DD, as the summarizer resolves it. */
    due_at?: string | null;
    detail?: string;
    snoozed_until?: string;
    source?: "model" | "user";
    at_ms?: number;
    done?: boolean;
  }[];
  /** The transcript's language, which decides the transcript block's direction. */
  language?: string;
  /** Write this many seconds of real two-track audio, so the page shows a player. */
  audio_seconds?: number;
  /** Pretend the retention sweep already removed this meeting's audio. */
  audio_deleted_at?: string;
  /** Matched to this seeded calendar event. */
  calendar?: { calendar_id: string; event_id: string; participants?: string[] };
}

/** A Google Calendar event, as a sync would have left it in the cache. */
export interface SeedEvent {
  id: string;
  calendar_id?: string;
  start: string;
  end: string;
  title?: string;
  all_day?: boolean;
  attendees?: string[];
  response?: string;
  conference_url?: string | null;
}

export interface SeedBody {
  /** Clear the library, the events and the saved appearance first. Also marks
   * first-run setup done, so no spec lands on /welcome by accident. */
  reset?: boolean;
  /** First-run setup finished or not; only welcome.spec.ts asks for `false`. */
  setup_done?: boolean;
  meetings?: SeedMeeting[];
  calendar_events?: SeedEvent[];
  detector_events?: {
    process?: string;
    window_title?: string;
    peak_score?: number;
    outcome?: string;
    evidence?: { code: string; weight: number; detail: string }[];
  }[];
}

// With UP_E2E_CDP set, the specs run against a browser on another machine — in practice
// a Chrome on Windows, driven from WSL, because Chromium inside WSL produces no animation
// frames at all and every click times out waiting for the page to settle. The page then
// resolves 127.0.0.1 on the browser's machine, which is exactly where the app under test
// is listening.
//
// It replaces the `browser` fixture itself, so nothing asks for Playwright's own
// Chromium: a `context` override that took the stock context as a fallback launched it
// anyway, and on machine B's Windows Node, which has none, 104 specs failed on
// "Executable doesn't exist" (job 006). The stock `context` then comes from
// `browser.newContext(contextOptions)`, so `baseURL` and the other options still apply.
const endpoint = process.env.UP_E2E_CDP;
const browsers = endpoint
  ? base.extend({
      browser: [
        async ({ playwright }, use) => {
          const remote = await playwright.chromium.connectOverCDP(endpoint);
          await use(remote);
          await remote.close();
        },
        { scope: "worker" },
      ],
    })
  : base;

export const test = browsers.extend<{
  seed: (meetings: SeedMeeting[]) => Promise<void>;
  seedBody: (body: SeedBody) => Promise<void>;
  /** Seed *without* clearing first: for asserting what a live event does to an open page. */
  seedMore: (body: SeedBody) => Promise<void>;
}>({
  context: async ({ context }, use) => {
    await context.addCookies([
      { name: "up_session", value: SESSION, url: BASE_URL },
      { name: "up_csrf", value: CSRF, url: BASE_URL },
    ]);
    await use(context);
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
  section: "audio" | "speech" | "appearance" | "storage" | "summaries" | "prompt" = "audio",
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

export function isoAt(dayOffset: number, hour: number, minute = 0): string {
  const date = new Date();
  date.setDate(date.getDate() - dayOffset);
  date.setHours(hour, minute, 0, 0);
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

/** A timestamp `minutes` from now (negative for the past), for events around the present. */
export function minutesFromNow(minutes: number): string {
  return minutesAgo(-minutes);
}

/** A local calendar date `days` from today (negative for the past), as YYYY-MM-DD. */
export function dateFromToday(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
