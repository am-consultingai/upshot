/**
 * The choices in the hidden "Transcribe again as…" dialog (R9): Hebrew first, then the
 * classifier's next guesses for this meeting, then every language Whisper knows.
 */
export interface Language {
  code: string;
  name: string;
  native: string;
}

export const HEBREW = "he";

/** Hebrew, then up to three of the classifier's candidates, without repeats. */
export function quickChoices(candidates: readonly string[] | undefined, languages: readonly Language[]): Language[] {
  const known = new Map(languages.map((language) => [language.code, language]));
  const codes = [HEBREW, ...(candidates ?? []).filter((code) => code !== HEBREW)];
  const out: Language[] = [];
  for (const code of codes) {
    const language = known.get(code);
    if (language && !out.some((item) => item.code === code)) out.push(language);
    if (out.length === 4) break;
  }
  return out;
}

/** Every language, by English name, narrowed by what was typed in any of its names. */
export function searchLanguages(languages: readonly Language[], query: string): Language[] {
  const needle = query.trim().toLocaleLowerCase();
  const sorted = [...languages].sort((a, b) => a.name.localeCompare(b.name, "en"));
  if (!needle) return sorted;
  return sorted.filter(
    (language) =>
      language.code.toLocaleLowerCase() === needle ||
      language.name.toLocaleLowerCase().includes(needle) ||
      language.native.toLocaleLowerCase().includes(needle),
  );
}

/** "Español · Spanish", or just the name when they are the same. */
export function label(language: Language): string {
  return language.native === language.name ? language.name : `${language.native} · ${language.name}`;
}
