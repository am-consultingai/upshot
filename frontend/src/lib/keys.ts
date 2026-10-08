/** Typing into a field is not a shortcut. */
export function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

/**
 * The key a single-key shortcut means, whatever the keyboard is set to.
 *
 * `event.key` is what the layout types, so with the keyboard on Hebrew the J key
 * arrives as "ח" and every letter shortcut silently did nothing. A Latin character is
 * taken as typed — AZERTY and Dvorak keep their own letters — and anything else falls
 * back to the physical key, which is what Ctrl+J and Ctrl+R already match on.
 */
export function shortcutKey(event: Pick<KeyboardEvent, "key" | "code">): string {
  const typed = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (/^[\x21-\x7e]$/.test(typed)) return typed;
  const letter = /^Key([A-Z])$/.exec(event.code);
  if (letter) return letter[1].toLowerCase();
  const digit = /^Digit([0-9])$/.exec(event.code);
  if (digit) return digit[1];
  const punctuation: Record<string, string> = { Slash: "/", Comma: ",", Period: "." };
  return punctuation[event.code] ?? typed;
}
