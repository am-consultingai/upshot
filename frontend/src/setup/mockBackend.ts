import type { CliSnapshot, SetupBackend, SetupSnapshot } from "./backend";
import type { CliId, KeyProviderId, SetupChoices, StepId } from "./flow";
import { chime } from "./chime";

/**
 * A scripted stand-in for the setup backend (Setup 0, z8tj1hb03a).
 *
 * Nothing here reaches the network, a CLI or the keyring: every answer is a timer.
 * It exists so the whole flow — every screen, every state, the real copy — can be
 * clicked through and confirmed before any of it is wired up. The dev-only panel
 * picks a scenario (what is on the machine at the start) and the outcomes (what
 * happens when the user acts).
 */

export type ScenarioId =
  | "fresh"
  | "claude-ready"
  | "both-ready"
  | "installed-signed-out"
  | "old-cli"
  | "claude-free"
  | "no-google-client";

/** Dev tooling, English only: these labels never reach a user. */
export const SCENARIOS: { id: ScenarioId; label: string }[] = [
  { id: "fresh", label: "Nothing installed, no calendar" },
  { id: "claude-ready", label: "Claude installed and signed in, Codex absent" },
  { id: "both-ready", label: "Claude and Codex both installed and signed in" },
  { id: "installed-signed-out", label: "Both installed, neither signed in" },
  { id: "old-cli", label: "Old Claude CLI: sign-in state unknown" },
  { id: "claude-free", label: "Claude signed in on a free account" },
  { id: "no-google-client", label: "Build without a Google client" },
];

/** The panel's own words. Dev tooling, English only, never shipped. */
export const PANEL = {
  title: "Setup mock",
  scenario: "Scenario: what is on the machine",
  outcome: {
    calendar: "Calendar ends",
    install: "Install ends",
    signin: "Sign-in ends",
    key: "Key check ends",
    speakers: "Sound test ends",
  },
  hebrew: "עברית",
  english: "English",
  dark: "Dark",
  light: "Light",
  reopen: "Close & reopen app",
  startOver: "Start over",
  show: "Setup mock",
  hide: "Hide",
  finished: "Last run saved:",
} as const;

export interface Outcomes {
  calendar: "connected" | "partial" | "cancelled" | "timeout";
  install: "ok" | "fail";
  signin: "ok" | "fail";
  key: "valid" | "invalid";
  speakers: "heard" | "silent";
}

export const OUTCOME_CHOICES: { [K in keyof Outcomes]: Outcomes[K][] } = {
  calendar: ["connected", "partial", "cancelled", "timeout"],
  install: ["ok", "fail"],
  signin: ["ok", "fail"],
  key: ["valid", "invalid"],
  speakers: ["heard", "silent"],
};

const ACCOUNT = "dana.levi@example.com";
const STEP_KEY = "upshot.setupMock.step";

const INSTALL: Record<CliId, string> = {
  claude: "irm https://claude.ai/install.ps1 | iex",
  codex: "irm https://chatgpt.com/codex/install.ps1 | iex",
  antigravity: "irm https://antigravity.google/cli/install.ps1 | iex",
};

const SIGNIN_URL: Record<CliId, string> = {
  claude: "https://claude.ai/oauth/authorize?code=true&client_id=…",
  codex: "https://auth.openai.com/oauth/authorize?client_id=…",
  antigravity: "https://accounts.google.com/o/oauth2/auth?client_id=…",
};

function cli(id: CliId, over: Partial<CliSnapshot> = {}): CliSnapshot {
  return {
    installed: false,
    signedIn: null,
    phase: "idle",
    installCommand: INSTALL[id],
    signinNeedsCode: id !== "codex",
    ...over,
  };
}

const ready = (plan: "paid" | "free" = "paid") =>
  ({ installed: true, signedIn: true, plan, account: ACCOUNT }) as const;

