import type { MessageKey } from "../i18n";

/**
 * The home screen's greeting: by the time of day, and by the user's first name when their
 * Google profile gave one (connecting Google Calendar asks for it). No name, no comma.
 */
export function greetingKey(hour: number): MessageKey {
  if (hour >= 5 && hour < 12) return "greeting.morning";
  if (hour >= 12 && hour < 18) return "greeting.afternoon";
  return "greeting.evening";
}

export function greeting(t: (key: MessageKey) => string, hour: number, name?: string | null): string {
  const hello = t(greetingKey(hour));
  const first = (name ?? "").trim();
  return first ? t("greeting.named").replace("{greeting}", hello).replace("{name}", first) : hello;
}
