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
