import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { en } from "../src/locales/en";
import { he } from "../src/locales/he";
import { directionFor } from "../src/i18n";

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

describe("catalogue parity", () => {
  it("he has exactly the key set of en", () => {
    expect(Object.keys(he).sort()).toEqual(Object.keys(en).sort());
  });

  /*
   * Proper nouns stay in Latin script in both catalogues, exactly as they do inside
   * Hebrew prose elsewhere ("הופק על ידי Upshot"). These are the keys where identical
   * strings mean translated, not forgotten: the product name, and the name of a file
   * format, which is not translated into Hebrew any more than "PDF" is.
   */
  const PROPER_NOUNS = new Set(["app.title", "meeting.exportMarkdown"]);

  it("no translation is left as the English string", () => {
    const identical = Object.keys(en).filter(
      (key) =>
        !PROPER_NOUNS.has(key) &&
        he[key as keyof typeof he] === en[key as keyof typeof en],
    );
    expect(identical).toEqual([]);
  });
});

describe("direction", () => {
  it("follows the locale", () => {
    expect(directionFor("he")).toBe("rtl");
    expect(directionFor("en")).toBe("ltr");
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
