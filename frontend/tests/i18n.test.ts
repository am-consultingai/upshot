import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { en } from "../src/locales/en";
import { catalogues, directionFor, LANGUAGES, type Locale } from "../src/i18n";

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

describe("catalogue parity", () => {
  const others = LANGUAGES.map(({ code }) => code).filter((code) => code !== "en");

  it.each(others)("%s has exactly the key set of en", (locale) => {
    expect(Object.keys(catalogues[locale]).sort()).toEqual(Object.keys(en).sort());
  });

  // Shown only for a count of one, which Hebrew writes as a word ("משימה אחת").
  const NUMBER_IN_WORDS: Partial<Record<Locale, readonly string[]>> = { he: ["sidebar.item"] };

  it.each(others)("%s keeps every {placeholder} of en", (locale) => {
    const missing = Object.entries(en).flatMap(([key, english]) =>
      (NUMBER_IN_WORDS[locale]?.includes(key) ? [] : (english.match(/\{\w+\}/g) ?? []))
        .filter((slot) => !catalogues[locale][key as keyof typeof en].includes(slot))
        .map((slot) => `${key}: ${slot}`),
    );
    expect(missing).toEqual([]);
  });

  /*
   * Proper nouns stay in Latin script in both catalogues, exactly as they do inside
   * Hebrew prose elsewhere ("הופק על ידי Upshot"). These are the keys where identical
   * strings mean translated, not forgotten: the product name, and the name of a file
   * format, which is not translated into Hebrew any more than "PDF" is.
   */
  const PROPER_NOUNS = new Set([
    "app.title",
    "transcriptions.client.api",
    "transcriptions.client.mcp",
    "settings.transcriptionDesktop",
    "settings.transcriptionCode",
    "settings.transcriptionTabWindows",
    "settings.transcriptionTabWsl",
    "meeting.exportMarkdown",
    "assistant.provider.anthropic",
    "assistant.provider.openai",
    "assistant.provider.gemini",
    "firstRun.ai.claude.name",
    "firstRun.ai.codex.name",
    "firstRun.ai.antigravity.name",
    "firstRun.services.claude.name",
    "firstRun.services.codex.name",
    "firstRun.services.gemini.name",
    "firstRun.done.sum.claude-subscription",
    "firstRun.done.sum.codex-subscription",
    // A pattern with no words of its own ("{greeting}, {name}"), the same in both.
    "greeting.named",
  ]);

  /*
   * Words German, Spanish and French share with English, and the abbreviations and
   * product names they keep in Latin script as English does: identical, yet translated.
   */
  const SAME_AS_ENGLISH: Partial<Record<Locale, readonly string[]>> = {
    de: [
      "meeting.pause", "meetingInfo.hours", "meetingInfo.minutes", "calendar.google",
      "invite.links", "invite.optional", "feedback.title", "feedback.kind.problem",
      "updates.version", "updates.status", "common.minutes", "common.hours", "rail.in",
      "firstRun.ai.code", "terms.ok",
    ],
    es: [
      "meetingInfo.hours", "meetingInfo.minutes", "calendar.google", "feedback.kind.idea",
      "common.minutes", "common.hours",
    ],
    fr: [
      "palette.do", "meeting.pause", "meetingInfo.description", "meetingInfo.hours",
      "meetingInfo.minutes", "settings.microphone", "calendar.google", "updates.version",
      "updates.versionValue", "common.minutes", "common.hours", "assistant.title",
      "assistant.open", "assistant.source", "meeting.sections", "firstRun.ai.code",
      "firstRun.audio.mic", "terms.ok", "nav.transcriptions", "transcriptions.title",
    ],
  };

  it.each(others)("%s leaves no translation as the English string", (locale) => {
    const allowed = new Set([...PROPER_NOUNS, ...(SAME_AS_ENGLISH[locale] ?? [])]);
    const identical = Object.keys(en).filter(
      (key) =>
        !allowed.has(key) &&
        catalogues[locale][key as keyof typeof en] === en[key as keyof typeof en],
    );
    expect(identical).toEqual([]);
  });
});

describe("direction", () => {
  it("follows the locale", () => {
    expect(directionFor("he")).toBe("rtl");
    expect(directionFor("en")).toBe("ltr");
    for (const locale of ["de", "es", "fr"] as const) expect(directionFor(locale)).toBe("ltr");
  });
});

describe("no hardcoded user-visible strings", () => {
  // Only .tsx holds JSX; a .ts file's `Promise<T>` is not a text node.
  const files = walk("src").filter(
    (file) => file.endsWith(".tsx") && !file.includes("locales"),
  );

  it("every JSX text node comes from the catalogue", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const [index, line] of source.split("\n").entries()) {
        // text sitting directly between JSX tags, e.g. >Save<
        const match = line.match(/>\s*([A-Za-z֐-׿][^<>{}]{2,})\s*</);
        if (match) offenders.push(`${file}:${index + 1}: ${match[1].trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("logical CSS properties only", () => {
  const files = walk("src");

  it("bans physical Tailwind direction utilities", () => {
    const banned = /\b(pl-|pr-|ml-|mr-|text-left|text-right|border-l-|border-r-)\S*/g;
    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const [index, line] of source.split("\n").entries()) {
        const hits = line.match(banned);
        if (hits) offenders.push(`${file}:${index + 1}: ${hits.join(", ")}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
