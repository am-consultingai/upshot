import { createContext, useContext, useSyncExternalStore } from "react";
import type { CliFacts, CliId, KeyProviderId, SetupChoices, StepId } from "./flow";

/**
 * Everything the setup screens need from the outside world, and nothing more.
 *
 * The screens never call `api` directly. Setup 0 plugs in a scripted fake so the flow
 * can be walked through without touching Google, installing a CLI or saving a key;
 * Setup 1–9 plug in one backed by `/calendar/*`, `/llm/*` and the settings. Swapping the
 * backend must not change a pixel.
 */

export type CalendarPhase = "idle" | "waiting" | "connected" | "partial" | "cancelled" | "timeout" | "failed";

export type CliPhase =
  /** Nothing running. What the card shows comes from the facts. */
  | "idle"
  /** Installing; sign-in follows on its own, without asking for another click. */
  | "installing"
  | "install-failed"
  /** Waiting on the provider's own sign-in, in the browser tab the backend opened. */
  | "signing-in"
  | "signin-failed"
  /** One tiny prompt, to prove the whole path works before setup says "ready". */
  | "testing";

export interface CliSnapshot extends CliFacts {
  phase: CliPhase;
  account?: string;
  /** The exact line Install runs, shown before it is clicked. */
  installCommand: string;
  /** The provider's sign-in page, shown in the page while sign-in waits. */
  signinUrl?: string;
  /** Claude's sign-in ends with a code the user pastes back; Codex's does not. */
  signinNeedsCode: boolean;
  /**
   * Where that code goes: into this page, or into the window the install or sign-in
   * opened (Claude Code's own login, which reads it there — Setup 4 decides whether it
   * can move into the page).
   */
  codeInWindow?: boolean;
}

export type KeyPhase = "idle" | "checking" | "valid" | "invalid";

/** The computer-audio self-test: a sound played, and whether the loopback heard it. */
export type SpeakerPhase = "idle" | "playing" | "heard" | "silent";

export interface SetupSnapshot {
  calendar: { available: boolean; phase: CalendarPhase; account?: string };
  cli: Record<CliId, CliSnapshot>;
  /**
   * Whether the CLI facts have arrived. Asking the machine what is installed can take
   * seconds on Windows, and the first screen does not wait for it.
   */
  cliKnown: boolean;
  key: { provider: KeyProviderId; phase: KeyPhase };
  /** `level` is what the loopback hears while the test sound plays, 0–1. */
  speakers: { phase: SpeakerPhase; level: number };
  /** The step setup was left on, for resuming after the app closed mid-way. */
  savedStep: StepId | null;
}

export interface SetupBackend {
  snapshot(): SetupSnapshot;
  subscribe(listener: () => void): () => void;

  connectCalendar(): void;
  cancelCalendar(): void;

  /** Install, then go straight on to sign-in: ticking the card is the only click. */
  install(id: CliId): void;
  /** Starts sign-in and opens the provider's page in the browser. */
  signIn(id: CliId): void;
  submitCode(id: CliId, code: string): void;
  cancel(id: CliId): void;

  checkKey(provider: KeyProviderId, key: string): void;
  chooseKeyProvider(provider: KeyProviderId): void;

  /** Play a test sound through the speakers and listen for it on the loopback. */
  testSpeakers(): void;

  /** Keep the language chosen in setup as the app's language. */
  setLanguage(language: "en" | "he"): void;
  saveStep(step: StepId): void;
  finish(choices: SetupChoices): Promise<void>;
}

export const SetupBackendContext = createContext<SetupBackend | null>(null);

export function useSetupBackend(): SetupBackend {
  const backend = useContext(SetupBackendContext);
  if (!backend) throw new Error("setup screens need a SetupBackendContext");
  return backend;
}

/** The backend's current state, re-rendering on every change. */
export function useSetupSnapshot(): SetupSnapshot {
  const backend = useSetupBackend();
  return useSyncExternalStore(backend.subscribe, backend.snapshot);
}
