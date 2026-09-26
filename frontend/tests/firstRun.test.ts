import { describe, expect, it } from "vitest";
import { DEFAULT_CAPTURE, chooseSummarizer, initialTicks, resumeAt, stepsFor, type CliFacts } from "../src/setup/flow";

const absent: CliFacts = { installed: false, signedIn: null };
const ready: CliFacts = { installed: true, signedIn: true, plan: "paid" };
const signedOut: CliFacts = { installed: true, signedIn: false };
const unknown: CliFacts = { installed: true, signedIn: null };
const free: CliFacts = { installed: true, signedIn: true, plan: "free" };

describe("stepsFor", () => {
  it("asks how to record last, and never about the model or the CPU/GPU", () => {
    expect(stepsFor({ calendarAvailable: true })).toEqual(["welcome", "calendar", "ai", "audio", "capture", "done"]);
  });

  it("hides the calendar step in a build that cannot connect one", () => {
    expect(stepsFor({ calendarAvailable: false })).not.toContain("calendar");
  });

  it("defaults to detect and notify", () => {
    expect(DEFAULT_CAPTURE).toBe("shadow");
  });
});

describe("resumeAt", () => {
  const steps = stepsFor({ calendarAvailable: true });
  it("reopens on the saved step", () => expect(resumeAt(steps, "ai")).toBe("ai"));
  it("starts over when there is none, or it no longer exists here", () => {
    expect(resumeAt(steps, null)).toBe("welcome");
    // A step saved by an older build, such as the model step that was removed.
    expect(resumeAt(steps, "model" as never)).toBe("welcome");
  });
});

describe("chooseSummarizer (Setup 8)", () => {
  const both = { claude: true, codex: true };

  it("prefers Claude when both are usable, with Codex as the fallback", () => {
    expect(chooseSummarizer(both, { claude: ready, codex: ready }, null)).toEqual({
      provider: "claude-subscription",
      fallback: "codex-subscription",
    });
  });

  it("takes whichever one is installed and signed in", () => {
    expect(chooseSummarizer(both, { claude: signedOut, codex: ready }, null)).toEqual({
      provider: "codex-subscription",
      fallback: "",
    });
    expect(chooseSummarizer(both, { claude: ready, codex: absent }, null)).toEqual({
      provider: "claude-subscription",
      fallback: "",
    });
  });

  it("does not count an unticked card, an unknown sign-in or a free account", () => {
    expect(chooseSummarizer({ claude: false, codex: true }, { claude: ready, codex: absent }, null).provider).toBe(
      "none",
    );
    expect(chooseSummarizer(both, { claude: unknown, codex: free }, null).provider).toBe("none");
  });

  it("falls to a checked key, then to none", () => {
    const nothing = { claude: false, codex: false };
    expect(chooseSummarizer(nothing, { claude: absent, codex: absent }, "gemini")).toEqual({
      provider: "gemini",
      fallback: "",
    });
    expect(chooseSummarizer(nothing, { claude: absent, codex: absent }, null)).toEqual({
      provider: "none",
      fallback: "",
    });
  });

  it("a usable subscription wins over a key", () => {
    expect(chooseSummarizer(both, { claude: ready, codex: absent }, "gemini").provider).toBe("claude-subscription");
  });
});

describe("initialTicks", () => {
  it("pre-ticks only what already works", () => {
    expect(initialTicks({ claude: ready, codex: signedOut })).toEqual({ claude: true, codex: false });
    expect(initialTicks({ claude: free, codex: unknown })).toEqual({ claude: false, codex: false });
  });
});