export function scenarioSnapshot(id: ScenarioId): SetupSnapshot {
  const base: SetupSnapshot = {
    calendar: { available: true, phase: "idle", accounts: [] },
    cli: { claude: cli("claude"), codex: cli("codex"), antigravity: cli("antigravity") },
    cliKnown: true,
    key: { provider: "gemini", phase: "idle" },
    speakers: { phase: "idle", level: 0 },
    savedStep: null,
    reports: { available: true, consent: "unset" },
  };
  switch (id) {
    case "fresh":
      return base;
    case "claude-ready":
      return { ...base, cli: { ...base.cli, claude: cli("claude", ready()) } };
    case "both-ready":
      return { ...base, cli: { ...base.cli, claude: cli("claude", ready()), codex: cli("codex", ready()) } };
    case "installed-signed-out":
      return {
        ...base,
        cli: {
          claude: cli("claude", { installed: true, signedIn: false }),
          codex: cli("codex", { installed: true, signedIn: false }),
          antigravity: cli("antigravity", { installed: true, signedIn: false }),
        },
      };
    case "old-cli":
      return { ...base, cli: { ...base.cli, claude: cli("claude", { installed: true, signedIn: null }) } };
    case "claude-free":
      return { ...base, cli: { ...base.cli, claude: cli("claude", ready("free")) } };
    case "no-google-client":
      return { ...base, calendar: { available: false, phase: "idle", accounts: [] } };
  }
}

export class MockSetupBackend implements SetupBackend {
  private state: SetupSnapshot;
  private listeners = new Set<() => void>();
  private timers = new Set<ReturnType<typeof setTimeout>>();
  scenario: ScenarioId;
  outcomes: Outcomes = { calendar: "connected", install: "ok", signin: "ok", key: "valid", speakers: "heard" };
  /** Multiplies every delay; the browser specs set it near zero. */
  pace = 1;
  finished: SetupChoices | null = null;

  constructor(scenario: ScenarioId = "fresh") {
    this.scenario = scenario;
    this.state = this.load(scenario);
  }

  snapshot = (): SetupSnapshot => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Start over from a scenario, forgetting any saved step. */
  reset(scenario: ScenarioId): void {
    this.clearTimers();
    this.scenario = scenario;
    this.finished = null;
    try {
      localStorage.removeItem(STEP_KEY);
    } catch {
      /* storage blocked: resume simply starts at the beginning */
    }
    this.set(scenarioSnapshot(scenario));
  }

  /** What the app would see after being closed and reopened mid-setup. */
  reopen(): void {
    this.clearTimers();
    const kept = this.state;
    this.set({
      ...this.load(this.scenario),
      key: kept.key.phase === "valid" ? kept.key : { ...kept.key, phase: "idle" },
      speakers: { phase: "idle", level: 0 },
      calendar: { ...kept.calendar, phase: kept.calendar.phase === "connected" ? "connected" : "idle" },
      cli: {
        claude: { ...kept.cli.claude, phase: "idle", signinUrl: undefined },
        codex: { ...kept.cli.codex, phase: "idle", signinUrl: undefined },
        antigravity: { ...kept.cli.antigravity, phase: "idle", signinUrl: undefined },
      },
    });
  }

  connectCalendar(): void {
    this.set({ ...this.state, calendar: { ...this.state.calendar, phase: "waiting" } });
    this.later(2500, () => {
      const outcome = this.outcomes.calendar;
      const had = this.state.calendar.accounts;
      // A second sign-in adds a second made-up account, as "Add another calendar" does.
      const address = had.length === 0 ? ACCOUNT : `account${had.length + 1}@example.com`;
      const accounts =
        outcome === "connected" ? [...had, { id: `ga_mock000${had.length + 1}`, address }] : had;
      // A failed second sign-in says so, beside the accounts that stay connected.
      this.set({ ...this.state, calendar: { ...this.state.calendar, phase: outcome, accounts } });
    });
  }

  cancelCalendar(): void {
    this.clearTimers();
    // Cancelling "Add another calendar" keeps the accounts already connected.
    const { accounts } = this.state.calendar;
    const phase = accounts.length ? "connected" : "cancelled";
    this.set({ ...this.state, calendar: { ...this.state.calendar, phase } });
  }

  install(id: CliId): void {
    this.setCli(id, { phase: "installing" });
    this.later(6000, () => {
      if (this.outcomes.install === "fail") this.setCli(id, { phase: "install-failed" });
      // Installed in the background; the user presses Sign in next (D75).
      else this.setCli(id, { phase: "idle", installed: true, signedIn: false });
    });
  }

