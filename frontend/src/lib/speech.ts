import type { ModelStatus } from "../api";

/**
 * What the meeting-language control offers, as the three states of two config keys.
 *
 * `asr.language_mode` and `asr.default_language` are an implementation: "fixed" plus
 * "en" is what a person means by "my meetings are in English". The control speaks in
 * the second terms and this module translates both ways, so nobody picks a mode.
 *
 * "detect" keeps the Hebrew model and detects once per meeting, which is what every
 * install before first-run setup did. It is offered rather than recommended: the
 * Hebrew fine-tune hears English as Hebrew often enough that an English meeting came
 * out pinned as Hebrew on a stranger's machine (ClickUp z8tj1haczh).
 */
export type MeetingLanguage = "he" | "en" | "detect";

export const MEETING_LANGUAGES: readonly MeetingLanguage[] = ["he", "en", "detect"];

export interface AsrLanguageConfig {
  language_mode?: string;
  default_language?: string;
}

export function meetingLanguageOf(asr: AsrLanguageConfig | undefined): MeetingLanguage {
  if (asr?.language_mode === "fixed") return asr.default_language === "en" ? "en" : "he";
  return "detect";
}

export function meetingLanguageValues(choice: MeetingLanguage): Record<string, string> {
  if (choice === "detect") {
    return { "asr.language_mode": "detect", "asr.default_language": "he" };
  }
  return { "asr.language_mode": "fixed", "asr.default_language": choice };
}

/**
 * What the setup screen shows before anything is chosen.
 *
 * The saved default is "detect" with Hebrew as the fallback — the behaviour every earlier
 * install had, and not one to change under them. A new install is asked instead, with
 * Hebrew preselected: most meetings here are Hebrew, and a fixed language cannot be
 * mis-detected. Once the person has chosen, their choice is what shows.
 */
export function initialMeetingLanguage(
  asr: AsrLanguageConfig | undefined,
  setupDone: boolean,
): MeetingLanguage {
  const saved = meetingLanguageOf(asr);
  return !setupDone && saved === "detect" ? "he" : saved;
}

/**
 * Whether first-run setup still has to be shown.
 *
 * Only an explicit `false` counts. A server that does not send the key at all is one
 * from before the setup screen existed, and trapping someone on a screen their backend
 * cannot finish would be worse than not showing it.
 */
export function setupPending(config: Record<string, unknown> | undefined): boolean {
  const setup = config?.setup as { done?: unknown } | undefined;
  return setup?.done === false;
}

/** 0..1 through the download, or null when there is nothing to measure against yet. */
export function downloadFraction(status: Pick<ModelStatus, "done_bytes" | "total_bytes">): number | null {
  if (!status.total_bytes) return null;
  return Math.min(1, Math.max(0, status.done_bytes / status.total_bytes));
}

/** Whether the drive can take the model, with the backend's one gigabyte to spare. */
export const SPARE_BYTES = 1024 ** 3;

export function roomForModel(status: Pick<ModelStatus, "expected_bytes" | "free_bytes" | "done_bytes">): boolean {
  if (!status.expected_bytes || !status.free_bytes) return true; // unknown: let the backend judge
  return status.free_bytes >= Math.max(0, status.expected_bytes - status.done_bytes) + SPARE_BYTES;
}
