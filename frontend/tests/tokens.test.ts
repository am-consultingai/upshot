import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * Two rules that a redesign quietly breaks and no visual check reliably catches.
 *
 * Both are cheap to enforce and expensive to discover later: a stray
 * `bg-neutral-200` looks perfectly fine until the theme flips and it stays put,
 * and a stray `ml-4` looks perfectly fine until the interface is read in Hebrew.
 */

const SRC = join(import.meta.dirname, "..", "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sources(path);
    return path.endsWith(".tsx") || path.endsWith(".ts") ? [path] : [];
  });
}

const files = sources(SRC).map((path) => ({
  path: path.slice(SRC.length + 1),
  text: readFileSync(path, "utf8"),
}));

describe("design tokens", () => {
  /**
   * Colour belongs to the token layer. A component naming a palette colour
   * directly is invisible to the theme, which is exactly how dark mode came out
   * as near-white text on a still-light background.
   */
  it("no component names a palette colour directly", () => {
    const palette =
      /(?<![\w-])(bg|text|border|ring|divide|placeholder|from|to|via)-(white|black|neutral|gray|slate|zinc|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)(-\d{2,3})?(?![\w-])/g;

    const offenders = files.flatMap(({ path, text }) =>
      [...text.matchAll(palette)].map((match) => `${path}: ${match[0]}`),
    );
    expect(offenders).toEqual([]);
  });

  /**
   * The interface ships in Hebrew. A physical property is correct in English and
   * wrong in Hebrew, and nothing in an English-language review will reveal it.
   */
  it("no component uses a physical direction utility", () => {
    const physical =
      /(?<![\w-])(ml|mr|pl|pr|left|right|border-l|border-r|rounded-l|rounded-r)-(\d+(\.\d+)?|px|auto|full)(?![\w-])|(?<![\w-])text-(left|right)(?![\w-])/g;

    const offenders = files.flatMap(({ path, text }) =>
      [...text.matchAll(physical)].map((match) => `${path}: ${match[0]}`),
    );
    expect(offenders).toEqual([]);
  });
});
