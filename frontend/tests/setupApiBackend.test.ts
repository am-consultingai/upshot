import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  settings: vi.fn(),
  calendarStatus: vi.fn(),
  diagnostics: vi.fn(),
  llmStatus: vi.fn(),
  llmSignin: vi.fn(),
  llmSigninCode: vi.fn(),
  llmTest: vi.fn(),
  reportSetupFailure: vi.fn(),
}));
vi.mock("../src/api", () => ({ api }));

import { ApiSetupBackend } from "../src/setup/apiBackend";

const SIGNED_OUT = {
  providers: [
    { id: "claude-subscription", ready: true, signed_in: false, console_open: true },
    { id: "antigravity-subscription", ready: true, signed_in: false, console_open: true },
  ],
};

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.settings.mockResolvedValue({ config: {} });
  api.calendarStatus.mockResolvedValue(null);
  api.diagnostics.mockResolvedValue(null);
  api.llmStatus.mockResolvedValue(SIGNED_OUT);
  api.reportSetupFailure.mockResolvedValue({ reported: true });
});

afterEach(() => vi.useRealTimers());

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("ApiSetupBackend: a sign-in that does not finish is reported (machine A, 2026-10-12)", () => {
  it("a refused sign-in fails the card and goes to the crash reports with its stage and reason", async () => {
    api.llmSignin.mockRejectedValue(new Error("500"));
    const backend = await ApiSetupBackend.create();
    backend.signIn("antigravity");
    await flush();
    expect(backend.snapshot().cli.antigravity.phase).toBe("signin-failed");
    expect(api.reportSetupFailure).toHaveBeenCalledWith({
      provider: "antigravity-subscription",
      stage: "signin",
      reason: "refused",
    });
    backend.dispose();
  });

  it("a sign-in that ends signed out says so too", async () => {
    api.llmSignin.mockResolvedValue({ launched: true });
    const backend = await ApiSetupBackend.create();
    backend.signIn("antigravity");
    await flush();
    api.llmStatus.mockResolvedValue({
      providers: [{ id: "antigravity-subscription", ready: true, signed_in: false, console_open: false }],
    });
    await vi.waitFor(() => expect(backend.snapshot().cli.antigravity.phase).toBe("signin-failed"), { timeout: 5000 });
    expect(api.reportSetupFailure).toHaveBeenCalledWith({
      provider: "antigravity-subscription",
      stage: "signin",
      reason: "ended-signed-out",
    });
    backend.dispose();
  });

  it("Antigravity's code goes into its own window; Claude's into the page", async () => {
    const backend = await ApiSetupBackend.create();
    await flush();
    expect(backend.snapshot().cli.antigravity.codeInWindow).toBe(true);
    expect(backend.snapshot().cli.claude.codeInWindow).toBe(false);
    backend.dispose();
  });
});

describe("ApiSetupBackend: a sign-in that worked is never reported as failed (machine B, 2026-10-12)", () => {
  const claude = (signedIn: boolean, consoleOpen: boolean) => ({
    providers: [{ id: "claude-subscription", ready: true, signed_in: signedIn, console_open: consoleOpen }],
  });

  it("a second press while signing in starts nothing", async () => {
    api.llmSignin.mockResolvedValue({ launched: true, url: "https://claude.ai/x" });
    const backend = await ApiSetupBackend.create();
    backend.signIn("claude");
    backend.signIn("claude");
    await flush();
    expect(api.llmSignin).toHaveBeenCalledTimes(1);
    backend.dispose();
  });

  it("one poll with nothing running, as a login is replaced, does not fail the card", async () => {
    vi.useFakeTimers();
    api.llmSignin.mockResolvedValue({ launched: true });
    api.llmTest.mockResolvedValue({ ok: true });
    const backend = await ApiSetupBackend.create();
    backend.signIn("claude");
    await vi.advanceTimersByTimeAsync(0);
    api.llmStatus.mockResolvedValue(claude(false, false));
    await vi.advanceTimersByTimeAsync(1500);
    expect(backend.snapshot().cli.claude.phase).toBe("signing-in");
    api.llmStatus.mockResolvedValue(claude(true, false));
    await vi.advanceTimersByTimeAsync(1500);
    expect(api.llmTest).toHaveBeenCalledWith("claude-subscription");
    expect(backend.snapshot().cli.claude.phase).toBe("idle");
    expect(api.reportSetupFailure).not.toHaveBeenCalled();
    backend.dispose();
  });

  it("a failed card still turns ready when the CLI signs in afterwards", async () => {
    vi.useFakeTimers();
    api.llmSignin.mockRejectedValue(new Error("500"));
    api.llmTest.mockResolvedValue({ ok: true });
    const backend = await ApiSetupBackend.create();
    backend.signIn("claude");
    await vi.advanceTimersByTimeAsync(0);
    expect(backend.snapshot().cli.claude.phase).toBe("signin-failed");
    api.llmStatus.mockResolvedValue(claude(true, false));
    await vi.advanceTimersByTimeAsync(1500);
    expect(backend.snapshot().cli.claude.phase).toBe("idle");
    expect(backend.snapshot().cli.claude.signedIn).toBe(true);
    backend.dispose();
  });
});
