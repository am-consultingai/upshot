/**
 * Recent searches, per browser.
 *
 * A convenience and nothing more: a private window or blocked storage simply has
 * none, and the Search screen reads the same without them. Five, newest first, a
 * repeated search moved to the front rather than listed twice.
 */
const KEY = "ma.search.recent";
const KEEP = 5;

export function recentSearches(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(KEY) ?? "[]") as unknown;
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function rememberSearch(term: string): void {
  const clean = term.trim();
  if (clean.length < 2) return;
  try {
    const next = [clean, ...recentSearches().filter((item) => item.toLowerCase() !== clean.toLowerCase())];
    window.localStorage.setItem(KEY, JSON.stringify(next.slice(0, KEEP)));
  } catch {
    /* storage refused: nothing to remember into */
  }
}

export function forgetSearches(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* nothing stored, nothing to forget */
  }
}
