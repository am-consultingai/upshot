/**
 * First-run setup, the parts that are rules rather than screens (epic z8tj1hb01k).
 *
 * Kept free of React and of the API so the mock (Setup 0) and the real flow share
 * exactly one definition of which steps appear and which provider ends up writing
 * the summaries. What the user confirms on the mock is what ships.
 */

export type StepId = "welcome" | "calendar" | "ai" | "audio" | "capture" | "done";

/**
 * The steps, in order, for this machine.
 *
 * The speech model is not among them: fetching it is the installer's job, and setup
 * never asks for it. A build with no Google client cannot connect a calendar, so that
 * step is hidden, not shown broken. There is no CPU/GPU step: the device is chosen
 * automatically. How meetings are recorded comes last, once the user has seen what
 * Upshot does with a recording.
 */
export function stepsFor(machine: { calendarAvailable: boolean }): StepId[] {
  return [
    "welcome",
    ...(machine.calendarAvailable ? (["calendar"] as const) : []),
    "ai",
    "audio",
    "capture",
    "done",
  ];
}

/**
 * `detection.mode`, as setup offers it. `shadow` is "detect and notify", the default
 * (D64): Upshot notices a call and says so, and records only when asked. `on` records
 * by itself. `off` is not offered here (the product owner took it out, 2026-09-26);
 * Settings still has it.
 */
export type CaptureMode = "shadow" | "on";
export const DEFAULT_CAPTURE: CaptureMode = "shadow";

/** Everything setup saves when it finishes. */
export interface SetupChoices {
  summarizer: Summarizer;
  capture: CaptureMode;
}

/** Where to reopen setup: the saved step if it still exists here, else the start. */
export function resumeAt(steps: StepId[], saved: StepId | null | undefined): StepId {
  return saved && steps.includes(saved) ? saved : steps[0];
}

export type CliId = "claude" | "codex";
export type KeyProviderId = "gemini" | "anthropic" | "openai";
export type ProviderId = "claude-subscription" | "codex-subscription" | KeyProviderId | "none";

export const CLI_PROVIDER: Record<CliId, ProviderId> = {
  claude: "claude-subscription",
  codex: "codex-subscription",
};

/** What setup knows about one subscription CLI on this machine. */
export interface CliFacts {
  installed: boolean;
  /** Three-valued, as `/llm/status` reports it: null is a CLI too old to be asked. */
  signedIn: boolean | null;
  /** "free" is an account that signed in but has no plan that includes the CLI. */
  plan?: "paid" | "free";
}

/**
 * A subscription that would summarize a meeting right now.
 *
 * Signed in means `true`, not "not false": an old CLI that cannot say is not counted
 * until a sign-in in setup proves it. A free account is signed in and still useless.
 */
export function cliUsable(cli: CliFacts): boolean {
  return cli.installed && cli.signedIn === true && cli.plan !== "free";
}

export interface Summarizer {
  provider: ProviderId;
  /** Used when the provider reports its quota spent; "" for none. */
  fallback: ProviderId | "";
}

/**
 * Which provider writes the summaries once setup finishes (Setup 8, settled with the
 * user on 2026-09-26).
 *
 * Only a subscription that is ticked and usable counts. Claude before Codex when both
 * are, with Codex as the fallback. With neither, a key that passed its check. With
 * nothing, `none`: the meeting stops at its transcript, which is a choice, not an error.
 */
export function chooseSummarizer(
  ticked: Record<CliId, boolean>,
  clis: Record<CliId, CliFacts>,
  checkedKey: KeyProviderId | null,
): Summarizer {
  const usable = (["claude", "codex"] as const).filter((id) => ticked[id] && cliUsable(clis[id]));
  if (usable.length > 0) {
    return { provider: CLI_PROVIDER[usable[0]], fallback: usable[1] ? CLI_PROVIDER[usable[1]] : "" };
  }
  return { provider: checkedKey ?? "none", fallback: "" };
}

/** Pre-tick a card when its CLI already works: there is nothing to ask. */
export function initialTicks(clis: Record<CliId, CliFacts>): Record<CliId, boolean> {
  return { claude: cliUsable(clis.claude), codex: cliUsable(clis.codex) };
}
