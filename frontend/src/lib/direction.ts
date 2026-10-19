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
 * Which way a piece of text runs, from its first strongly directional letter — what
 * `dir="auto"` decides, for an element whose text is inside a `<bdi>` (which
 * `dir="auto"` skips). Hebrew, Arabic and the other right-to-left scripts run right to
 * left; anything else, and text with no letters at all, left to right.
 */
const RTL_LETTER = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/u;
const LTR_LETTER = /[A-Za-z\u00C0-\u024F\u0370-\u03FF\u0400-\u04FF]/u;

export function directionOf(text: string): Direction {
  for (const char of text) {
    if (RTL_LETTER.test(char)) return "rtl";
    if (LTR_LETTER.test(char)) return "ltr";
  }
  return "ltr";
}

