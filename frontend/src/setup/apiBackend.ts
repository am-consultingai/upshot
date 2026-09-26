import { api, type AudioLevel, type CalendarStatus, type LlmProvider } from "../api";
import type { CalendarPhase, CliPhase, CliSnapshot, SetupBackend, SetupSnapshot } from "./backend";
import { CHIME_SECONDS, chime } from "./chime";
import { CLI_PROVIDER, type CliId, type KeyProviderId, type SetupChoices, type StepId } from "./flow";

/**
 * First-run setup on the real application (epic z8tj1hb01k): the same screens the mock
 * was confirmed on, driven by `/calendar/*`, `/llm/*` and the settings.
 *
 * Nothing here is new server behaviour. Install and sign-in are the windows Settings
 * already opens; this chains them, so ticking a card is the only click — a CLI that
 * finishes installing is signed in next, and one that signs in is tested next — by
 * polling the status Settings already reads.
 */

/** How often a running install, sign-in or connection is looked at. */
const POLL_MS = 1500;
/** An install or sign-in nobody finishes is given up on, not watched for ever. */
const GIVE_UP_MS = 10 * 60_000;
/** A loopback peak above this, while the chime plays, is the chime being heard. */
const HEARD_PEAK = 0.02;

/** Google's answers, in the words the calendar step has a line for. */
function calendarPhase(status: CalendarStatus, current: CalendarPhase): CalendarPhase {
  if (status.state === "connected") return "connected";
  if (status.state === "connecting") return "waiting";
  const error = status.error ?? "";
  if (!error) return current === "waiting" ? "cancelled" : current;
  if (error.includes("permission to see calendar events")) return "partial";
  if (error.includes("in time")) return "timeout";
  if (error.includes("not granted")) return "cancelled";
  return "failed";
}

function cliFacts(row: LlmProvider | undefined, id: CliId): Omit<CliSnapshot, "phase"> {
  return {
    installed: !!row?.ready,
    signedIn: row?.signed_in ?? null,
    account: row?.account,
    installCommand: row?.install_command ?? "",
    signinUrl: row?.signin_url || undefined,
    // Claude Code's own login ends on a code, typed into its window; Codex's does not.
    signinNeedsCode: id === "claude",
    codeInWindow: id === "claude",
  };
}

export class ApiSetupBackend implements SetupBackend {
  private state: SetupSnapshot;
  private listeners = new Set<() => void>();
  private timers = new Map<string, ReturnType<typeof setInterval>>();
  private started = new Map<CliId, number>();
  private disposed = false;

  private constructor(state: SetupSnapshot) {
    this.state = state;
  }

  /**
   * Everything the first screen needs to decide which steps there are, read once. What
   * the CLIs are follows on its own: it can take seconds, and nothing before the AI step
   * needs it.
   */
  static async create(): Promise<ApiSetupBackend> {
    const [settings, calendar] = await Promise.all([
      api.settings(),
      api.calendarStatus().catch(() => null),
    ]);
    const config = settings.config as { setup?: { step?: string } };
    const saved = config.setup?.step;
    const backend = new ApiSetupBackend({
      calendar: {
        available: !!calendar?.configured,
        phase: calendar?.state === "connected" ? "connected" : "idle",
        account: calendar?.account ?? undefined,
      },
      cli: {
        claude: { ...cliFacts(undefined, "claude"), phase: "idle" },
        codex: { ...cliFacts(undefined, "codex"), phase: "idle" },
      },
      cliKnown: false,
      key: { provider: "gemini", phase: "idle" },
      speakers: { phase: "idle", level: 0 },
      savedStep: saved ? (saved as StepId) : null,
    });
    backend.learnClis();
    return backend;
  }

  /** What is installed and signed in, once, for the cards to start from. */
  private learnClis(): void {
    const attempt = (left: number): void => {
      api
        .llmStatus()
        .then((status) => {
          const rows = status.providers;
          this.set({
            ...this.state,
            cliKnown: true,
            cli: {
              claude: { ...this.state.cli.claude, ...cliFacts(rows.find((r) => r.id === CLI_PROVIDER.claude), "claude") },
              codex: { ...this.state.cli.codex, ...cliFacts(rows.find((r) => r.id === CLI_PROVIDER.codex), "codex") },
            },
          });
        })
        .catch(() => (left > 0 ? setTimeout(() => attempt(left - 1), POLL_MS) : this.set({ ...this.state, cliKnown: true })));
    };
    attempt(3);
  }

