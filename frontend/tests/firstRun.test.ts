import { describe, expect, it } from "vitest";
import {
  DEFAULT_CAPTURE,
  chooseSummarizer,
  crumbOf,
  initialTicks,
  finalSummarizer,
  keepSaved,
  resumeAt,
  summaryOrder,
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
  it("asks about AI plans before the calendar and ends on Finish, never on the model or the CPU/GPU", () => {
    // The plans come first so their helpers install while the calendar is connected (O5);
    // Finish shows what was set up and asks about crash reports (setup plan, §3.6).
    expect(stepsFor({ calendarAvailable: true })).toEqual([
      "welcome", "services", "calendar", "ai", "key", "audio", "capture", "finish",
    ]);
  });

  it("shows six stops on the track, named for what they ask", () => {
    const track = [...new Set(stepsFor({ calendarAvailable: true }).map(crumbOf))];
    expect(track).toEqual(["welcome", "plans", "calendar", "connect", "recording", "finish"]);
    expect(crumbOf("key")).toBe("connect");
    expect(crumbOf("audio")).toBe("recording");
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

describe("keepSaved (setup reopened after an install, D69)", () => {
  const none = { provider: "none", fallback: "" } as const;

  it("keeps a key or a local model chosen before when the AI steps end on none", () => {
    expect(keepSaved(none, { provider: "gemini", fallback: "" })).toEqual({ provider: "gemini", fallback: "" });
    expect(keepSaved(none, { provider: "ollama", fallback: "" })).toEqual({ provider: "ollama", fallback: "" });
  });

  it("drops a saved subscription fallback, which setup has just decided on", () => {
    expect(keepSaved(none, { provider: "gemini", fallback: "codex-subscription" })).toEqual({
      provider: "gemini",
      fallback: "",
    });
  });

  it("lets a subscription that works now win over what was saved", () => {
    const now = { provider: "claude-subscription", fallback: "" } as const;
    expect(keepSaved(now, { provider: "gemini", fallback: "" })).toEqual(now);
  });

  it("does not keep a subscription setup found no longer works", () => {
    expect(keepSaved(none, { provider: "codex-subscription", fallback: "" })).toEqual(none);
  });

  it("writes none on a first run, with nothing saved", () => {
    expect(keepSaved(none, null)).toEqual(none);
  });
});

describe("finalSummarizer (the no-plan step's answer)", () => {
  const none = { provider: "none", fallback: "" } as const;
  const key = { provider: "gemini", fallback: "" } as const;

  it("writes none when transcripts only was chosen, even over a saved key", () => {
    expect(finalSummarizer(none, key, "none")).toEqual(none);
  });

  it("keeps a saved key when it was kept, or the step never came", () => {
    expect(finalSummarizer(none, key, "keep")).toEqual(key);
    expect(finalSummarizer(none, key, null)).toEqual(key);
  });

  it("lets a working subscription win whatever was answered", () => {
    const claude = { provider: "claude-subscription", fallback: "" } as const;
    expect(finalSummarizer(claude, key, "none")).toEqual(claude);
  });
});

describe("summaryOrder (the line under the plans question)", () => {
  it("is the ticked plans in the order they write summaries", () => {
    expect(summaryOrder(ticks({ antigravity: true, claude: true }))).toEqual(["claude", "antigravity"]);
    expect(summaryOrder(ticks())).toEqual([]);
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
