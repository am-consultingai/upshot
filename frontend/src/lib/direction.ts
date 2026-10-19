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
 * Which way a piece of text runs.
 *
 * Not by its first letter, which is what `dir="auto"` goes by: a Hebrew action item
 * that opens with a product name ("PR חדש לתיקון הבאג") is a Hebrew sentence. Nor by
 * counting letters, where long English names outweigh short Hebrew words
 * ("לעדכן את ה-README ב-GitHub").
 *
 * A text that opens with a right-to-left word is a right-to-left sentence, however
 * many English names follow ("להגדיר Google Analytics"): an English sentence does not
 * open with a Hebrew word. Otherwise, words (runs of letters) from right-to-left
 * scripts against the others; digits, punctuation and symbols do not count, and text
 * with no letters at all runs left to right.
 *
 * Inside a right-to-left block (`lean`) a tie goes to the block, and so do two things
 * only a Hebrew sentence has: a one-letter prefix joined to a word ("ל-GitHub",
 * "ל־staging") and a Hebrew function word (עם, של, לפני…). "Code review לפני merge"
 * there is Hebrew; "Send דנה the notes", which only names someone, is still English.
 * Elsewhere a tie goes to the first word.
 */
const HEBREW_PREFIX = /(^|[^\p{L}])\p{Script=Hebrew}[-\u05BE]\S/u;
const HEBREW_FUNCTION_WORDS = new Set([
  "את", "של", "עם", "על", "אל", "לפני", "אחרי", "עבור", "בין", "מול", "עד", "גם", "כל",
]);
const RTL_LETTER = /[\p{Script=Hebrew}\p{Script=Arabic}\p{Script=Syriac}\p{Script=Thaana}\p{Script=Nko}]/u;
const WORD = /\p{L}+/gu;

export function directionOf(text: string, lean?: Direction): Direction {
  let rtl = 0;
  let ltr = 0;
  let first: Direction | null = null;
  for (const [word] of text.matchAll(WORD)) {
    const here: Direction = RTL_LETTER.test(word) ? "rtl" : "ltr";
    if (here === "rtl" && first === null) return "rtl";
    if (lean === "rtl" && HEBREW_FUNCTION_WORDS.has(word)) return "rtl";
    first ??= here;
    if (here === "rtl") rtl += 1;
    else ltr += 1;
  }
  if (lean === "rtl" && HEBREW_PREFIX.test(text)) return "rtl";
  if (rtl === ltr) return lean ?? first ?? "ltr";
  return rtl > ltr ? "rtl" : "ltr";
}
