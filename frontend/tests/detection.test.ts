import { describe, expect, it } from "vitest";
import type { Prompt } from "../src/api";
import { appName, explainOffer } from "../src/lib/detection";
import { en } from "../src/locales/en";
import { he } from "../src/locales/he";

const t = (key: keyof typeof en) => en[key];

function offer(fields: Partial<Prompt>): Prompt {
  return {
    kind: "detected",
    title: "",
    calendar_id: null,
    event_id: null,
    conference_url: null,
    process: "C:\\Program Files\\Zoom\\bin\\Zoom.exe",
    ...fields,
  };
}

describe("the nudge says what it detected and why", () => {
  it("names the app without its path or .exe", () => {
    expect(appName("C:\\Program Files\\Zoom\\bin\\Zoom.exe")).toBe("Zoom");
    expect(appName(null)).toBe("");
  });

  it("is one line: app, score, evidence in words", () => {
    const line = explainOffer(
      offer({
        score: 7,
        evidence: [
          { code: "mic.known_app", detail: "Zoom.exe" },
          { code: "vad.loopback", detail: "someone else is speaking" },
          { code: "window.title", detail: "Zoom Meeting" },
        ],
      }),
      t,
    );
    expect(line).toBe(
      "Zoom · score 7 · a call app took the microphone, someone else is speaking, window “Zoom Meeting”",
    );
  });

  it("leaves out what it cannot put in words, and says it once", () => {
    const line = explainOffer(
      offer({
        score: 4,
        evidence: [
          { code: "ignored", detail: "x" },
          { code: "something.new", detail: "x" },
          { code: "vad.mic", detail: "you are speaking" },
          { code: "vad.mic", detail: "you are speaking" },
        ],
      }),
      t,
    );
    expect(line).toBe("Zoom · score 4 · you are speaking");
  });

  it("an offer from before the score was sent still names the app", () => {
    expect(explainOffer(offer({}), t)).toBe("Zoom");
  });

  it("a calendar meeting's start is not a detection and has no why", () => {
    expect(explainOffer(offer({ kind: "calendar", score: null }), t)).toBe("");
  });

  it("every evidence phrase is in Hebrew too", () => {
    const keys = Object.keys(en).filter((key) => key.startsWith("detector.evidence."));
    expect(keys.length).toBeGreaterThan(5);
    for (const key of keys) expect(he[key as keyof typeof en]).not.toBe(en[key as keyof typeof en]);
  });
});
