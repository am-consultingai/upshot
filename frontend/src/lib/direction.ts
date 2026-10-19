/**
 * Which way a meeting's text runs. The backend decides, for every language Whisper
 * transcribes (app/asr/languages.py); the list of right-to-left languages is not
 * repeated here.
 */
export type Direction = "ltr" | "rtl";

export function textDirection(direction: string | null | undefined): Direction {
  return direction === "rtl" ? "rtl" : "ltr";
}

/** The notes run in the summary's language; the transcript in the meeting's. */
export function meetingDirections(meeting: { direction?: string | null; summary_direction?: string | null } | undefined): {
  summary: Direction;
  transcript: Direction;
} {
  return {
    summary: textDirection(meeting?.summary_direction ?? meeting?.direction),
    transcript: textDirection(meeting?.direction),
  };
}

/**
 * Which way a piece of text runs, by the letters most of it is written in.
 *
 * Not the first letter, which is what `dir="auto"` goes by: a Hebrew action item that
 * opens with a product name ("PR חדש לתיקון הבאג") is a Hebrew sentence, and laid out
 * left to right it reads backwards. Letters from right-to-left scripts against all
 * other letters; digits, punctuation and marks do not count. A tie goes to the first
 * letter, and text with no letters at all runs left to right.
 */
const RTL_LETTER = /[\p{Script=Hebrew}\p{Script=Arabic}\p{Script=Syriac}\p{Script=Thaana}\p{Script=Nko}]/u;
const LETTER = /\p{L}/u;

export function directionOf(text: string): Direction {
  let rtl = 0;
  let ltr = 0;
  let first: Direction | null = null;
  for (const char of text) {
    if (!LETTER.test(char)) continue;
    const here: Direction = RTL_LETTER.test(char) ? "rtl" : "ltr";
    first ??= here;
    if (here === "rtl") rtl += 1;
    else ltr += 1;
  }
  if (rtl === ltr) return first ?? "ltr";
  return rtl > ltr ? "rtl" : "ltr";
}
