import { describe, expect, it } from "vitest";
import {
  DEFAULT_CAPTURE,
  chooseSummarizer,
  initialTicks,
  resumeAt,
  stepsFor,
  withoutUnneeded,
  type CliFacts,
} from "../src/setup/flow";

const absent: CliFacts = { installed: false, signedIn: null };
const ready: CliFacts = { installed: true, signedIn: true, plan: "paid" };
const signedOut: CliFacts = { installed: true, signedIn: false };
const unknown: CliFacts = { installed: true, signedIn: null };
const free: CliFacts = { installed: true, signedIn: true, plan: "free" };

/** The three CLIs' facts, absent unless given. */
const clis = (over: Partial<Record<"claude" | "codex" | "antigravity", CliFacts>> = {}) => ({
  claude: absent,
  codex: absent,
  antigravity: absent,
  ...over,
});
/** Which cards are ticked, none unless given. */
const ticks = (over: Partial<Record<"claude" | "codex" | "antigravity", boolean>> = {}) => ({
  claude: false,
  codex: false,
  antigravity: false,
  ...over,
});

describe("stepsFor", () => {
  it("asks how to record last, and never about the model or the CPU/GPU", () => {
    expect(stepsFor({ calendarAvailable: true })).toEqual([
      "welcome",
      "calendar",
      "services",
      "ai",
      "key",
      "audio",
      "capture",
      "done",
    ]);
  });

  it("hides the calendar step in a build that cannot connect one", () => {
    expect(stepsFor({ calendarAvailable: false })).not.toContain("calendar");
  });

  it("defaults to detect and notify", () => {
    expect(DEFAULT_CAPTURE).toBe("shadow");
  });
});

describe("withoutUnneeded (the API key only when no subscription is signed in)", () => {
  const steps = stepsFor({ calendarAvailable: true });

  it("nothing ticked: no connect step, and the key is offered", () => {
    const walked = withoutUnneeded(steps, ticks(), clis());
    expect(walked).not.toContain("ai");
    expect(walked).toContain("key");
  });

  it("ticked but not signed in: connect, then the key", () => {
    const walked = withoutUnneeded(steps, ticks({ antigravity: true }), clis({ antigravity: signedOut }));
    expect(walked.slice(walked.indexOf("ai"), walked.indexOf("ai") + 2)).toEqual(["ai", "key"]);
  });

  it("one ticked subscription signed in: no key step", () => {
    const walked = withoutUnneeded(
      steps,
      ticks({ claude: true, antigravity: true }),
      clis({ claude: signedOut, antigravity: ready }),
    );
    expect(walked).toContain("ai");
    expect(walked).not.toContain("key");
  });

  it("a signed-in CLI that was not ticked does not count, nor does a free account", () => {
    expect(withoutUnneeded(steps, ticks(), clis({ codex: ready }))).toContain("key");
    expect(withoutUnneeded(steps, ticks({ claude: true }), clis({ claude: free }))).toContain("key");
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
  const all = ticks({ claude: true, codex: true, antigravity: true });

  it("prefers Claude when both are usable, with Codex as the fallback", () => {
    expect(chooseSummarizer(all, clis({ claude: ready, codex: ready }), null)).toEqual({
      provider: "claude-subscription",
      fallback: "codex-subscription",
    });
  });

  it("takes whichever one is installed and signed in", () => {
    expect(chooseSummarizer(all, clis({ claude: signedOut, codex: ready }), null)).toEqual({
      provider: "codex-subscription",
      fallback: "",
    });
    expect(chooseSummarizer(all, clis({ claude: ready }), null)).toEqual({
      provider: "claude-subscription",
      fallback: "",
    });
  });

  it("uses a Google AI plan through Antigravity, after the other two", () => {
    expect(chooseSummarizer(all, clis({ antigravity: ready }), null)).toEqual({
      provider: "antigravity-subscription",
      fallback: "",
    });
    expect(chooseSummarizer(all, clis({ codex: ready, antigravity: ready }), null)).toEqual({
      provider: "codex-subscription",
      fallback: "antigravity-subscription",
    });
  });

  it("does not count an unticked card, an unknown sign-in or a free account", () => {
    expect(chooseSummarizer(ticks({ codex: true }), clis({ claude: ready }), null).provider).toBe("none");
    expect(chooseSummarizer(all, clis({ claude: unknown, codex: free }), null).provider).toBe("none");
  });

  it("falls to a checked key, then to none", () => {
    expect(chooseSummarizer(ticks(), clis(), "gemini")).toEqual({ provider: "gemini", fallback: "" });
    expect(chooseSummarizer(ticks(), clis(), null)).toEqual({ provider: "none", fallback: "" });
  });

  it("a usable subscription wins over a key", () => {
    expect(chooseSummarizer(all, clis({ claude: ready }), "gemini").provider).toBe("claude-subscription");
  });
});

describe("initialTicks", () => {
  it("pre-ticks only what already works", () => {
    expect(initialTicks(clis({ claude: ready, codex: signedOut, antigravity: ready }))).toEqual({
      claude: true,
      codex: false,
      antigravity: true,
    });
    expect(initialTicks(clis({ claude: free, codex: unknown }))).toEqual(ticks());
  });
});
