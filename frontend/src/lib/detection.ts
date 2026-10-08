import type { Prompt } from "../api";
import type { MessageKey } from "../locales/en";

/** The app's own name for a process, which is all anyone wants to read. */
export function appName(process: string | null | undefined): string {
  if (!process) return "";
  const file = process.split("\\").pop() ?? process;
  return file.replace(/\.exe$/i, "");
}

/** The evidence codes the detector scores (app/detect/evidence.py), in words. */
const EVIDENCE: Record<string, MessageKey> = {
  "mic.known_app": "detector.evidence.mic.known_app",
  "mic.unknown_app": "detector.evidence.mic.unknown_app",
  "vad.loopback": "detector.evidence.vad.loopback",
  "vad.mic": "detector.evidence.vad.mic",
  "window.title": "detector.evidence.window.title",
  "session.render": "detector.evidence.session.render",
  camera: "detector.evidence.camera",
  calendar: "detector.evidence.calendar",
};

/**
 * Why the detector thinks a call is on, as one line: the app, the score and the evidence,
 * "Zoom · score 7 · someone else is speaking, you are speaking". Empty for an offer that
 * is not a detection (a calendar meeting's start is the clock's, not evidence).
 *
 * The evidence is put in words from its code rather than shown as the detector's own
 * detail, which is English and written for the log; only a window title, which is the
 * user's own text, is quoted. Codes this does not know are left out, never shown raw.
 */
export function explainOffer(prompt: Prompt, t: (key: MessageKey) => string): string {
  if (prompt.kind !== "detected") return "";
  const evidence = [
    ...new Set(
      (prompt.evidence ?? []).flatMap(({ code, detail }) => {
        const key = EVIDENCE[code];
        return key ? [t(key).replace("{detail}", detail)] : [];
      }),
    ),
  ];
  return [
    appName(prompt.process),
    prompt.score != null ? t("detector.score").replace("{score}", String(prompt.score)) : "",
    evidence.join(", "),
  ]
    .filter(Boolean)
    .join(" · ");
}
