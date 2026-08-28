import { createContext, useContext } from "react";
import { en, type MessageKey } from "./locales/en";
import { he } from "./locales/he";

export type Locale = "en" | "he";

export const catalogues: Record<Locale, Record<MessageKey, string>> = { en, he };

export const RTL_LOCALES: ReadonlySet<Locale> = new Set<Locale>(["he"]);

export function directionFor(locale: Locale): "rtl" | "ltr" {
  return RTL_LOCALES.has(locale) ? "rtl" : "ltr";
}

export interface I18n {
  locale: Locale;
  t: (key: MessageKey) => string;
  setLocale: (locale: Locale) => void;
}

export const I18nContext = createContext<I18n>({
  locale: "en",
  t: (key) => catalogues.en[key],
  setLocale: () => undefined,
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