  snapshot = (): SetupSnapshot => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Stop every poll: the screen is gone. */
  dispose(): void {
    this.disposed = true;
    for (const timer of this.timers.values()) clearInterval(timer);
    this.timers.clear();
  }

  // ------------------------------------------------------------------ calendar

  connectCalendar(): void {
    // Opened inside the click, before any await, or the browser blocks it as a popup;
    // pointed at Google's page once the server has made it.
    let tab = window.open("about:blank", "_blank");
    this.setCalendar({ phase: "waiting" });
    void api
      .calendarConnect()
      .then((status) => {
        if (tab) tab.location.href = status.auth_url;
        else tab = window.open(status.auth_url, "_blank");
        let closedFor = 0;
        this.poll("calendar", async () => {
          const next = await api.calendarStatus();
          const phase = calendarPhase(next, this.state.calendar.phase);
          if (phase === "waiting" && tab?.closed) {
            // The user closed Google's tab without finishing. Google will never answer,
            // and the server would wait five minutes for it: stop now and offer Connect
            // again. One poll of grace, for a tab closed the moment it succeeded.
            closedFor += 1;
            if (closedFor >= 2) {
              await api.calendarCancel().catch(() => undefined);
              this.setCalendar({ phase: "idle" });
              return true;
            }
            return false;
          }
          this.setCalendar({ phase, account: next.account ?? undefined });
          return phase !== "waiting";
        });
      })
      .catch(() => {
        tab?.close();
        this.setCalendar({ phase: "failed" });
      });
  }

  cancelCalendar(): void {
    this.stop("calendar");
    void api.calendarCancel().finally(() => this.setCalendar({ phase: "cancelled" }));
  }

  // ------------------------------------------------------------------ AI

  install(id: CliId): void {
    this.setCli(id, { phase: "installing" });
    this.started.set(id, Date.now());
    void api
      .llmInstall(CLI_PROVIDER[id])
      .then((result) => {
        if (!result.launched) {
          this.setCli(id, { phase: "install-failed" });
          return;
        }
        this.watch(id);
      })
      .catch(() => this.setCli(id, { phase: "install-failed" }));
  }

  signIn(id: CliId): void {
    this.setCli(id, { phase: "signing-in" });
    this.started.set(id, Date.now());
    void api
      .llmSignin(CLI_PROVIDER[id])
      .then((result) => {
        if (result.url) {
          this.setCli(id, { signinUrl: result.url });
          window.open(result.url, "_blank");
        }
        this.watch(id);
      })
      .catch(() => this.setCli(id, { phase: "signin-failed" }));
  }

  /** Claude Code reads its code in its own window; there is nothing to send from here. */
  submitCode(): void {}

  cancel(id: CliId): void {
    this.stop(`cli-${id}`);
    if (this.state.cli[id].phase === "signing-in" && id === "codex") {
      void api.llmSigninCancel(CLI_PROVIDER[id]).catch(() => undefined);
    }
    this.setCli(id, { phase: "idle" });
  }

  /**
   * Follow a card from install to ready. Installed while installing means sign-in is
   * next — Claude Code's install window goes on to sign in by itself; Codex's sign-in is
   * started here. Signed in means one tiny test prompt, then ready. A window closed
   * before its job was done ends the wait as a failure, as does ten minutes of nothing.
   */
  private watch(id: CliId): void {
    const provider = CLI_PROVIDER[id];
    this.poll(`cli-${id}`, async () => {
      const status = await api.llmStatus();
      const row = status.providers.find((r) => r.id === provider);
      const facts = cliFacts(row, id);
      const phase = this.state.cli[id].phase;
      const overdue = Date.now() - (this.started.get(id) ?? Date.now()) > GIVE_UP_MS;
      const windowGone = row?.console_open === false;
      this.setCli(id, { ...facts, signinUrl: facts.signinUrl ?? this.state.cli[id].signinUrl });

      if (phase === "installing") {
        if (facts.installed) {
          if (id === "codex") {
            this.signIn(id);
            return true; // signIn watches from here
          }
          this.setCli(id, { phase: "signing-in" });
          return false;
        }
        if (windowGone || overdue) {
          this.setCli(id, { phase: "install-failed" });
          return true;
        }
        return false;
      }
      if (phase === "signing-in") {
        if (facts.signedIn === true) {
          this.test(id);
          return true;
        }
        if (windowGone || overdue) {
          this.setCli(id, { phase: "signin-failed" });
          return true;
        }
      }
      return phase !== "signing-in";
    });
  }

