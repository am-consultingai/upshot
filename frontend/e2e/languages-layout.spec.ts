import type { Page } from "@playwright/test";
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, reset, test } from "./fixtures";
import { parityBody } from "./parity-data";

/**
 * Every language fits (z8tj1hfgyu). German and French run a third longer than English:
 * the record button, the calendar filter and two Settings sections were cut off, and a
 * setup lead ran under the cards below it. Controls wrap rather than cut; text a
 * component shortens on purpose (an ellipsis) is not counted.
 */
const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };
const LANGUAGES = ["en", "he", "de", "es", "fr"] as const;
const SCREENS = [
  { path: "/", ready: "calendar-timegrid" },
  { path: "/m/m-onboarding", ready: "summary-html" },
  { path: "/actions", ready: "action-group" },
  { path: "/settings#requests", ready: "transcriptions-page" },
  { path: "/settings#calendar", ready: "settings-nav" },
  { path: "/settings#transcription", ready: "settings-nav" },
] as const;

/** Controls whose text is wider than their box and not shortened on purpose. */
async function cutOff(page: Page): Promise<string[]> {
  await page.waitForTimeout(300);
  return page.evaluate(() =>
    Array.from(
      document.querySelectorAll<HTMLElement>("button, a, [role=tab], label, select, h1, h2, h3, th, [role=menuitem]"),
    )
      .filter((el) => {
        if (el.clientWidth <= 1 || !(el.textContent ?? "").trim()) return false;
        if (getComputedStyle(el).textOverflow === "ellipsis" || el.closest(".truncate")) return false;
        return el.scrollWidth > el.clientWidth + 1;
      })
      .map((el) => `${el.tagName} "${(el.textContent ?? "").trim().slice(0, 40)}" ${el.scrollWidth}>${el.clientWidth}`),
  );
}

async function speak(request: import("@playwright/test").APIRequestContext, language: string) {
  await request.put("/api/settings", {
    headers: HEADERS,
    data: { values: { "ui.language": language, "llm.provider": "fake" } },
  });
}

for (const language of LANGUAGES) {
  test(`the_main_screens_fit_in_${language}`, async ({ page, seedBody, request }) => {
    await seedBody(parityBody());
    await speak(request, language);
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const screen of SCREENS) {
      await gotoApp(page, screen.path);
      await expect(page.getByTestId(screen.ready).first()).toBeVisible();
      expect(await cutOff(page), screen.path).toEqual([]);
    }
  });

  test(`setup_fits_in_${language}`, async ({ page, seedBody, request }) => {
    await seedBody({ setup_done: false });
    await speak(request, language);
    await gotoApp(page, "/welcome");
    for (const [step, next] of [
      ["welcome", ["setup-next"]],
      // The plans question wants an answer before Continue; it comes before the calendar.
      ["services", ["services-none", "setup-next"]],
      ["calendar", ["setup-skip"]],
      ["key", ["key-choice-none", "setup-next"]],
      ["audio", ["setup-next"]],
      ["capture", ["setup-next"]],
      ["finish", []],
    ] as const) {
      await expect(page.getByTestId(`setup-step-${step}`)).toBeVisible();
      expect(await cutOff(page), step).toEqual([]);
      // The lead never runs under what follows it.
      const lead = page.getByTestId("setup-lead");
      const fits = await lead.evaluate((el) => el.scrollHeight <= el.clientHeight + 1);
      expect(fits, `${step}: the lead runs under the step`).toBe(true);
      for (const id of next) await page.getByTestId(id).click();
    }
    await reset(request);
  });
}

test("on_a_short_screen_the_last_two_steps_fit_unscrolled", async ({ page, seedBody, request }) => {
  // Fitting unscrolled comes first on a short screen (the PR #20 review); the sound check
  // and how to record are now a screen each, both with a lead (setup plan, 2026-10-20).
  await page.setViewportSize({ width: 1000, height: 600 });
  await seedBody({ setup_done: false });
  await speak(request, "en");
  await gotoApp(page, "/welcome");
  await page.getByTestId("setup-next").click();
  await page.getByTestId("services-none").click();
  await page.getByTestId("setup-next").click();
  await page.getByTestId("setup-skip").click(); // calendar
  await expect(page.getByTestId("setup-step-key")).toBeVisible();
  await page.getByTestId("key-choice-none").click();
  await page.getByTestId("setup-next").click();
  for (const step of ["audio", "capture"]) {
    await expect(page.getByTestId(`setup-step-${step}`)).toBeVisible();
    const content = page.getByTestId("setup-content");
    const scrolls = await content.evaluate((el) => {
      const pane = el.closest(".overflow-y-auto");
      return pane ? pane.scrollHeight > pane.clientHeight + 1 : false;
    });
    expect(scrolls, `${step} scrolls on a 600px screen`).toBe(false);
    if (step === "audio") await page.getByTestId("setup-next").click();
  }
  await reset(request);
});
