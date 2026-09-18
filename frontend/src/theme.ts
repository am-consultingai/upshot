export type Theme = "light" | "dark" | "system";

export const THEMES: readonly Theme[] = ["light", "dark", "system"];

export function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/**
 * Applies the theme to the document.
 *
 * "system" removes the attribute rather than resolving the preference here, so
 * the `@media (prefers-color-scheme: dark)` rule in tokens.css decides — and keeps
 * deciding when the machine switches at sunset, with no listener to maintain.
 * "light" and "dark" set the attribute explicitly, and the media rule is guarded
 * with `:not([data-theme="light"])` so a deliberate choice always wins.
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}
