/**
 * First-run setup, the parts that are rules rather than screens (epic z8tj1hb01k).
 *
 * Kept free of React and of the API so the mock (Setup 0) and the real flow share
 * exactly one definition of which steps appear and which provider ends up writing
 * the summaries. What the user confirms on the mock is what ships.
 */

export type StepId = "welcome" | "services" | "calendar" | "ai" | "key" | "audio" | "capture" | "finish";

/**
 * What the progress track shows: six stops, named for what they ask (setup plan,
 * 2026-10-20). Connecting the AI and the key are one stop, and so are the sound check and
 * how to record, so the track does not grow and shrink as steps come and go.
 */
export type CrumbId = "welcome" | "plans" | "calendar" | "connect" | "recording" | "finish";

export function crumbOf(step: StepId): CrumbId {
  if (step === "services") return "plans";
  if (step === "ai" || step === "key") return "connect";
  if (step === "audio" || step === "capture") return "recording";
  return step;
}

/**
 * The steps, in order, for this machine.
 *
 * Which AI plans the user pays for comes before the calendar, so their helpers install in
 * the background while the calendar is connected, and nobody waits on them (setup plan,
 * O5). The speech model is not among the steps: fetching it is the installer's job (D65).
 * A build with no Google client cannot connect a calendar, so that step is hidden, not
 * shown broken. There is no CPU/GPU step. Finish shows what was set up, with a Change on
 * each line and the crash-report question (D87), and is where setup reopens after an
 * install (D69).
 */
export function stepsFor(machine: { calendarAvailable: boolean }): StepId[] {
  return [
    "welcome",
    // Which AI plans the user pays for, before any is set up (D75).
    "services",
    ...(machine.calendarAvailable ? (["calendar"] as const) : []),
    "ai",
    // No paid plan connected: a key, or transcripts only (`withoutUnneeded`).
    "key",
    "audio",
    "capture",
    "finish",
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
  /** null leaves `detection.*` as it is: detection was switched off in Settings (D69). */
  capture: CaptureMode | null;
  /** The crash-report answer, where the build can send them (D87). */
  reports: boolean | null;
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
  /** Any provider Settings offers: setup itself only ever picks a `ProviderId`. */
  provider: ProviderId | (string & {});
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

/**
 * What finishing writes, when setup reopens on choices made before (D69). A provider setup
 * cannot offer or check — an API key's, a local model's — stays when the AI steps end on
 * "none": passing the key step over is not asking to switch summaries off. A subscription
 * is not kept that way: whether it still works is known, and `chooseSummarizer` decided.
 */
export function keepSaved(chosen: Summarizer, saved: Summarizer | null): Summarizer {
  if (chosen.provider !== "none" || !saved || saved.provider === "none") return chosen;
  if (SUBSCRIPTIONS.has(saved.provider)) return chosen;
  // Its fallback is kept only if it is not a subscription: that one setup has just decided.
  return { provider: saved.provider, fallback: SUBSCRIPTIONS.has(saved.fallback) ? "" : saved.fallback };
}

const SUBSCRIPTIONS = new Set<string>(Object.values(CLI_PROVIDER));

/** A provider that is one of the subscription CLIs setup connects. */
export function isSubscription(provider: string): boolean {
  return SUBSCRIPTIONS.has(provider);
}

/**
 * What the no-plan step chose: a key, transcripts only, or keeping a key or local model
 * set before (offered only on a reopened setup that has one).
 */
export type KeyChoice = "key" | "none" | "keep";

/** The summaries finishing writes: `keepSaved`, unless the user chose transcripts only. */
export function finalSummarizer(chosen: Summarizer, saved: Summarizer | null, keyChoice: KeyChoice | null): Summarizer {
  if (keyChoice === "none" && chosen.provider === "none") return { provider: "none", fallback: "" };
  return keepSaved(chosen, saved);
}

/** The ticked subscriptions in the order they would write summaries (`CLI_IDS`). */
export function summaryOrder(ticked: Record<CliId, boolean>): CliId[] {
  return CLI_IDS.filter((id) => ticked[id]);
}

/** Pre-tick a card when its CLI already works: there is nothing to ask. */
export function initialTicks(clis: Record<CliId, CliFacts>): Record<CliId, boolean> {
  return {
    claude: cliUsable(clis.claude),
    codex: cliUsable(clis.codex),
    antigravity: cliUsable(clis.antigravity),
  };
}
