import { describe, expect, it } from "vitest";
import { label, quickChoices, searchLanguages, type Language } from "../src/lib/transcribeAgain";

const LANGUAGES: Language[] = [
  { code: "ar", name: "Arabic", native: "العربية" },
  { code: "en", name: "English", native: "English" },
  { code: "es", name: "Spanish", native: "Español" },
  { code: "he", name: "Hebrew", native: "עברית" },
  { code: "ru", name: "Russian", native: "Русский" },
  { code: "pt", name: "Portuguese", native: "Português" },
];

describe("transcribe again: the choices", () => {
  it("offers Hebrew, then the classifier's next three", () => {
    expect(quickChoices(["es", "pt", "en"], LANGUAGES).map((l) => l.code)).toEqual(["he", "es", "pt", "en"]);
  });

  it("does not repeat Hebrew when the classifier said it", () => {
    expect(quickChoices(["he", "en", "ar"], LANGUAGES).map((l) => l.code)).toEqual(["he", "en", "ar"]);
  });

  it("is just Hebrew with nothing stored", () => {
    expect(quickChoices(undefined, LANGUAGES).map((l) => l.code)).toEqual(["he"]);
  });

  it("Other… lists every language, by name", () => {
    expect(searchLanguages(LANGUAGES, "").map((l) => l.code)).toEqual(["ar", "en", "he", "pt", "ru", "es"]);
  });

  it("finds a language by its English name, its own name or its code", () => {
    expect(searchLanguages(LANGUAGES, "span").map((l) => l.code)).toEqual(["es"]);
    expect(searchLanguages(LANGUAGES, "Русск").map((l) => l.code)).toEqual(["ru"]);
    expect(searchLanguages(LANGUAGES, "ar").map((l) => l.code)).toEqual(["ar"]);
  });

  it("shows the native name beside the English one", () => {
    expect(label(LANGUAGES[2])).toBe("Español · Spanish");
    expect(label(LANGUAGES[1])).toBe("English");
  });
});
