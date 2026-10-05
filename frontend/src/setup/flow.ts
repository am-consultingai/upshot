/**
 * First-run setup, the parts that are rules rather than screens (epic z8tj1hb01k).
 *
 * Kept free of React and of the API so the mock (Setup 0) and the real flow share
 * exactly one definition of which steps appear and which provider ends up writing
 * the summaries. What the user confirms on the mock is what ships.
 */

export type StepId = "welcome" | "calendar" | "services" | "ai" | "key" | "reports" | "audio";

/**
 * What the progress track shows: the three AI screens are one stop on it, "AI setup"
 * (product owner, 2026-09-28), so the track does not grow and shrink as the key step
 * comes and goes.
 */
export type CrumbId = "welcome" | "calendar" | "aiSetup" | "reports" | "audio";

export function crumbOf(step: StepId): CrumbId {
  return step === "services" || step === "ai" || step === "key" ? "aiSetup" : step;
}

/**
 * The steps, in order, for this machine.
 *
 * The speech model is not among them: fetching it is the installer's job (D65), and setup
 * never asks for it. A build with no Google client cannot connect a calendar, so that
 * step is hidden, not shown broken. There is no CPU/GPU step: the device is chosen
 * automatically. The last step is the sound check, with how meetings are recorded under
 * it, and its Done finishes setup: there is no summary screen (product owner, 2026-09-28).
 */
export function stepsFor(machine: { calendarAvailable: boolean; reportsAvailable?: boolean }): StepId[] {
  return [
    "welcome",
    ...(machine.calendarAvailable ? (["calendar"] as const) : []),
    // Which AI services the user pays for, before any is set up (D75).
    "services",
    "ai",
    // An API key, only when no subscription was signed in (`withoutUnneeded`).
    "key",
    // Crash reports, asked once (D87), and only in a build that can send them.
    ...(machine.reportsAvailable ? (["reports"] as const) : []),
    "audio",
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

/**
 * The steps as the user walks them now. The key step is offered only when none of the
 * subscriptions ticked is signed in and working — ticking nothing counts as none
 * (product owner, 2026-09-28). The connect step is passed over when nothing is ticked.
 */
export function withoutUnneeded(
  steps: StepId[],
  ticked: Record<CliId, boolean>,
  clis: Record<CliId, CliFacts>,
): StepId[] {
  const anyTicked = CLI_IDS.some((id) => ticked[id]);
  const anyUsable = CLI_IDS.some((id) => ticked[id] && cliUsable(clis[id]));
  return steps.filter((step) => (step === "ai" ? anyTicked : step === "key" ? !anyUsable : true));
}

/** Where to reopen setup: the saved step if it still exists here, else the start. */
export function resumeAt(steps: StepId[], saved: StepId | null | undefined): StepId {
  return saved && steps.includes(saved) ? saved : steps[0];
}

/** The subscription CLIs, in the order setup prefers them when more than one works. */
export const CLI_IDS = ["claude", "codex", "antigravity"] as const;
export type CliId = (typeof CLI_IDS)[number];
export type KeyProviderId = "gemini" | "anthropic" | "openai";
export type ProviderId =
  | "claude-subscription"
  | "codex-subscription"
  | "antigravity-subscription"
  | KeyProviderId
  | "none";

export const CLI_PROVIDER: Record<CliId, ProviderId> = {
  claude: "claude-subscription",
  codex: "codex-subscription",
  // A Google AI plan, through Google's Antigravity CLI (D79).
  antigravity: "antigravity-subscription",
};

/** Every CLI off: the starting point before anything is known or ticked. */
export const NO_CLIS: Record<CliId, boolean> = { claude: false, codex: false, antigravity: false };

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
 * Only a subscription that is ticked and usable counts, in `CLI_IDS` order: Claude, then
 * Codex, then Antigravity, the next one usable as the fallback. With none, a key that
 * passed its check. With nothing, `none`: the meeting stops at its transcript, which is
 * a choice, not an error.
 */
export function chooseSummarizer(
  ticked: Record<CliId, boolean>,
  clis: Record<CliId, CliFacts>,
  checkedKey: KeyProviderId | null,
): Summarizer {
  const usable = CLI_IDS.filter((id) => ticked[id] && cliUsable(clis[id]));
  if (usable.length > 0) {
    return { provider: CLI_PROVIDER[usable[0]], fallback: usable[1] ? CLI_PROVIDER[usable[1]] : "" };
  }
  return { provider: checkedKey ?? "none", fallback: "" };
}

/** Pre-tick a card when its CLI already works: there is nothing to ask. */
export function initialTicks(clis: Record<CliId, CliFacts>): Record<CliId, boolean> {
  return {
    claude: cliUsable(clis.claude),
    codex: cliUsable(clis.codex),
    antigravity: cliUsable(clis.antigravity),
  };
}