  signIn(id: CliId, afterInstall = false): void {
    this.setCli(id, {
      ...(afterInstall ? { installed: true, signedIn: false } : {}),
      phase: "signing-in",
      signinUrl: SIGNIN_URL[id],
    });
    // Codex notices the browser finishing by itself; Claude and Antigravity wait for the
    // pasted code.
    if (id === "codex") this.later(6000, () => this.completeSignIn(id));
  }

  submitCode(id: CliId, code: string): void {
    if (!code.trim()) return;
    this.later(800, () => this.completeSignIn(id));
  }

  cancel(id: CliId): void {
    this.clearTimers();
    this.setCli(id, { phase: "idle", signinUrl: undefined });
  }

  checkKey(provider: KeyProviderId, key: string): void {
    if (!key.trim()) return;
    this.set({ ...this.state, key: { provider, phase: "checking" } });
    this.later(1500, () => this.set({ ...this.state, key: { provider, phase: this.outcomes.key } }));
  }

  chooseKeyProvider(provider: KeyProviderId): void {
    this.set({ ...this.state, key: { provider, phase: "idle" } });
  }

  /**
   * The mock really plays the sound — a short three-note chime — so the screen can be
   * judged with the sound it will make. The level is scripted to follow the notes; the
   * real backend reads it from the loopback of the chosen output.
   */
  testSpeakers(): void {
    this.set({ ...this.state, speakers: { phase: "playing", level: 0 } });
    const heard = this.outcomes.speakers === "heard";
    if (heard) chime();
    const started = Date.now();
    const tick = () => {
      if (this.state.speakers.phase !== "playing") return;
      const t = (Date.now() - started) / 1000;
      if (t >= 1.8) {
        this.set({ ...this.state, speakers: { phase: heard ? "heard" : "silent", level: 0 } });
        return;
      }
      // Three notes, each an attack and a decay.
      const note = t % 0.45;
      const level = heard && t < 1.35 ? Math.max(0.08, 0.85 * Math.exp(-note * 5)) : 0;
      this.set({ ...this.state, speakers: { phase: "playing", level } });
      this.later(50, tick);
    };
    this.later(50, tick);
  }

  /** Nothing to keep: the page's own language state is the whole of it in the mock. */
  setLanguage(): void {}

  setCrashReports(on: boolean): void {
    this.set({ ...this.state, reports: { ...this.state.reports, consent: on ? "on" : "off" } });
  }

  saveStep(step: StepId): void {
    try {
      localStorage.setItem(STEP_KEY, step);
    } catch {
      /* see reset() */
    }
    if (this.state.savedStep !== step) this.set({ ...this.state, savedStep: step });
  }

  async finish(choices: SetupChoices): Promise<void> {
    this.finished = choices;
    await new Promise((resolve) => this.later(600, () => resolve(undefined)));
  }

  private completeSignIn(id: CliId): void {
    if (this.outcomes.signin === "fail") {
      this.setCli(id, { phase: "signin-failed", signinUrl: undefined });
      return;
    }
    const plan = this.scenario === "claude-free" && id === "claude" ? "free" : "paid";
    this.setCli(id, { phase: "testing", signinUrl: undefined, signedIn: true, account: ACCOUNT, plan });
    this.later(1500, () => this.setCli(id, { phase: "idle" }));
  }

  private load(scenario: ScenarioId): SetupSnapshot {
    let savedStep: StepId | null = null;
    try {
      savedStep = localStorage.getItem(STEP_KEY) as StepId | null;
    } catch {
      /* see reset() */
    }
    return { ...scenarioSnapshot(scenario), savedStep };
  }

  private setCli(id: CliId, change: Partial<CliSnapshot>): void {
    this.set({ ...this.state, cli: { ...this.state.cli, [id]: { ...this.state.cli[id], ...change } } });
  }

  private set(next: SetupSnapshot): void {
    this.state = next;
    for (const listener of this.listeners) listener();
  }

  private later(ms: number, run: () => void): void {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      run();
    }, ms * this.pace);
    this.timers.add(timer);
  }

  private clearTimers(): void {
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
  }
}