  private test(id: CliId): void {
    this.setCli(id, { phase: "testing" });
    void api
      .llmTest(CLI_PROVIDER[id])
      .then((result) => this.setCli(id, { phase: result.ok ? "idle" : "signin-failed" }))
      .catch(() => this.setCli(id, { phase: "signin-failed" }));
  }

  // ------------------------------------------------------------------ key

  /** Stored, then proved with one tiny prompt; a key that fails is not left behind. */
  checkKey(provider: KeyProviderId, key: string): void {
    if (!key.trim()) return;
    this.set({ ...this.state, key: { provider, phase: "checking" } });
    void api
      .putSecrets({ [provider]: key.trim() })
      .then(() => api.llmTest(provider))
      .then(async (result) => {
        if (!result.ok) await api.putSecrets({ [provider]: "" });
        this.set({ ...this.state, key: { provider, phase: result.ok ? "valid" : "invalid" } });
      })
      .catch(() => this.set({ ...this.state, key: { provider, phase: "invalid" } }));
  }

  chooseKeyProvider(provider: KeyProviderId): void {
    this.set({ ...this.state, key: { provider, phase: "idle" } });
  }

  // ------------------------------------------------------------------ sound check

  /**
   * Listen on the loopback, then play the chime in this page: the loopback hearing it
   * is the proof that meetings' far side will be recorded. Listening starts first
   * because opening the endpoint takes a moment on Windows.
   */
  testSpeakers(): void {
    this.set({ ...this.state, speakers: { phase: "playing", level: 0 } });
    let peak = 0;
    const source = new EventSource("/api/audio/level?track=them");
    source.onmessage = (event) => {
      const reading = JSON.parse(event.data as string) as AudioLevel;
      if (reading.error || reading.done) return;
      peak = Math.max(peak, reading.peak);
      if (this.state.speakers.phase === "playing") {
        this.set({ ...this.state, speakers: { phase: "playing", level: Math.min(1, reading.rms * 4) } });
      }
    };
    setTimeout(() => chime(), 400);
    setTimeout(
      () => {
        source.close();
        if (this.disposed) return;
        this.set({ ...this.state, speakers: { phase: peak > HEARD_PEAK ? "heard" : "silent", level: 0 } });
      },
      400 + CHIME_SECONDS * 1000,
    );
  }

  // ------------------------------------------------------------------ progress

  setLanguage(language: "en" | "he"): void {
    void api.putSettings({ "ui.language": language }).catch(() => undefined);
  }

  saveStep(step: StepId): void {
    if (this.state.savedStep === step) return;
    this.set({ ...this.state, savedStep: step });
    void api.putSettings({ "setup.step": step }).catch(() => undefined);
  }

  async finish({ summarizer, capture }: SetupChoices): Promise<void> {
    await api.putSettings({
      "llm.provider": summarizer.provider,
      "llm.fallback_provider": summarizer.fallback,
      // Answered here, so the library never asks it again (CaptureChoice is gone).
      "detection.mode": capture,
      "detection.decided": true,
      "setup.done": true,
      "setup.step": "",
    });
  }

  // ------------------------------------------------------------------ plumbing

  /** Run `check` every POLL_MS until it answers true. One poll per name at a time. */
  private poll(name: string, check: () => Promise<boolean>): void {
    this.stop(name);
    const timer = setInterval(() => {
      check()
        .then((done) => {
          if (done && this.timers.get(name) === timer) this.stop(name);
        })
        .catch(() => undefined);
    }, POLL_MS);
    this.timers.set(name, timer);
  }

  private stop(name: string): void {
    const timer = this.timers.get(name);
    if (timer !== undefined) clearInterval(timer);
    this.timers.delete(name);
  }

  private setCalendar(change: Partial<SetupSnapshot["calendar"]>): void {
    this.set({ ...this.state, calendar: { ...this.state.calendar, ...change } });
  }

  private setCli(id: CliId, change: Partial<CliSnapshot> & { phase?: CliPhase }): void {
    this.set({ ...this.state, cli: { ...this.state.cli, [id]: { ...this.state.cli[id], ...change } } });
  }

  private set(next: SetupSnapshot): void {
    if (this.disposed) return;
    this.state = next;
    for (const listener of this.listeners) listener();
  }
}
