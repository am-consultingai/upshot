import { createContext, useContext } from "react";
import { en, type MessageKey } from "./locales/en";
import { he } from "./locales/he";
import { de } from "./locales/de";
import { es } from "./locales/es";
import { fr } from "./locales/fr";
import type { Theme } from "./theme";

export type Locale = "en" | "he" | "de" | "es" | "fr";

export const catalogues: Record<Locale, Record<MessageKey, string>> = { en, he, de, es, fr };

/**
 * The interface languages, in the order every picker lists them, each named in its own
 * language: someone who cannot read the current one must still find theirs.
 */
export const LANGUAGES: readonly { code: Locale; name: string }[] = [
  { code: "en", name: "English" },
  { code: "he", name: "עברית" },
  { code: "de", name: "Deutsch" },
  { code: "es", name: "Español" },
  { code: "fr", name: "Français" },
];

export function isLocale(value: unknown): value is Locale {
  return LANGUAGES.some((language) => language.code === value);
}

export function languageName(locale: Locale): string {
  return LANGUAGES.find((language) => language.code === locale)?.name ?? locale;
}

export const RTL_LOCALES: ReadonlySet<Locale> = new Set<Locale>(["he"]);

export function directionFor(locale: Locale): "rtl" | "ltr" {
  return RTL_LOCALES.has(locale) ? "rtl" : "ltr";
}

export interface I18n {
  locale: Locale;
  t: (key: MessageKey) => string;
  setLocale: (locale: Locale) => void;
  /** Appearance lives here too: one context for what the shell looks like. */
  theme: Theme;
  setTheme: (theme: Theme) => void;
  /** Whether the explanatory tooltips are shown; Settings can turn them off. */
  tooltips: boolean;
  setTooltips: (on: boolean) => void;
}

export const I18nContext = createContext<I18n>({
  locale: "en",
  t: (key) => catalogues.en[key],
  setLocale: () => undefined,
  theme: "light",
  setTheme: () => undefined,
  tooltips: true,
  setTooltips: () => undefined,
});

export function useI18n(): I18n {
  return useContext(I18nContext);
}

/** Applies the locale to the document — no reload, ever. */
export function applyLocale(locale: Locale): void {
  document.documentElement.lang = locale;
  document.documentElement.dir = directionFor(locale);
}

export type { MessageKey };
